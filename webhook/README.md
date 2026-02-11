Define and deploy an AWS Lambda function to respond to Github Actions.

1. INSTALL.md contains information on building and testing lambda.zip, follow all of its steps
2. deploy.sh deploys lambda.zip to AWS Cloudformation and yields the function URL
3. Function URL for a new deploy should be used in trigger-webhook.sh

Look for an existing Python virtualenv .venv directory, and assume existing AWS default credentials.

Look for existing Github actions in ../.github.