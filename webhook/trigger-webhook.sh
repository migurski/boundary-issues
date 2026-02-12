#!/bin/bash
set -e

# Lambda function URL - update this when redeploying
LAMBDA_URL="https://zxtp2qqjaqe4oa2nfuouqsczey0nsuvu.lambda-url.us-west-2.on.aws/"

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
