Implement the webhook library in C++ with aws-lambda-cpp.

0. Work inside a temprary directory inside this one to keep files contained, do not commit any of this implementation or installation work to Git.
1. Read SPEC.md for complete behavior specification
2. Parse test_case.json and generate a test file
3. Implement the lambda_handler() function
4. Run tests until all pass
5. Package implementation into lambda.zip for deployment to AWS Lambda

All test_case.json test cases must pass.

## Implementation Hints

- The current version of aws-lambda-cpp is v0.2.10 with more information at https://github.com/awslabs/aws-lambda-cpp
- For AWS Lambda custom runtime (provided.al2023), the compiled binary MUST be named `bootstrap` in the lambda.zip package. Lambda looks for this exact filename as the entrypoint.
- The base image public.ecr.aws/lambda/provided:al2023 has a Lambda runtime entrypoint that can interfere with our bootstrap, so be sure to use the right one.
- The compiled binary architecture must match the Lambda function's architecture, by default x86_64 → build for x86_64/amd64. Run the build in Docker with `--platform` flag or appropriate base images to ensure architecture compatibility.
