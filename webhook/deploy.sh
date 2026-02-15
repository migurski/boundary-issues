#!/bin/bash
set -e

# Configuration
ZIP_FILE="$1"
STACK_NAME="boundary-issues-webhook-stack"
FUNCTION_NAME="boundary-issues-webhook"
REGION="us-west-2"
TEMPLATE_FILE="$(dirname "$0")/cloudformation-template.yaml"
DOCKERFILE_DIR="$(dirname "$0")"
ENV_FILE="$(dirname "$0")/../.env"

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

# Setup GitHub token in Secrets Manager
echo ""
echo "Setting up GitHub token in Secrets Manager..."
SECRET_NAME="${STACK_NAME}/github-token"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: .env file not found at $ENV_FILE"
    exit 1
fi

# Read GitHub token from .env file
GITHUB_TOKEN=$(grep '^GITHUB_TOKEN=' "$ENV_FILE" | cut -d= -f2)
if [ -z "$GITHUB_TOKEN" ]; then
    echo "ERROR: GITHUB_TOKEN not found in $ENV_FILE"
    exit 1
fi

# Check if secret exists
if aws secretsmanager describe-secret \
    --secret-id "$SECRET_NAME" \
    --region "$REGION" &>/dev/null; then
    echo "Secret $SECRET_NAME already exists - skipping creation"
else
    echo "Creating secret $SECRET_NAME..."
    aws secretsmanager create-secret \
        --name "$SECRET_NAME" \
        --description "GitHub token for ${STACK_NAME}" \
        --secret-string "$GITHUB_TOKEN" \
        --region "$REGION" \
        --output text > /dev/null
    echo "Secret created successfully"
fi

# Build and push Docker image to ECR
echo ""
echo "Building and pushing Docker image to ECR..."

# ECR repository will be managed by CloudFormation
# We need to build and push to a temporary tag first
ECR_REPO_NAME="${STACK_NAME}-processor-image"

# Check if repository exists (created by CloudFormation)
if ! aws ecr describe-repositories \
    --repository-names "$ECR_REPO_NAME" \
    --region "$REGION" &>/dev/null; then
    echo "NOTE: ECR repository $ECR_REPO_NAME does not exist yet."
    echo "      It will be created by CloudFormation on first deployment."
    echo "      For first deployment, we'll create a temporary repository."

    # Create temporary repository for initial deployment
    TEMP_REPO_NAME="${ECR_REPO_NAME}-temp"
    if ! aws ecr describe-repositories \
        --repository-names "$TEMP_REPO_NAME" \
        --region "$REGION" &>/dev/null; then
        echo "Creating temporary ECR repository $TEMP_REPO_NAME..."
        aws ecr create-repository \
            --repository-name "$TEMP_REPO_NAME" \
            --image-scanning-configuration scanOnPush=true \
            --region "$REGION" \
            --output text > /dev/null
    fi
    ECR_REPO_NAME="$TEMP_REPO_NAME"
fi

# Get ECR login credentials
echo "Logging in to ECR..."
aws ecr get-login-password --region "$REGION" | \
    docker login --username AWS --password-stdin \
    "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# Build Docker image for ARM64
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO_NAME}"
IMAGE_TAG="latest"
echo "Building Docker image..."
echo "  Context: $DOCKERFILE_DIR"
echo "  Platform: linux/arm64"
docker build --platform linux/arm64 -t "${ECR_URI}:${IMAGE_TAG}" "$DOCKERFILE_DIR"

# Push to ECR
echo "Pushing Docker image to ECR..."
docker push "${ECR_URI}:${IMAGE_TAG}"

# Get image digest for CloudFormation
IMAGE_DIGEST=$(aws ecr describe-images \
    --repository-name "$ECR_REPO_NAME" \
    --image-ids imageTag="$IMAGE_TAG" \
    --region "$REGION" \
    --query 'imageDetails[0].imageDigest' \
    --output text)

PROCESSOR_IMAGE_URI="${ECR_URI}@${IMAGE_DIGEST}"
echo "Docker image pushed: $PROCESSOR_IMAGE_URI"

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
STATE_MACHINE_ARN=$(echo "$STACK_OUTPUTS" | jq -r '.[] | select(.OutputKey=="StateMachineArn") | .OutputValue')
PROCESSOR_FUNCTION_NAME=$(echo "$STACK_OUTPUTS" | jq -r '.[] | select(.OutputKey=="ProcessorFunctionName") | .OutputValue')
PROCESSOR_LOG_GROUP_NAME=$(echo "$STACK_OUTPUTS" | jq -r '.[] | select(.OutputKey=="ProcessorLogGroupName") | .OutputValue')

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
echo "  Function Name: $PROCESSOR_FUNCTION_NAME"
echo "  Log Group: $PROCESSOR_LOG_GROUP_NAME"
echo ""
echo "State Machine:"
echo "  ARN: $STATE_MACHINE_ARN"
echo ""
echo "Next steps:"
echo "1. Update trigger-webhook.sh with the Function URL above"
echo "   Edit line 5 of webhook/trigger-webhook.sh"
echo ""
echo "2. To view webhook logs, run:"
echo "   aws logs tail $LOG_GROUP_NAME --follow --region $REGION"
echo ""
echo "3. To view processor logs, run:"
echo "   aws logs tail $PROCESSOR_LOG_GROUP_NAME --follow --region $REGION"
echo ""
echo "4. To view state machine executions, run:"
echo "   aws stepfunctions list-executions --state-machine-arn \"$STATE_MACHINE_ARN\" --region $REGION"
echo ""
echo "5. To retrieve Function URL later, run:"
echo "   aws cloudformation describe-stacks \\"
echo "     --stack-name $STACK_NAME \\"
echo "     --query 'Stacks[0].Outputs[?OutputKey==\\\`WebhookUrl\\\`].OutputValue' \\"
echo "     --output text \\"
echo "     --region $REGION"
echo "========================================"
