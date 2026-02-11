Webhook is an AWS Lambda function to accept events from Github Actions and log them to stderr.

It has one function called lambda_handler() function, whose input is JSON from Github Actions delivered via a function URL input.
