Implement the webhook library in C++ with aws-lambda-cpp.

1. Read SPEC.md for complete behavior specification
2. Parse test_case.json and generate a test file
3. Implement the lambda_handler() function
4. Run tests until all pass
5. Package implementation into lambda.zip for deployment to AWS Lambda

All test_case.json test cases must pass.

## Important Requirements

### Binary Naming
For AWS Lambda custom runtime (provided.al2023), the compiled binary MUST be named `bootstrap` in the lambda.zip package. Lambda looks for this exact filename as the entrypoint.

### Architecture Matching
The compiled binary architecture must match the Lambda function's architecture:
- Lambda x86_64 → build for x86_64/amd64
- Lambda arm64 → build for arm64/aarch64

Use Docker's `--platform` flag or appropriate base images to ensure architecture compatibility.
