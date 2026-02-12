#!/bin/bash
set -e

# Configuration
ZIP_FILE="$1"
STACK_NAME="boundary-issues-webhook-stack"
FUNCTION_NAME="boundary-issues-webhook"
REGION="us-west-2"
TEMPLATE_FILE="$(dirname "$0")/cloudformation-template.yaml"

# Validate ZIP file argument
if [ -z "$ZIP_FILE" ]; then
    echo "Error: ZIP file path required"
    echo "Usage: $0 <path-to-zip-file>"
    exit 1
fi

if [ ! -f "$ZIP_FILE" ]; then
    echo "Error: ZIP file not found: $ZIP_FILE"
    exit 1
fi

echo "Starting CloudFormation deployment of $FUNCTION_NAME to $REGION..."
echo ""

# Get AWS account ID for bucket naming
echo "Getting AWS account ID..."
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET_NAME="boundary-issues-lambda-deployments-${ACCOUNT_ID}"
echo "Using S3 bucket: $BUCKET_NAME"

# Check if S3 bucket exists, create if not
echo "Checking S3 bucket..."
if aws s3api head-bucket --bucket "$BUCKET_NAME" --region "$REGION" 2>/dev/null; then
    echo "S3 bucket $BUCKET_NAME already exists"
else
    echo "Creating S3 bucket $BUCKET_NAME..."
    aws s3api create-bucket \
        --bucket "$BUCKET_NAME" \
        --region "$REGION" \
        --create-bucket-configuration LocationConstraint="$REGION"

    # Enable versioning for better tracking
    aws s3api put-bucket-versioning \
        --bucket "$BUCKET_NAME" \
        --versioning-configuration Status=Enabled \
        --region "$REGION"

    echo "S3 bucket created with versioning enabled"
fi

# Upload ZIP file to S3 with timestamp-based key
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
S3_KEY="lambda-packages/${FUNCTION_NAME}-${TIMESTAMP}.zip"
echo ""
echo "Uploading Lambda package to S3..."
echo "  Source: $ZIP_FILE"
echo "  Destination: s3://$BUCKET_NAME/$S3_KEY"
aws s3 cp "$ZIP_FILE" "s3://$BUCKET_NAME/$S3_KEY" --region "$REGION"
echo "Upload complete"

# Check if CloudFormation stack exists
echo ""
echo "Checking CloudFormation stack..."
if aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" &>/dev/null; then
    echo "Updating existing CloudFormation stack..."
    OPERATION="update"

    aws cloudformation update-stack \
        --stack-name "$STACK_NAME" \
        --template-body "file://${TEMPLATE_FILE}" \
        --parameters \
            ParameterKey=S3Bucket,ParameterValue="$BUCKET_NAME" \
            ParameterKey=S3Key,ParameterValue="$S3_KEY" \
            ParameterKey=FunctionName,ParameterValue="$FUNCTION_NAME" \
        --capabilities CAPABILITY_NAMED_IAM \
        --region "$REGION" \
        --output text > /dev/null || {
            # Check if the error is "No updates to be performed"
            if aws cloudformation describe-stacks \
                --stack-name "$STACK_NAME" \
                --region "$REGION" &>/dev/null; then
                echo "  (No changes detected - stack is already up to date)"
                OPERATION="none"
            else
                echo "ERROR: Stack update failed"
                exit 1
            fi
        }
else
    echo "Creating new CloudFormation stack..."
    OPERATION="create"

    aws cloudformation create-stack \
        --stack-name "$STACK_NAME" \
        --template-body "file://${TEMPLATE_FILE}" \
        --parameters \
            ParameterKey=S3Bucket,ParameterValue="$BUCKET_NAME" \
            ParameterKey=S3Key,ParameterValue="$S3_KEY" \
            ParameterKey=FunctionName,ParameterValue="$FUNCTION_NAME" \
        --capabilities CAPABILITY_NAMED_IAM \
        --region "$REGION" \
        --output text > /dev/null
fi

# Wait for stack operation to complete (if there was a change)
if [ "$OPERATION" = "create" ] || [ "$OPERATION" = "update" ]; then
    echo "Waiting for stack operation to complete..."
    if [ "$OPERATION" = "create" ]; then
        aws cloudformation wait stack-create-complete \
            --stack-name "$STACK_NAME" \
            --region "$REGION"
    else
        aws cloudformation wait stack-update-complete \
            --stack-name "$STACK_NAME" \
            --region "$REGION"
    fi
    echo "Stack operation completed successfully"
fi

# Get outputs from stack
echo ""
echo "Retrieving stack outputs..."
STACK_OUTPUTS=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs')

FUNCTION_URL=$(echo "$STACK_OUTPUTS" | jq -r '.[] | select(.OutputKey=="WebhookUrl") | .OutputValue')
ACTUAL_FUNCTION_NAME=$(echo "$STACK_OUTPUTS" | jq -r '.[] | select(.OutputKey=="FunctionName") | .OutputValue')
LOG_GROUP_NAME=$(echo "$STACK_OUTPUTS" | jq -r '.[] | select(.OutputKey=="LogGroupName") | .OutputValue')

echo ""
echo "========================================="
echo "CloudFormation Deployment Complete!"
echo "========================================="
echo "Stack Name: $STACK_NAME"
echo "Function Name: $ACTUAL_FUNCTION_NAME"
echo "Region: $REGION"
echo "Function URL: $FUNCTION_URL"
echo "Log Group: $LOG_GROUP_NAME"
echo ""
echo "Next steps:"
echo "1. Update trigger-webhook.sh with the Function URL above"
echo "   Edit line 5 of webhook/trigger-webhook.sh"
echo ""
echo "2. To configure GitHub webhook, run:"
echo "   python3 setup_github_webhook.py $FUNCTION_URL"
echo ""
echo "3. To view logs, run:"
echo "   aws logs tail $LOG_GROUP_NAME --follow --region $REGION"
echo ""
echo "4. To retrieve Function URL later, run:"
echo "   aws cloudformation describe-stacks \\"
echo "     --stack-name $STACK_NAME \\"
echo "     --query 'Stacks[0].Outputs[?OutputKey==\\\`WebhookUrl\\\`].OutputValue' \\"
echo "     --output text \\"
echo "     --region $REGION"
echo "========================================="
