# Plan: Add `finish.py` to update GitHub PR status

## 1. Create `finish.py` Lambda function
- **Location**: `/Users/migurski/Documents/protomaps-political-multiviews/webhook/finish.py`
- **Purpose**: Update GitHub PR status with state machine execution result
- **Functionality**:
  - Receive execution result (success/failure) and original event data
  - Fetch GitHub token from Secrets Manager
  - Extract `repository.statuses_url` from the event
  - Construct AWS console URL for the state machine execution
  - Create GitHub status via API with:
    - `state`: "success" or "failure"
    - `target_url`: AWS console URL (e.g., `https://us-west-2.console.aws.amazon.com/states/home?region=us-west-2#/v2/executions/details/arn:...`)
    - `description`: Brief message about the result
    - `context`: "boundary-issues-processor"
- **Include comprehensive unit tests** following the same pattern as `webhook.py` and `task.py`

## 2. Update CloudFormation template (`cloudformation-template.yaml`)
- **Add FinishFunctionRole**: IAM role with permissions for:
  - CloudWatch Logs (basic execution)
  - Secrets Manager (read GitHub token)
- **Add FinishFunction**: Lambda function resource
  - Runtime: python3.14
  - Handler: finish.lambda_handler
  - Code from S3 (same ZIP as webhook.py and task.py)
  - Environment variable: GITHUB_SECRET_ARN
- **Update StateMachineRole**: Add permission to invoke FinishFunction
- **Update ProcessorStateMachine definition**: Add new states to call FinishFunction on both success and failure:
  ```
  InvokeTask (waitForTaskToken)
    → Success → ReportSuccess (invoke finish.py with success)
    → Failure (Catch) → ReportFailure (invoke finish.py with failure)
  ```

## 3. Update build and deployment files
- **Makefile**: Add `finish.py` to the ZIP package (`index.zip: webhook.py task.py finish.py`)
- **GitHub Actions workflow**: Add test for finish.py (`python -m unittest webhook/finish.py -v`)

## 4. No changes needed for:
- `deploy.sh` - works automatically with updated CloudFormation template
- `webhook.py` - already passes through `repository.statuses_url`
- `task.py` and `processor.py` - no changes needed

The finish function will be invoked by the state machine after the task completes (success or failure), and will create a visible status on the GitHub PR with a link to view the execution details in AWS console.

---

## Original Request

We want to add a new status to a PR to reflect the progress of the state machine. For this, we will need to add a new small function alongside webhook.py and task.py called finish.py, deployed and coded and tested just like the other two. Our webhook.py function will create a new status on the PR using repository.statuses_url from the input JSON, then mark it as either succeeded or failed based on the final result of the state machine. Github status like URLs, let's just use the AWS console URL e.g. https://us-west-2.console.aws.amazon.com/states/home?region=us-west-2#/v2/executions/details/arn:aws:states:us-west-2:101696101272:execution:boundary-issues-webhook-processor:pr-4-9e59f95b for now.
