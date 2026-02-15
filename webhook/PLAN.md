# Implementation Plan: State Machine Integration

## User Instructions

We are working in two branches here, and coordinating changes between the two of them in order to test changes in Github Actions. The basic setup is already complete in ../.github/workflows, and you have the permissions you need for AWS in ~/.aws and Github in ../.env. There's a script for pushing to origin in push-it.sh you may want to consult that shows how the branch migurski/do-not-merge (PR #4) is stacked atop the branch migurski/execute-a-state-machine where we are actually working. Our goal is to expand the existing CloudFormation definition to include a state machine, to have the lambda_handler() in index.py execute that state machine with its payload, and to observe that this is successful by looking at logs from Github and from AWS. The state machine should for now be just a single step, running a Docker image Lambda function based on ./Dockerfile that can check out the HEAD of this repo to test that a pull request worked. We are working in two branches here: migurski/do-not-merge (PR #4) is stacked atop the branch migurski/execute-a-state-machine where we are actually working.

## Implementation Plan

### 1. GitHub Token Security Strategy
**Problem**: Docker Lambda needs GitHub token to clone repos, but token is in gitignored `../.env`

**Solution**: AWS Secrets Manager with deployment-time injection
- **deploy.sh** will:
  1. Read `GITHUB_TOKEN` from `../.env` (already gitignored)
  2. Create/update secret in AWS Secrets Manager: `/${STACK_NAME}/github-token`
  3. Only upload if secret doesn't exist or if explicitly requested
  4. Never write token to CloudFormation template or commit it
- **CloudFormation** will:
  1. Grant Docker Lambda IAM permission to read the secret
  2. Pass secret ARN via environment variable (not the token itself)
- **Docker Lambda** will:
  1. Use boto3 to fetch token from Secrets Manager at runtime
  2. Use token to clone GitHub repo
  3. Token stays encrypted in AWS, never in logs

### 2. CloudFormation Resource Naming Strategy
**Problem**: Hardcoded names prevent multiple stacks and cause conflicts

**Solution**: Stack-scoped resource naming using `${AWS::StackName}`
- **All resources** will use stack name prefix via `!Sub`:
  - ECR Repository: `${AWS::StackName}-processor-image`
  - State Machine: `${AWS::StackName}-processor`
  - Docker Lambda: `${AWS::StackName}-processor-function`
  - Secrets: `${AWS::StackName}/github-token`
  - IAM Roles: `${AWS::StackName}-StateMachineRole`, etc.
- **Existing webhook function**: Keep current behavior for backward compatibility
- **Benefits**:
  - Multiple stacks can coexist
  - Resources are clearly associated with their stack
  - Automatic cleanup when stack is deleted

### 3. CloudFormation Template Expansion
**Add these resources**:
1. **ECR Repository** - store Docker image
2. **ProcessorFunction** (AWS::Lambda::Function):
   - PackageType: Image
   - ImageUri: Points to ECR image
   - Environment: Secret ARN reference
   - IAM: SecretsManager read permission
3. **StateMachineRole** (AWS::IAM::Role):
   - Invoke ProcessorFunction
   - CloudWatch Logs write
4. **ProcessorStateMachine** (AWS::StepFunctions::StateMachine):
   - Single Task state
   - Invokes ProcessorFunction
   - Passes input as payload
5. **Update LambdaExecutionRole**:
   - Add states:StartExecution permission

### 4. Webhook Lambda (index.py)
```python
import boto3, json, os
sfn = boto3.client('stepfunctions')

def lambda_handler(event, context):
    body = json.loads(event.get('body', '{}'))
    execution = sfn.start_execution(
        stateMachineArn=os.environ['STATE_MACHINE_ARN'],
        input=json.dumps(body)
    )
    return {'statusCode': 200, 'body': json.dumps({'executionArn': execution['executionArn']})}
```

### 5. Docker Lambda Handler (processor.py)
```python
import boto3, json, subprocess, os

def handler(event, context):
    # Fetch GitHub token from Secrets Manager
    secrets = boto3.client('secretsmanager')
    token = secrets.get_secret_value(SecretId=os.environ['GITHUB_SECRET_ARN'])['SecretString']

    # Extract PR info
    pr_sha = event.get('pull_request', {}).get('head', {}).get('sha')

    # Clone repo
    repo_url = f"https://{token}@github.com/protomaps/political-multiviews.git"
    subprocess.run(['git', 'clone', '--depth', '1', repo_url, '/tmp/repo'], check=True)
    subprocess.run(['git', 'checkout', pr_sha], cwd='/tmp/repo', check=True)

    return {'status': 'success', 'sha': pr_sha}
```

### 6. Update Dockerfile
- Add `git` package
- Copy `processor.py`
- Set CMD to `processor.handler`

### 7. Enhanced deploy.sh
**New Docker build & push section**:
1. Get ECR login credentials
2. Build Docker image for linux/arm64
3. Tag with ECR repository URL
4. Push to ECR
5. Get image digest for CloudFormation

**New Secrets Manager section** (before CloudFormation deploy):
1. Check if secret exists
2. If not, create from `../.env`
3. Verify secret is accessible

### 8. Testing & Verification Plan
1. **Build**: `make index.zip` → `./deploy.sh index.zip`
2. **Commit & Push**: Changes to migurski/execute-a-state-machine
3. **Sync branches**: Run `./push-it.sh` to update PR #4
4. **GitHub Actions triggers** → webhook Lambda
5. **Verify**:
   - GitHub Actions: Successful webhook POST
   - CloudWatch `/aws/lambda/${WebhookFunction}`: State machine execution started
   - Step Functions Console: Execution in RUNNING/SUCCEEDED state
   - CloudWatch `/aws/lambda/${ProcessorFunction}`: Git clone success logs
   - CloudWatch: No token leakage in any logs

### Key Security Guarantees
✅ Token never committed to git (../.env is gitignored)
✅ Token never in CloudFormation template
✅ Token encrypted at rest in Secrets Manager
✅ Token fetched at runtime only
✅ Token not logged (boto3 doesn't log secret values)

### Key Uniqueness Guarantees
✅ All resources prefixed with `${AWS::StackName}`
✅ Stack can be deployed multiple times with different names
✅ Resources auto-cleanup when stack deleted
✅ No hardcoded identifiers except stack name itself
