#!/bin/bash
set -e

# Lambda function configuration
FUNCTION_NAME="boundary-issues-webhook"
REGION="us-west-2"

# Get the Lambda function URL
echo "Retrieving Lambda function URL..." >&2
LAMBDA_URL=$(aws lambda get-function-url-config \
    --function-name "$FUNCTION_NAME" \
    --region "$REGION" \
    --query 'FunctionUrl' \
    --output text 2>/dev/null)

if [ -z "$LAMBDA_URL" ]; then
    echo "ERROR: Could not retrieve Lambda function URL for $FUNCTION_NAME in $REGION" >&2
    exit 1
fi

echo "Lambda URL: $LAMBDA_URL" >&2

# Read payload from stdin or file
if [ -n "$1" ]; then
    PAYLOAD_FILE="$1"
else
    # Read from stdin
    PAYLOAD_FILE="/tmp/webhook-payload-$$.json"
    cat > "$PAYLOAD_FILE"
    CLEANUP_PAYLOAD=true
fi

echo "Sending push event to Lambda function..." >&2

# Send to Lambda with GitHub headers
RESPONSE=$(curl -X POST "$LAMBDA_URL" \
    -H "Content-Type: application/json" \
    -H "X-GitHub-Event: ${GITHUB_EVENT:-push}" \
    -H "X-GitHub-Delivery: ${GITHUB_DELIVERY:-$(date +%s)}" \
    -d @"$PAYLOAD_FILE" \
    --silent \
    --show-error \
    --write-out "\nHTTP_STATUS:%{http_code}")

# Clean up temporary file if created
if [ "$CLEANUP_PAYLOAD" = "true" ]; then
    rm -f "$PAYLOAD_FILE"
fi

# Extract HTTP status
HTTP_STATUS=$(echo "$RESPONSE" | grep "HTTP_STATUS:" | cut -d: -f2)
BODY=$(echo "$RESPONSE" | grep -v "HTTP_STATUS:")

echo "HTTP Status: $HTTP_STATUS" >&2

if [ "$HTTP_STATUS" -ge 200 ] && [ "$HTTP_STATUS" -lt 300 ]; then
    echo "Successfully triggered Lambda function" >&2
    echo "$BODY"
    exit 0
else
    echo "ERROR: Lambda function returned status $HTTP_STATUS" >&2
    echo "$BODY" >&2
    exit 1
fi
