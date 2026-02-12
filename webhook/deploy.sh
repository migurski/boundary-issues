#!/bin/bash
set -e

# Configuration
ZIP_FILE="$1"
STACK_NAME="boundary-issues-webhook-stack"
FUNCTION_NAME="boundary-issues-webhook"
PROCESSOR_FUNCTION_NAME="boundary-processor"
REGION="us-west-2"
TEMPLATE_FILE="$(dirname "$0")/cloudformation-template.yaml"
DOCKERFILE_PATH="$(dirname "$0")/../Dockerfile"

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

# Build and push Docker image for processor Lambda
echo ""
echo "Building and pushing Docker image for processor Lambda..."

# Check if ECR repository exists, get URI
ECR_REPO_NAME="${STACK_NAME}-${PROCESSOR_FUNCTION_NAME}"
echo "Checking ECR repository: $ECR_REPO_NAME"
ECR_REPO_URI=$(aws ecr describe-repositories \
    --repository-names "$ECR_REPO_NAME" \
    --region "$REGION" \
    --query 'repositories[0].repositoryUri' \
    --output text 2>/dev/null || echo "")

if [ -z "$ECR_REPO_URI" ]; then
    echo "Creating ECR repository: $ECR_REPO_NAME"
    ECR_REPO_URI=$(aws ecr create-repository \
        --repository-name "$ECR_REPO_NAME" \
        --region "$REGION" \
        --image-scanning-configuration scanOnPush=true \
        --query 'repository.repositoryUri' \
        --output text)
    echo "ECR repository created: $ECR_REPO_URI"
else
    echo "ECR repository exists: $ECR_REPO_URI"
fi

# Login to ECR
echo "Logging in to ECR..."
aws ecr get-login-password --region "$REGION" | \
    docker login --username AWS --password-stdin "${ECR_REPO_URI%%/*}"

# Build Docker image
IMAGE_TAG="${TIMESTAMP}"
echo "Building Docker image..."
echo "  Dockerfile: $DOCKERFILE_PATH"
echo "  Tag: $ECR_REPO_URI:$IMAGE_TAG"
docker build --platform linux/arm64 -t "$ECR_REPO_URI:$IMAGE_TAG" -f "$DOCKERFILE_PATH" "$(dirname "$DOCKERFILE_PATH")"

# Also tag as latest
docker tag "$ECR_REPO_URI:$IMAGE_TAG" "$ECR_REPO_URI:latest"

# Push to ECR
echo "Pushing Docker image to ECR..."
docker push "$ECR_REPO_URI:$IMAGE_TAG"
docker push "$ECR_REPO_URI:latest"
echo "Docker image pushed successfully"

PROCESSOR_IMAGE_URI="$ECR_REPO_URI:$IMAGE_TAG"

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
            ParameterKey=ProcessorFunctionName,ParameterValue="$PROCESSOR_FUNCTION_NAME" \
            ParameterKey=ProcessorImageUri,ParameterValue="$PROCESSOR_IMAGE_URI" \
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
            ParameterKey=ProcessorFunctionName,ParameterValue="$PROCESSOR_FUNCTION_NAME" \
            ParameterKey=ProcessorImageUri,ParameterValue="$PROCESSOR_IMAGE_URI" \
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
PROCESSOR_FUNCTION_NAME_OUTPUT=$(echo "$STACK_OUTPUTS" | jq -r '.[] | select(.OutputKey=="ProcessorFunctionName") | .OutputValue')
STATE_MACHINE_ARN=$(echo "$STACK_OUTPUTS" | jq -r '.[] | select(.OutputKey=="StateMachineArn") | .OutputValue')
DATA_BUCKET_NAME=$(echo "$STACK_OUTPUTS" | jq -r '.[] | select(.OutputKey=="DataBucketName") | .OutputValue')

echo ""
echo "========================================="
echo "CloudFormation Deployment Complete!"
echo "========================================="
echo "Stack Name: $STACK_NAME"
echo "Region: $REGION"
echo ""
echo "Webhook Lambda:"
echo "  Function Name: $ACTUAL_FUNCTION_NAME"
echo "  Function URL: $FUNCTION_URL"
echo "  Log Group: $LOG_GROUP_NAME"
echo ""
echo "Processor Lambda:"
echo "  Function Name: $PROCESSOR_FUNCTION_NAME_OUTPUT"
echo "  Docker Image: $PROCESSOR_IMAGE_URI"
echo "  Log Group: /aws/lambda/$PROCESSOR_FUNCTION_NAME_OUTPUT"
echo ""
echo "Step Functions:"
echo "  State Machine ARN: $STATE_MACHINE_ARN"
echo ""
echo "Storage:"
echo "  ECR Repository: $ECR_REPO_URI"
echo "  Data Bucket: $DATA_BUCKET_NAME"
echo ""
echo "Next steps:"
echo "1. Update trigger-webhook.sh with the Function URL above"
echo "   Edit line 5 of webhook/trigger-webhook.sh"
echo ""
echo "4. To view processor logs:"
echo "   aws logs tail /aws/lambda/$PROCESSOR_FUNCTION_NAME_OUTPUT --follow --region $REGION"
echo "========================================="
