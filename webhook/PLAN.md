# State Machine Restructuring Plan

## User Requirements

**Initial request:**
> We are going to restructure our state machine somewhat: we want to re-use index.py so that it can accept different JSON payloads, and be used in the place of the processor function in the state machine. The first new payload will have "action":"build" in place of the current "action":"synchronize" with the same "repository" and "pull_request" dictionaries, and in this new behavior it will invoke the processor function async. index.py will need a test suite to cover these new inputs, that should verify the correct Lambda invoke happened. The function will return quickly, and we will use Step Functions execution tokens to allow the state machine to run to completion.

**Clarifications:**
> I see that having "index.py" do double-duty might be confusing, let's update this plan so that our webhook mode looks like the current index.py but renamed to webhook.py, while the task mode looks like planned behavior and named task.py. We can stick them both in the same Lambda zip file and have the two different functions use different handlers.

> Flows "A" & "B" no longer make sense, there's just one flow and it looks like Flow B. We will no longer need to switch on the value of "action" now that we are planning two different Lambda function definitions with different handlers.

> I would prefer to keep our unittests inside the two function just like we do presently in index.py. We also do not need unit tests for processor.py.

## Architecture Overview

**Two separate Lambda handlers in one deployment package:**
1. **webhook.py** (renamed from index.py): Entry point for GitHub/external events, starts state machine
2. **task.py** (new): Called BY state machine with task token, invokes processor async

## Execution Flow (Unified)

```
GitHub/Trigger → webhook.py → start state machine → return immediately
                 [async] state machine → task.py + task token → invoke processor async → return immediately
                 [async] processor → does work → sendTaskSuccess/Failure → state machine completes
```

All actions (synchronize, build, future actions) follow this same async pattern.

## Implementation Changes

### 1. Rename index.py → webhook.py
- File: `webhook/webhook.py`
- Minimal changes, just renaming
- Keep existing lambda_handler function
- Keep STATE_MACHINE_ARN env var
- Starts state machine and returns (unchanged behavior)
- **Keep all existing unit tests** at bottom of file (TestLambdaHandler class)

### 2. Create task.py
- File: `webhook/task.py` (NEW)
- Handler: `lambda_handler(event, context)`
- Environment: `PROCESSOR_FUNCTION_ARN`
- Logic: Extract task token, invoke processor async with Event invocation, return immediately
- **Include unit tests** at bottom of file:
  - test_invokes_processor_async
  - test_passes_task_token_to_processor
  - test_passes_through_event_fields
  - test_returns_immediately
  - test_missing_processor_arn_env

### 3. Update processor.py
- File: `../processor.py`
- Extract `taskToken` from event
- On success: call `sfn.send_task_success(taskToken=..., output=...)`
- On error: call `sfn.send_task_failure(taskToken=..., error=..., cause=...)`
- Maintain backward compatibility (works with or without task token)

### 4. CloudFormation Updates
- File: `webhook/cloudformation-template.yaml`

**Add TaskFunction:**
- Runtime: python3.14
- Handler: task.lambda_handler
- Uses same zip as WebhookFunction
- Environment: PROCESSOR_FUNCTION_ARN

**Add TaskFunctionRole:**
- Permission to invoke ProcessorFunction

**Update WebhookFunction:**
- Handler: webhook.lambda_handler (was index.lambda_handler)

**Simplify ProcessorStateMachine:**
- Remove Choice state logic
- Single state using waitForTaskToken pattern
- Invokes TaskFunction with task token

**Update ProcessorFunctionRole:**
- Add states:SendTaskSuccess and states:SendTaskFailure permissions

**Update StateMachineRole:**
- Invoke TaskFunction (not ProcessorFunction)

### 5. Build & Test Updates

**Makefile:**
- Update zip creation to include both webhook.py and task.py
- Command: `zip webhook.zip webhook.py task.py`

**GitHub Actions:**
- Run unit tests: `python3 -m unittest webhook.py task.py -v`
- Runs automatically on PR events

### 6. Documentation
- Update TESTING.md with async flow verification steps
- Update this PLAN.md with new architecture

## File Structure
```
webhook/
├── webhook.py              # Webhook handler (renamed from index.py) + tests
├── task.py                 # NEW: Async processor invoker + tests
├── cloudformation-template.yaml
├── Makefile                # Handles zip creation
├── deploy.sh
└── trigger-webhook.sh

../
├── processor.py            # Updated with task token callbacks
└── Dockerfile
```

## Implementation Order

1. Create task.py with handler + unit tests
2. Rename index.py → webhook.py
3. Update processor.py for task token callbacks
4. Update Makefile for new zip contents
5. Add TaskFunction + TaskFunctionRole to CloudFormation
6. Simplify state machine definition
7. Update IAM permissions
8. Deploy and integration test
9. Update TESTING.md

## Testing Strategy

**Local unit tests:**
```bash
source ../.venv/bin/activate
python3 -m unittest webhook.py -v
python3 -m unittest task.py -v
```

**GitHub Actions:** Runs unit tests automatically on PR events

**Integration testing:**
- Trigger with action="synchronize" or action="build" payloads
- Verify webhook returns immediately
- Verify state machine execution waits for callback
- Check CloudWatch logs for task.py async invocation
- Verify processor sends sendTaskSuccess/sendTaskFailure

## Key Benefits

- Clean separation: webhook (entry) → task (orchestrator) → processor (worker)
- All actions async: Webhook returns immediately
- Extensible: New actions require no infrastructure changes
- Tests co-located with handlers
