# AWS Setup

This document describes the AWS resources and permissions required to deploy and run the webhook infrastructure.

## Overview

Deployment is handled by `webhook/deploy.sh`, which uses two CloudFormation stacks:

- **Bootstrap stack** (`boundary-issues-bootstrap`) — long-lived foundational resources: S3 bucket, ECR repository, Secrets Manager secret
- **Webhook stack** (`boundary-issues-webhook`) — application resources: Lambda functions, Step Functions state machine, IAM roles

The stacks are deployed in that order. The bootstrap stack's outputs are passed as parameters to the webhook stack.

## Prerequisites

- AWS CLI installed and configured
- Docker with Buildx support (for building the ARM64 processor image)
- `jq` and `make` available on the PATH
- A `.env` file in the repo root containing `GITHUB_TOKEN=<token>` (used to populate Secrets Manager on local deploys; not required in CI)

## Deployer IAM User

Create an IAM user for deployment (e.g. `Boundary-Issues-Deployer`) and attach a customer-managed policy with the following permissions. The policy must allow:

### CloudFormation

- `cloudformation:ListStacks` — resource `*` (not resource-scopable)
- `cloudformation:DescribeStacks`, `CreateStack`, `UpdateStack`, `DescribeStackEvents` — scoped to both stacks:
  - `arn:aws:cloudformation:<region>:<account>:stack/boundary-issues-bootstrap/*`
  - `arn:aws:cloudformation:<region>:<account>:stack/boundary-issues-webhook/*`

### ECR

- `ecr:GetAuthorizationToken` — resource `*` (not resource-scopable)
- `ecr:BatchCheckLayerAvailability`, `GetDownloadUrlForLayer`, `BatchGetImage`, `InitiateLayerUpload`, `UploadLayerPart`, `CompleteLayerUpload`, `PutImage`, `DescribeImages` — scoped to the ECR repository created by the bootstrap stack (name pattern: `boundary-issues-bootstrap-images-<region>-<account>`)

### S3

- `s3:PutObject`, `s3:GetObject`, `s3:ListBucket` — scoped to the S3 bucket created by the bootstrap stack (name pattern: `boundary-issues-bootstrap-packages-<region>-<account>`)

### Lambda

- Full CRUD + URL config + tagging actions — scoped to `arn:aws:lambda:<region>:<account>:function:boundary-issues-webhook-*`

### Step Functions

- `states:CreateStateMachine`, `UpdateStateMachine`, `DeleteStateMachine`, `DescribeStateMachine`, `TagResource` — the state machine name is CloudFormation-generated, so scope to `arn:aws:states:<region>:<account>:stateMachine:*` or restrict once a static name is set in the template

### IAM

- Role management (`CreateRole`, `DeleteRole`, `GetRole`, `PassRole`, `AttachRolePolicy`, `DetachRolePolicy`, `PutRolePolicy`, `DeleteRolePolicy`, `GetRolePolicy`) — scoped to `arn:aws:iam::<account>:role/boundary-issues-webhook-*`
- Managed policy management (`CreatePolicy`, `DeletePolicy`, `CreatePolicyVersion`, `DeletePolicyVersion`, `GetPolicy`, `GetPolicyVersion`, `ListPolicyVersions`) — scoped to `arn:aws:iam::<account>:policy/boundary-issues-webhook-*`

### CloudWatch Logs

- `logs:CreateLogGroup`, `DeleteLogGroup`, `DescribeLogGroups`, `PutRetentionPolicy` — scoped to `/aws/lambda/boundary-issues-webhook-*`

### EventBridge

- `events:PutRule`, `DeleteRule`, `DescribeRule`, `PutTargets`, `RemoveTargets`, `ListTargetsByRule`, `ListRules`, `TagResource`, `UntagResource` — scoped to `arn:aws:events:<region>:<account>:rule/boundary-issues-webhook-*` (covers all current and future scheduled rules in the webhook stack)

## GitHub Actions Secrets

The deployer's credentials must be added to the repository as Actions secrets:

| Secret | Description |
|---|---|
| `AWS_ACCESS_KEY_ID` | Deployer IAM user access key |
| `AWS_SECRET_ACCESS_KEY` | Deployer IAM user secret key |
| `AWS_DEFAULT_REGION` | Target region (e.g. `us-west-2`) |

## Resources Created

### Bootstrap Stack

| Resource | Type | Name pattern |
|---|---|---|
| Deployment bucket | S3 | `<stack-name>-packages-<region>-<account>` |
| Processor image repo | ECR | `<stack-name>-images-<region>-<account>` |
| GitHub token secret | Secrets Manager | `<stack-name>-webhook/github-token` |

### Webhook Stack

| Resource | Type | Notes |
|---|---|---|
| Webhook Lambda | Lambda (ZIP) | Handles incoming GitHub webhook POSTs |
| Task Lambda | Lambda (ZIP) | Invokes processor with Step Functions task token |
| Processor Lambda | Lambda (Docker/ARM64) | Runs Planetiler tile generation |
| Finish Lambda | Lambda (ZIP) | Reports success/failure back to GitHub PR status |
| Sweep Lambda | Lambda (ZIP) | Refreshes stale OSM relation cache on a schedule |
| Sweep schedule rule | EventBridge | Triggers SweepFunction on a cron schedule |
| State machine | Step Functions | Orchestrates task → optional second task → finish |
| IAM roles | IAM | One per Lambda function + one for the state machine |

## Deployment Flow

1. Bootstrap stack is created or updated
2. GitHub token is written to Secrets Manager from `.env` (local) or skipped (CI)
3. Processor Docker image is built for `linux/arm64` and pushed to ECR
   - In GitHub Actions, a registry cache (`<repo>:cache`) is used to speed up subsequent builds
4. Lambda ZIP package (`index.zip`) is built via `make` and uploaded to S3
5. Webhook stack is created or updated with references to the new image digest and ZIP S3 key
