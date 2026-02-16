# Testing the State Machine Integration

This document describes how to test the complete GitHub webhook → Lambda → State Machine → Processor workflow.

## Overview

The system has five main components working asynchronously:
1. **GitHub Actions** - Triggers on PR events, sends payload to webhook
2. **Webhook Lambda** (webhook.py) - Receives GitHub events, starts state machine, returns immediately
3. **Step Functions State Machine** - Waits for task token callback
4. **Task Lambda** (task.py) - Invokes processor asynchronously with task token, returns immediately
5. **Processor Lambda** (processor.py) - Clones repo, checks out PR HEAD, sends task success/failure callback to state machine

**Execution Flow:**
```
GitHub → webhook.py → start state machine → return immediately
         [async] state machine → task.py + task token → invoke processor async → return immediately
         [async] processor → does work → sendTaskSuccess/Failure → state machine completes
```

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
git rebase --onto migurski/execute-a-state-machine HEAD~1 # just one commit
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

### 3. Check Task Lambda Logs

Verify the task function invoked the processor asynchronously:

```bash
# Get task function log stream
TASK_LOG_STREAM=$(aws logs describe-log-streams \
  --log-group-name /aws/lambda/boundary-issues-webhook-task-function \
  --order-by LastEventTime \
  --descending \
  --max-items 1 \
  --region "$REGION" \
  --query 'logStreams[0].logStreamName' \
  --output text)

# Check task function logs
aws logs get-log-events \
  --log-group-name /aws/lambda/boundary-issues-webhook-task-function \
  --log-stream-name "$TASK_LOG_STREAM" \
  --region "$REGION" \
  --query 'events[*].message' \
  --output text
```

**Expected output:**
```
Received event: {"taskToken": "...", "action": "synchronize", "number": 4, ...}
Invoking processor function asynchronously: arn:aws:lambda:us-west-2:101696101272:function:boundary-issues-webhook-processor-function
Payload: {"taskToken": "...", "action": "synchronize", ...}
Processor invoked successfully, StatusCode: 202
```

**Key validations:**
- ✅ Task token received in event
- ✅ Processor invoked with InvocationType: Event (async)
- ✅ StatusCode 202 (async invocation accepted)
- ✅ Function returns immediately

### 4. Check State Machine Executions

Verify the state machine execution is waiting for task token callback:

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

**Expected output (while processor is running):**
```
+---------------+-------------+------------------+
|  pr-4-58533593|  RUNNING    |  1771182257.924  |
+---------------+-------------+------------------+
```

**Expected output (after processor completes):**
```
+---------------+-------------+------------------+
|  pr-4-58533593|  SUCCEEDED  |  1771182257.924  |
+---------------+-------------+------------------+
```

**Key validations:**
- ✅ Status is `RUNNING` (waiting for task token callback from processor)
- ✅ Status becomes `SUCCEEDED` or `FAILED` after processor sends callback
- ✅ Execution name follows pattern `pr-{number}-{request_id}`
- ✅ State machine uses waitForTaskToken pattern

**To view execution history:**
```bash
aws stepfunctions get-execution-history \
  --execution-arn "arn:aws:states:us-west-2:101696101272:execution:boundary-issues-webhook-processor:pr-4-58533593" \
  --region "$REGION" \
  --query 'events[*].[timestamp,type]' \
  --output table
```

Expected events:
- ExecutionStarted
- TaskStateEntered
- TaskScheduled (with waitForTaskToken)
- TaskStarted
- TaskSubmitted (processor invoked)
- TaskSucceeded/TaskFailed (callback from processor)
- ExecutionSucceeded/ExecutionFailed

### 5. Check Processor Lambda Logs

Verify the processor received the task token and sends callbacks to Step Functions:

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
Received event: {"taskToken": "...", "action": "synchronize", "number": 4, ...}
Task token found, will send callback to Step Functions
Fetching secret from: arn:aws:secretsmanager:us-west-2:101696101272:secret:boundary-issues-bootstrap-webhook/github-token
Successfully retrieved GitHub token from Secrets Manager
Processing PR #4, HEAD SHA: a4051323948dd82dbd7bf47b694adf191c29da55, URL: https://github.com/migurski/boundary-issues.git
Cloning repository to /tmp/repo
Clone output:
Checking out commit a4051323948dd82dbd7bf47b694adf191c29da55
Fetch output:
Checkout output:
Current HEAD: a4051323948dd82dbd7bf47b694adf191c29da55
Successfully checked out PR #4 at a4051323948dd82dbd7bf47b694adf191c29da55
Run build-country-polygon.py
Successfully ran build-country-polygon.py
```

**Key validations:**
- ✅ Task token received in event
- ✅ Step Functions client initialized for callbacks
- ✅ GitHub token retrieved from Secrets Manager
- ✅ Repository cloned successfully (no "Repository not found" error)
- ✅ Commit checked out matches PR HEAD SHA
- ✅ Verification confirms `git rev-parse HEAD` matches expected SHA
- ✅ On success: calls sfn.send_task_success() with task token
- ✅ On error: calls sfn.send_task_failure() with task token and error details
- ❌ No runtime entrypoint errors (Docker image must use proper awslambdaric configuration)

## Common Issues and Solutions

### Issue: "LambdaContext object has no attribute 'request_id'"
**Solution:** Use `context.aws_request_id` instead of `context.request_id` in webhook.py

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
- Processor failed to send task callback (check IAM permissions for states:SendTaskSuccess/SendTaskFailure)

### Issue: State machine stuck in RUNNING state
**Solution:** This means the processor never sent a task callback. Check:
1. Processor Lambda logs for errors or timeouts
2. ProcessorFunctionRole has states:SendTaskSuccess and states:SendTaskFailure permissions
3. Task token was passed correctly from state machine → task.py → processor.py
4. Processor code calls sfn.send_task_success() or sfn.send_task_failure() with the task token

### Issue: Task function fails to invoke processor
**Solution:** Check:
1. TaskFunctionRole has lambda:InvokeFunction permission for ProcessorFunction
2. PROCESSOR_FUNCTION_ARN environment variable is set correctly
3. Task function logs show StatusCode 202 (async invocation accepted)

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
- [ ] Unit tests pass for webhook (`python -m unittest webhook/webhook.py`)
- [ ] Unit tests pass for task handler (`python -m unittest webhook/task.py`)
