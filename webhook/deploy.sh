#!/bin/bash
set -e

# Configuration
FUNCTION_NAME="boundary-issues-webhook"
ROLE_NAME="boundary-issues-webhook-role"
REGION="us-west-2"
RUNTIME="python3.12"
HANDLER="lambda_function.lambda_handler"

echo "Starting deployment of $FUNCTION_NAME to $REGION..."

# Check if IAM role exists
echo "Checking IAM role..."
if aws iam get-role --role-name $ROLE_NAME 2>/dev/null; then
    echo "IAM role $ROLE_NAME already exists"
    ROLE_ARN=$(aws iam get-role --role-name $ROLE_NAME --query 'Role.Arn' --output text)
else
    echo "Creating IAM role $ROLE_NAME..."

    # Create trust policy
    cat > /tmp/trust-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

    # Create role
    ROLE_ARN=$(aws iam create-role \
        --role-name $ROLE_NAME \
        --assume-role-policy-document file:///tmp/trust-policy.json \
        --query 'Role.Arn' \
        --output text)

    echo "Created role: $ROLE_ARN"

    # Attach basic execution policy
    aws iam attach-role-policy \
        --role-name $ROLE_NAME \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

    echo "Attached AWSLambdaBasicExecutionRole policy"

    # Wait for role to be ready
    echo "Waiting for role to propagate..."
    sleep 10

    rm /tmp/trust-policy.json
fi

# Create deployment package
echo "Creating deployment package..."
cd "$(dirname "$0")"
zip -q lambda.zip lambda_function.py
echo "Created lambda.zip"

# Check if Lambda function exists
echo "Checking Lambda function..."
if aws lambda get-function --function-name $FUNCTION_NAME --region $REGION 2>/dev/null; then
    echo "Updating existing function code..."
    aws lambda update-function-code \
        --function-name $FUNCTION_NAME \
        --zip-file fileb://lambda.zip \
        --region $REGION \
        --output text > /dev/null
    echo "Function code updated"
else
    echo "Creating new Lambda function..."
    aws lambda create-function \
        --function-name $FUNCTION_NAME \
        --runtime $RUNTIME \
        --role $ROLE_ARN \
        --handler $HANDLER \
        --zip-file fileb://lambda.zip \
        --region $REGION \
        --timeout 30 \
        --memory-size 128 \
        --output text > /dev/null
    echo "Function created"
fi

# Check if function URL exists
echo "Checking function URL..."
if aws lambda get-function-url-config --function-name $FUNCTION_NAME --region $REGION 2>/dev/null; then
    echo "Function URL already exists"
else
    echo "Creating function URL..."
    aws lambda create-function-url-config \
        --function-name $FUNCTION_NAME \
        --auth-type NONE \
        --cors '{"AllowOrigins": ["*"], "AllowMethods": ["*"], "AllowHeaders": ["*"]}' \
        --region $REGION \
        --output text > /dev/null
    echo "Function URL created"
fi

# Add permissions for public access (always run, regardless of whether URL is new)
echo "Adding public access permissions..."

# Add InvokeFunctionUrl permission
aws lambda add-permission \
    --function-name $FUNCTION_NAME \
    --statement-id FunctionURLAllowPublicAccess \
    --action lambda:InvokeFunctionUrl \
    --principal "*" \
    --function-url-auth-type NONE \
    --region $REGION \
    --output text > /dev/null 2>&1 || echo "  (InvokeFunctionUrl permission may already exist)"

# Add InvokeFunction permission (also required for public access)
aws lambda add-permission \
    --function-name $FUNCTION_NAME \
    --statement-id FunctionURLAllowPublicAccess2 \
    --action lambda:InvokeFunction \
    --principal "*" \
    --region $REGION \
    --output text > /dev/null 2>&1 || echo "  (InvokeFunction permission may already exist)"

# Get function URL
FUNCTION_URL=$(aws lambda get-function-url-config \
    --function-name $FUNCTION_NAME \
    --region $REGION \
    --query 'FunctionUrl' \
    --output text)

# Clean up
rm lambda.zip
echo "Cleaned up deployment package"

echo ""
echo "========================================="
echo "Deployment complete!"
echo "========================================="
echo "Function Name: $FUNCTION_NAME"
echo "Region: $REGION"
echo "Function URL: $FUNCTION_URL"
echo ""
echo "To configure GitHub webhook, run:"
echo "  python3 setup_github_webhook.py $FUNCTION_URL"
echo ""
echo "To view logs, run:"
echo "  aws logs tail /aws/lambda/$FUNCTION_NAME --follow --region $REGION"
echo "========================================="
