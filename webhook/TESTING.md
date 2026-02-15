# Testing the State Machine Integration

This document describes how to test the complete GitHub webhook → Lambda → State Machine → Processor workflow.

## Overview

The system has four main components:
1. **GitHub Actions** - Triggers on PR events, sends payload to webhook
2. **Webhook Lambda** - Receives GitHub events, starts state machine
3. **Step Functions State Machine** - Orchestrates the processor
4. **Processor Lambda** - Clones repo and checks out PR HEAD

## Prerequisites

- AWS CLI configured with appropriate credentials
- Access to CloudFormation stack outputs
- GitHub repository with PR #4 or similar test PR

## Testing Workflow

### 1. Trigger a GitHub Event

The webhook can be triggered two ways:

#### A. Via GitHub Actions (Production)
Push changes to the `migurski/execute-a-state-machine` branch and sync to `migurski/do-not-merge`:

```bash
git push origin migurski/execute-a-state-machine
git checkout migurski/do-not-merge
sleep 1
git rebase -i migurski/execute-a-state-machine
git push -f origin migurski/do-not-merge
git checkout migurski/execute-a-state-machine
```

Wait 15-30 seconds for GitHub Actions to trigger.

#### B. Via Manual Test (Development)
Create a test payload and send it directly:

```bash
# Create test payload
cat > /tmp/pr-test.json << 'EOF'
{"action":"synchronize","number":4,"pull_request":{"diff_url":"https://github.com/migurski/boundary-issues/pull/4.diff","base":{"sha":"db7adabab3c93cf4c05f35c1df2b716596f82faa"},"head":{"sha":"f6400f99d7e2094ccd2034c47f72820cef488a1f"}}}
EOF

# Trigger webhook
./trigger-webhook.sh /tmp/pr-test.json
```

### 2. Check Webhook Lambda Logs

Verify the webhook received the event and started the state machine:

```bash
# Get stack outputs
STACK_NAME="boundary-issues-webhook-stack"
REGION="us-west-2"

# Get log group name
LOG_GROUP=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`LogGroupName`].OutputValue' \
  --output text)

# Check recent logs (adjust timestamp as needed)
aws logs filter-log-events \
  --log-group-name "$LOG_GROUP" \
  --region "$REGION" \
  --start-time 1771182200000 \
  --query 'events[*].message' \
  --output text
```

**Expected output:**
```
Received event: {"version": "2.0", ...}
Parsed payload: {"action": "synchronize", "number": 4, ...}
Starting state machine execution: pr-4-58533593
State machine execution started: arn:aws:states:us-west-2:101696101272:execution:...
```

**Key validations:**
- ✅ Event received with `x-github-event: pull_request`
- ✅ Payload parsed successfully
- ✅ State machine execution started with ARN returned
- ❌ No errors about `context.request_id` (should use `aws_request_id`)

### 3. Check State Machine Executions

Verify the state machine execution status:

```bash
# Get state machine ARN
STATE_MACHINE_ARN=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`StateMachineArn`].OutputValue' \
  --output text)

# List recent executions
aws stepfunctions list-executions \
  --state-machine-arn "$STATE_MACHINE_ARN" \
  --region "$REGION" \
  --max-results 5 \
  --query 'executions[*].[name,status,startDate]' \
  --output table
```

**Expected output:**
```
+---------------+-------------+------------------+
|  pr-4-58533593|  SUCCEEDED  |  1771182257.924  |
+---------------+-------------+------------------+
```

**Key validations:**
- ✅ Status is `SUCCEEDED` (not `FAILED`)
- ✅ Execution name follows pattern `pr-{number}-{request_id}`

### 4. Check Processor Lambda Logs

Verify the processor cloned the repository and checked out the commit:

```bash
# Get processor log group name
PROCESSOR_LOG_GROUP=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`ProcessorLogGroupName`].OutputValue' \
  --output text)

# Check recent logs
aws logs filter-log-events \
  --log-group-name "$PROCESSOR_LOG_GROUP" \
  --region "$REGION" \
  --start-time 1771182200000 \
  --query 'events[*].message' \
  --output text
```

**Expected output:**
```
Received event: {"action": "synchronize", "number": 4, ...}
Fetching secret from: arn:aws:secretsmanager:us-west-2:101696101272:secret:boundary-issues-webhook-stack/github-token
Successfully retrieved GitHub token from Secrets Manager
Processing PR #4, HEAD SHA: a4051323948dd82dbd7bf47b694adf191c29da55, Repo: migurski/boundary-issues
Cloning repository to /tmp/repo
Clone output:
Checking out commit a4051323948dd82dbd7bf47b694adf191c29da55
Fetch output:
Checkout output:
Current HEAD: a4051323948dd82dbd7bf47b694adf191c29da55
Successfully checked out PR #4 at a4051323948dd82dbd7bf47b694adf191c29da55
```

**Key validations:**
- ✅ GitHub token retrieved from Secrets Manager
- ✅ Repository extracted from `diff_url` (not hardcoded)
- ✅ Repository cloned successfully (no "Repository not found" error)
- ✅ Commit checked out matches PR HEAD SHA
- ✅ Verification confirms `git rev-parse HEAD` matches expected SHA
- ❌ No runtime entrypoint errors (Docker image must use proper awslambdaric configuration)

## Common Issues and Solutions

### Issue: "LambdaContext object has no attribute 'request_id'"
**Solution:** Use `context.aws_request_id` instead of `context.request_id` in index.py

### Issue: "Runtime.InvalidEntrypoint"
**Solution:** Ensure Dockerfile has proper ENTRYPOINT and CMD:
```dockerfile
ENTRYPOINT ["python3", "-m", "awslambdaric"]
CMD ["processor.handler"]
```

### Issue: "Repository not found"
**Solution:** Extract repository from PR `diff_url` instead of hardcoding. Check processor.py extracts `{owner}/{repo}` from the diff_url pattern.

### Issue: State machine execution FAILED
**Solution:** Check processor Lambda logs for the specific error. Common causes:
- GitHub token not in Secrets Manager
- Invalid repository URL
- Git clone timeout (increase Lambda timeout)

## Security Validations

### GitHub Token Security
1. ✅ Token never appears in git history
2. ✅ Token stored in Secrets Manager (encrypted at rest)
3. ✅ Token retrieved at runtime only
4. ✅ Token not logged in CloudWatch (boto3 redacts secret values)

Verify token is NOT in logs:
```bash
aws logs filter-log-events \
  --log-group-name "$PROCESSOR_LOG_GROUP" \
  --region "$REGION" \
  --start-time 1771182200000 \
  --filter-pattern "ghp_" \
  --query 'events[*].message' \
  --output text
```

Should return: No events found

### Resource Uniqueness
All resources use `${AWS::StackName}` prefix:
```bash
aws cloudformation describe-stack-resources \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query 'StackResources[*].[LogicalResourceId,PhysicalResourceId]' \
  --output table
```

Verify all resources include stack name in their physical ID.

## Performance Benchmarks

From successful test runs:

- **Webhook Lambda**: ~330ms execution time, 90MB memory used
- **Processor Lambda**: ~1700-3300ms execution time, 130MB memory used
  - Init duration: ~3700-8500ms (cold start)
  - Includes: git clone, fetch, checkout, verification
- **State Machine**: Total ~5-10 seconds end-to-end
- **GitHub Actions → Complete**: ~30-60 seconds

## Test Checklist

Before pushing to production:

- [ ] Webhook receives GitHub events
- [ ] State machine execution starts successfully
- [ ] Processor Lambda retrieves GitHub token
- [ ] Repository clones without errors
- [ ] PR HEAD commit checked out correctly
- [ ] State machine execution status is SUCCEEDED
- [ ] No GitHub token leakage in logs
- [ ] All resources have unique stack-scoped names
- [ ] Unit tests pass (`python -m unittest webhook/index.py`)
