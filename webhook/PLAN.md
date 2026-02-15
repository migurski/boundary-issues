# State Machine Integration

## Original Goal

> We are working in two branches here, and coordinating changes between the two of them in order to test changes in Github Actions. The basic setup is already complete in ../.github/workflows, and you have the permissions you need for AWS in ~/.aws and Github in ../.env. There's a script for pushing to origin in push-it.sh you may want to consult that shows how the branch migurski/do-not-merge (PR #4) is stacked atop the branch migurski/execute-a-state-machine where we are actually working. Our goal is to expand the existing CloudFormation definition to include a state machine, to have the lambda_handler() in index.py execute that state machine with its payload, and to observe that this is successful by looking at logs from Github and from AWS. The state machine should for now be just a single step, running a Docker image Lambda function based on ./Dockerfile that can check out the HEAD of this repo to test that a pull request worked.

## Implementation Summary

### Architecture

The system implements a GitHub webhook → Lambda → State Machine → Docker Lambda workflow:

1. **GitHub Actions** (.github/workflows/lambda-webhook.yml) triggers on PR events
2. **Webhook Lambda** (index.py) receives events, starts Step Functions execution
3. **Step Functions State Machine** orchestrates single-step workflow
4. **Processor Lambda** (../processor.py in Docker) clones repo and checks out PR HEAD

### Components Implemented

**webhook/index.py** - Webhook Lambda handler
- Parses GitHub PR event from request body (handles both string and dict)
- Uses `context.aws_request_id` to generate unique execution names
- Starts Step Functions state machine execution
- Returns execution ARN in response
- Includes comprehensive unit tests (7 tests covering all error cases)

**../processor.py** - Docker Lambda handler (moved to parent directory)
- Fetches GitHub token from AWS Secrets Manager
- Extracts repository URL from `repository.clone_url` field
- Clones repository using authenticated GitHub URL
- Checks out PR HEAD SHA and verifies checkout
- Returns success/error status with detailed logging

**../Dockerfile** - Processor Lambda container (moved to parent directory)
- Based on Ubuntu 24.04 ARM64
- Includes Python 3, GDAL, NumPy, git
- Installs awslambdaric for Lambda runtime
- Configured with proper ENTRYPOINT and CMD for Lambda execution

**webhook/cloudformation-template.yaml** - CloudFormation stack
- `ProcessorRepository` - ECR repository for Docker images
- `ProcessorFunction` - Docker-based Lambda (ARM64, 1024MB, 300s timeout)
- `ProcessorFunctionRole` - IAM role with Secrets Manager read permission
- `ProcessorStateMachine` - Single-step state machine invoking ProcessorFunction
- `StateMachineRole` - IAM role for state machine execution
- Updated `LambdaExecutionRole` - Added states:StartExecution permission
- All resources use `${AWS::StackName}` prefix for uniqueness

**webhook/deploy.sh** - Enhanced deployment script
- Creates/verifies Secrets Manager secret from `../.env`
- Builds Docker image for linux/arm64 platform
- Pushes image to ECR (CloudFormation-managed repository)
- Handles temporary repository for initial deployment
- Deploys/updates CloudFormation stack with image URI
- Outputs webhook URL, log groups, and state machine ARN

**webhook/TESTING.md** - Comprehensive testing guide
- Manual and GitHub Actions testing workflows
- CloudWatch log verification steps for each component
- Common issues and solutions from integration testing
- Security validation procedures
- Performance benchmarks from actual runs

### Security Implementation

**GitHub Token Protection:**
- Token stored in `../.env` (gitignored)
- `deploy.sh` reads token and creates AWS Secrets Manager secret
- Secret name: `${STACK_NAME}/github-token`
- Processor Lambda retrieves at runtime via boto3
- Token never appears in CloudFormation, git, or logs

**Resource Uniqueness:**
- All resources prefixed with `${AWS::StackName}`
- Multiple stacks can coexist without conflicts
- Clean separation between stack instances

### Testing

Run unit tests locally:
```bash
source ../.venv/bin/activate
python3 -m unittest index.py -v
```

Deploy and test end-to-end:
```bash
make index.zip
./deploy.sh index.zip
./push-it.sh  # Sync branches to trigger GitHub Actions
```

See TESTING.md for detailed verification procedures and log analysis.
