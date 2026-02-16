#!/bin/bash
set -e

# Configuration
REGION="us-west-2"
DOCKERFILE_DIR="$(dirname "$0")/.."
ENV_FILE="$(dirname "$0")/../.env"

BOOTSTRAP_STACK_NAME="boundary-issues-bootstrap"
BOOTSTRAP_TEMPLATE_FILE="$(dirname "$0")/bootstrap-template.yaml"

WEBHOOK_STACK_NAME="boundary-issues-webhook"
WEBHOOK_TEMPLATE_FILE="$(dirname "$0")/cloudformation-template.yaml"

# Get AWS account ID
echo "Getting AWS account ID..."
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Account ID: $ACCOUNT_ID"

# ============================================================================
# PHASE 1: Deploy Bootstrap Stack (foundational resources)
# ============================================================================
echo ""
echo "========================================="
echo "PHASE 1: Bootstrap Stack Deployment"
echo "========================================="
echo "Bootstrap stack: $BOOTSTRAP_STACK_NAME"

# Check if bootstrap stack exists
if aws cloudformation describe-stacks \
    --stack-name "$BOOTSTRAP_STACK_NAME" \
    --region "$REGION" &>/dev/null; then
    echo "Updating existing bootstrap stack..."
    BOOTSTRAP_OPERATION="update"

    aws cloudformation update-stack \
        --stack-name "$BOOTSTRAP_STACK_NAME" \
        --template-body "file://${BOOTSTRAP_TEMPLATE_FILE}" \
        --region "$REGION" \
        --output text > /dev/null || {
            # Check if the error is "No updates to be performed"
            if aws cloudformation describe-stacks \
                --stack-name "$BOOTSTRAP_STACK_NAME" \
                --region "$REGION" &>/dev/null; then
                echo "  (No changes detected - bootstrap stack is up to date)"
                BOOTSTRAP_OPERATION="none"
            else
                echo "ERROR: Bootstrap stack update failed"
                exit 1
            fi
        }
else
    echo "Creating new bootstrap stack..."
    BOOTSTRAP_OPERATION="create"

    aws cloudformation create-stack \
        --stack-name "$BOOTSTRAP_STACK_NAME" \
        --template-body "file://${BOOTSTRAP_TEMPLATE_FILE}" \
        --region "$REGION" \
        --output text > /dev/null
fi

# Wait for bootstrap stack operation to complete
if [ "$BOOTSTRAP_OPERATION" = "create" ] || [ "$BOOTSTRAP_OPERATION" = "update" ]; then
    echo "Waiting for bootstrap stack operation to complete..."
    if [ "$BOOTSTRAP_OPERATION" = "create" ]; then
        aws cloudformation wait stack-create-complete \
            --stack-name "$BOOTSTRAP_STACK_NAME" \
            --region "$REGION"
    else
        aws cloudformation wait stack-update-complete \
            --stack-name "$BOOTSTRAP_STACK_NAME" \
            --region "$REGION"
    fi
    echo "Bootstrap stack operation completed"
fi

# Get outputs from bootstrap stack
echo "Retrieving bootstrap stack outputs..."
BOOTSTRAP_OUTPUTS=$(aws cloudformation describe-stacks \
    --stack-name "$BOOTSTRAP_STACK_NAME" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs')

BUCKET_NAME=$(echo "$BOOTSTRAP_OUTPUTS" | jq -r '.[] | select(.OutputKey=="DeploymentBucketName") | .OutputValue')
ECR_REPO_URI=$(echo "$BOOTSTRAP_OUTPUTS" | jq -r '.[] | select(.OutputKey=="ProcessorRepositoryUri") | .OutputValue')
ECR_REPO_NAME=$(echo "$BOOTSTRAP_OUTPUTS" | jq -r '.[] | select(.OutputKey=="ProcessorRepositoryName") | .OutputValue')
SECRET_NAME=$(echo "$BOOTSTRAP_OUTPUTS" | jq -r '.[] | select(.OutputKey=="GitHubTokenSecretName") | .OutputValue')

echo "Bootstrap resources:"
echo "  S3 Bucket: $BUCKET_NAME"
echo "  ECR Repository: $ECR_REPO_NAME"
echo "  Secret Name: $SECRET_NAME"

# ============================================================================
# PHASE 2: Update GitHub Token Secret
# ============================================================================
echo ""
echo "========================================="
echo "PHASE 2: Update GitHub Token Secret"
echo "========================================="

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

echo "Updating GitHub token in secret: $SECRET_NAME"
aws secretsmanager put-secret-value \
    --secret-id "$SECRET_NAME" \
    --secret-string "$GITHUB_TOKEN" \
    --region "$REGION" \
    --output text > /dev/null
echo "Secret updated successfully"

# ============================================================================
# PHASE 3: Build and Push Docker Image
# ============================================================================
echo ""
echo "========================================="
echo "PHASE 3: Build and Push Docker Image"
echo "========================================="

# Get ECR login credentials
echo "Logging in to ECR..."
aws ecr get-login-password --region "$REGION" | \
    docker login --username AWS --password-stdin \
    "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# Build Docker image for ARM64
IMAGE_TAG="latest"
echo "Building Docker image..."
echo "  Context: $DOCKERFILE_DIR"
echo "  Platform: linux/arm64"
echo "  Destination: ${ECR_REPO_URI}:${IMAGE_TAG}"
docker build --platform linux/arm64 -t "${ECR_REPO_URI}:${IMAGE_TAG}" "$DOCKERFILE_DIR"

# Push to ECR
echo "Pushing Docker image to ECR..."
docker push "${ECR_REPO_URI}:${IMAGE_TAG}"

# Get image digest for CloudFormation
IMAGE_DIGEST=$(aws ecr describe-images \
    --repository-name "$ECR_REPO_NAME" \
    --image-ids imageTag="$IMAGE_TAG" \
    --region "$REGION" \
    --query 'imageDetails[0].imageDigest' \
    --output text)

PROCESSOR_IMAGE_URI="${ECR_REPO_URI}@${IMAGE_DIGEST}"
echo "Docker image pushed: $PROCESSOR_IMAGE_URI"

# ============================================================================
# PHASE 4: Build and Upload Lambda Package
# ============================================================================
echo ""
echo "========================================="
echo "PHASE 4: Build and Upload Lambda Package"
echo "========================================="

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
S3_KEY="lambda-packages/webhook-index-${TIMESTAMP}.zip"
echo "Building Lambda package..."
make index.zip
echo "Uploading to S3..."
echo "  Source: index.zip"
echo "  Destination: s3://$BUCKET_NAME/$S3_KEY"
aws s3 cp "index.zip" "s3://$BUCKET_NAME/$S3_KEY" --region "$REGION"
echo "Upload complete"

# ============================================================================
# PHASE 5: Deploy Main Application Stack
# ============================================================================
echo ""
echo "========================================="
echo "PHASE 5: Deploy Main Application Stack"
echo "========================================="
echo "Application stack: $WEBHOOK_STACK_NAME"

# Check if CloudFormation stack exists
if aws cloudformation describe-stacks \
    --stack-name "$WEBHOOK_STACK_NAME" \
    --region "$REGION" &>/dev/null; then
    echo "Updating existing CloudFormation stack..."
    OPERATION="update"

    aws cloudformation update-stack \
        --stack-name "$WEBHOOK_STACK_NAME" \
        --template-body "file://${WEBHOOK_TEMPLATE_FILE}" \
        --parameters \
            ParameterKey=BootstrapS3Bucket,ParameterValue="$BUCKET_NAME" \
            ParameterKey=WebhookZipS3Key,ParameterValue="$S3_KEY" \
            ParameterKey=ProcessorImageUri,ParameterValue="$PROCESSOR_IMAGE_URI" \
            ParameterKey=GitHubSecretName,ParameterValue="$SECRET_NAME" \
        --capabilities CAPABILITY_NAMED_IAM \
        --region "$REGION" \
        --output text > /dev/null || {
            # Check if the error is "No updates to be performed"
            if aws cloudformation describe-stacks \
                --stack-name "$WEBHOOK_STACK_NAME" \
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
        --stack-name "$WEBHOOK_STACK_NAME" \
        --template-body "file://${WEBHOOK_TEMPLATE_FILE}" \
        --parameters \
            ParameterKey=BootstrapS3Bucket,ParameterValue="$BUCKET_NAME" \
            ParameterKey=WebhookZipS3Key,ParameterValue="$S3_KEY" \
            ParameterKey=ProcessorImageUri,ParameterValue="$PROCESSOR_IMAGE_URI" \
            ParameterKey=GitHubSecretName,ParameterValue="$SECRET_NAME" \
        --capabilities CAPABILITY_NAMED_IAM \
        --region "$REGION" \
        --output text > /dev/null
fi

# Wait for stack operation to complete (if there was a change)
if [ "$OPERATION" = "create" ] || [ "$OPERATION" = "update" ]; then
    echo "Waiting for stack operation to complete..."
    if [ "$OPERATION" = "create" ]; then
        aws cloudformation wait stack-create-complete \
            --stack-name "$WEBHOOK_STACK_NAME" \
            --region "$REGION"
    else
        aws cloudformation wait stack-update-complete \
            --stack-name "$WEBHOOK_STACK_NAME" \
            --region "$REGION"
    fi
    echo "Stack operation completed successfully"
fi

# Get outputs from stack
echo ""
echo "Retrieving stack outputs..."
STACK_OUTPUTS=$(aws cloudformation describe-stacks \
    --stack-name "$WEBHOOK_STACK_NAME" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs')

FUNCTION_URL=$(echo "$STACK_OUTPUTS" | jq -r '.[] | select(.OutputKey=="WebhookUrl") | .OutputValue')
STATE_MACHINE_ARN=$(echo "$STACK_OUTPUTS" | jq -r '.[] | select(.OutputKey=="StateMachineArn") | .OutputValue')
PROCESSOR_FUNCTION_NAME=$(echo "$STACK_OUTPUTS" | jq -r '.[] | select(.OutputKey=="ProcessorFunctionName") | .OutputValue')
PROCESSOR_LOG_GROUP_NAME=$(echo "$STACK_OUTPUTS" | jq -r '.[] | select(.OutputKey=="ProcessorLogGroupName") | .OutputValue')

echo ""
echo "========================================="
echo "CloudFormation Deployment Complete!"
echo "========================================="
echo "Bootstrap Stack: $BOOTSTRAP_STACK_NAME"
echo "Application Stack: $WEBHOOK_STACK_NAME"
echo "Region: $REGION"
echo ""
echo "Bootstrap Resources:"
echo "  S3 Bucket: $BUCKET_NAME"
echo "  ECR Repository: $ECR_REPO_NAME"
echo "  GitHub Secret: $SECRET_NAME"
echo ""
echo "Webhook Lambda:"
echo "  Function URL: $FUNCTION_URL"
echo ""
echo "Processor Lambda:"
echo "  Function Name: $PROCESSOR_FUNCTION_NAME"
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
echo "========================================"
