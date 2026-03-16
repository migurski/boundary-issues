from __future__ import annotations

import json
import logging
import os
import random
import typing
import unittest
import unittest.mock
import urllib.parse
import urllib.request
import urllib.error

# Note: boto3 is available in AWS Lambda runtime
# For local testing, install via: pip install boto3
import boto3

# Configure logging
logging.basicConfig(format='%(levelname)s: %(message)s')
logging.getLogger().setLevel(logging.INFO)


EXECUTION_NAME_PAT = "PR{0}-{1}"


def lambda_handler(event: dict[str, typing.Any], context: typing.Any) -> dict[str, typing.Any]:
    """
    Webhook Lambda handler that receives GitHub events and triggers state machine.
    """
    logging.info(f"Received event: {json.dumps(event)}")

    # Get state machine ARN from environment
    state_machine_arn = os.environ.get('STATE_MACHINE_ARN')
    if not state_machine_arn:
        logging.error("STATE_MACHINE_ARN environment variable not set")
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': 'STATE_MACHINE_ARN not configured'})
        }

    # Parse the request body
    try:
        body = event.get('body', '{}')
        if isinstance(body, str):
            payload = json.loads(body)
        else:
            payload = body

        logging.info(f"Parsed payload: {json.dumps(payload)}")

    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse request body: {e}")
        return {
            'statusCode': 400,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': 'Invalid JSON in request body'})
        }

    # Initialize Step Functions client
    sfn = boto3.client('stepfunctions')

    # Start state machine execution
    try:
        pr_number = payload.get('number', 'unknown')
        execution_name = EXECUTION_NAME_PAT.format(pr_number, context.aws_request_id[:8])

        logging.info(f"Starting state machine execution: {execution_name}")

        destination_prefix = f"s3://{os.environ.get('DATA_BUCKET')}/{context.aws_request_id[:8]}/"
        wait_seconds = random.randint(120, 360)
        stepfunctions_payload = {"destination": destination_prefix, "wait_seconds": wait_seconds, **payload}

        response = sfn.start_execution(
            stateMachineArn=state_machine_arn,
            name=execution_name,
            input=json.dumps(stepfunctions_payload)
        )

        logging.info(f"State machine execution started: {response['executionArn']}")

        # Set GitHub status to pending with execution URL
        do_status(payload, destination_prefix)

        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'message': 'State machine execution started'})
        }

    except Exception as e:
        logging.error(f"Failed to start state machine execution: {e}")
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': f'Failed to start execution: {str(e)}'})
        }


def do_status(payload: dict[str, typing.Any], destination_prefix: str | None) -> None:
    """
    Set GitHub PR status to pending.

    Args:
        payload: Parsed GitHub webhook payload
        destination_prefix: s3:// URL where results go
    """
    github_secret_arn = os.environ.get('GITHUB_SECRET_ARN')
    if not github_secret_arn:
        logging.warning("GITHUB_SECRET_ARN not set, skipping status update")
        return

    # Extract required information from payload
    statuses_url = payload.get('repository', {}).get('statuses_url')
    if not statuses_url:
        logging.warning("repository.statuses_url not found in payload, skipping status update")
        return

    head_sha = payload.get('pull_request', {}).get('head', {}).get('sha')
    if not head_sha:
        logging.warning("pull_request.head.sha not found in payload, skipping status update")
        return

    # Fetch GitHub token from Secrets Manager
    try:
        secrets_client = boto3.client('secretsmanager')
        secret_response = secrets_client.get_secret_value(SecretId=github_secret_arn)
        github_token = secret_response['SecretString']
        logging.info("Successfully retrieved GitHub token from Secrets Manager")
    except Exception as e:
        logging.error(f"Failed to retrieve GitHub token: {e}")
        return

    # Replace {sha} placeholder in statuses_url with actual SHA
    status_api_url = statuses_url.replace('{sha}', head_sha)
    logging.info(f"Status API URL: {status_api_url}")

    # Construct AWS console URL for the execution
    if destination_prefix:
        parsed_url = urllib.parse.urlparse(destination_prefix)
        s3_client = boto3.client('s3')
        region_name = s3_client.get_bucket_location(Bucket=parsed_url.netloc)['LocationConstraint']
        target_host = f"{parsed_url.netloc}.s3.{region_name}.amazonaws.com"
        target_path = os.path.join(parsed_url.path, 'index.html')
        target_url = urllib.parse.urlunparse(('https', target_host, target_path, None, None, None))
        pr_html_url = payload.get('pull_request', {}).get('html_url', '')
        pr_number = payload.get('number', '')
        pr_link = f'<a href="{pr_html_url}">Pull Request #{pr_number}</a>' if pr_html_url else 'Pull Request'
        index_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Boundary Issues Check</title>
<script src="https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js"></script>
<style>
body {{ font-family: sans-serif; margin: 0; display: flex; flex-direction: column; height: 100vh; }}
header {{ padding: 8px 12px; background: #f5f5f5; border-bottom: 1px solid #ddd; }}
iframe {{ flex: 1; border: none; width: 100%; }}
</style>
</head>
<body>
<header>
    <p>{pr_link}</p>
    <p id="status" hx-get="status.html" hx-trigger="load"></p>
</header>
<iframe src="preview.html"></iframe>
</body>
</html>"""
        s3_client.put_object(
            Bucket=parsed_url.netloc,
            Key=target_path.lstrip('/'),
            ACL='public-read',
            ContentType='text/html',
            Body=index_html.encode('utf8'),
            StorageClass='INTELLIGENT_TIERING',
        )
        s3_client.put_object(
            Bucket=parsed_url.netloc,
            Key=os.path.join(parsed_url.path, 'status.html').lstrip('/'),
            ACL='public-read',
            ContentType='text/html',
            Body='Starting first check.'.encode('utf8'),
            StorageClass='INTELLIGENT_TIERING',
        )
        s3_client.put_object(
            Bucket=parsed_url.netloc,
            Key=os.path.join(parsed_url.path, 'preview.html').lstrip('/'),
            ACL='public-read',
            ContentType='text/html',
            Body="""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Preview</title></head>
<body><p>Preview not yet built.</p></body>
</html>""".encode('utf8'),
            StorageClass='INTELLIGENT_TIERING',
        )
    else:
        target_url = None

    # Create GitHub status
    status_payload = {
        'state': 'pending',
        'description': 'Boundary issues check pending',
        'context': 'boundary-issues-processor'
    }
    if target_url:
        status_payload['target_url'] = target_url

    logging.info(f"Creating GitHub status: {json.dumps(status_payload)}")

    try:
        # Create HTTP request
        request = urllib.request.Request(
            status_api_url,
            data=json.dumps(status_payload).encode('utf-8'),
            headers={
                'Authorization': f'token {github_token}',
                'Content-Type': 'application/json',
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': 'boundary-issues-webhook'
            },
            method='POST'
        )

        # Send request
        with urllib.request.urlopen(request) as response:
            response_data = response.read()
            logging.info(f"GitHub API response: {response_data.decode('utf-8')}")

            return

    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        logging.error(f"GitHub API request failed: {e.code} {e.reason}")
        logging.error(f"Response body: {error_body}")
        return
    except Exception as e:
        logging.error(f"Failed to create GitHub status: {e}")
        return


class TestLambdaHandler(unittest.TestCase):
    """
    Unit tests for the webhook Lambda handler.

    These tests validate expected behavior learned from integration testing:
    - Context must use aws_request_id (not request_id)
    - Body can be string or already-parsed dict
    - STATE_MACHINE_ARN must be set in environment
    - Valid GitHub PR payloads trigger state machine execution
    - Errors return appropriate status codes
    """

    def setUp(self) -> None:
        """Set up test fixtures"""
        self.test_state_machine_arn = 'arn:aws:states:us-west-2:123456789012:stateMachine:test-processor'
        self.test_execution_arn = 'arn:aws:states:us-west-2:123456789012:execution:test-processor:PR4-12345678'

        # Mock context object with aws_request_id (learned from logs)
        self.mock_context = unittest.mock.Mock()
        self.mock_context.aws_request_id = '12345678-1234-1234-1234-123456789012'

        # Sample GitHub PR event (based on actual GitHub Actions payload)
        self.github_pr_event = {
            'version': '2.0',
            'routeKey': '$default',
            'rawPath': '/',
            'headers': {
                'content-type': 'application/json',
                'x-github-event': 'pull_request',
                'x-github-delivery': '22041329269'
            },
            'body': json.dumps({
                'action': 'synchronize',
                'number': 4,
                'pull_request': {
                    'html_url': 'https://github.com/migurski/boundary-issues/pull/4',
                    'diff_url': 'https://github.com/migurski/boundary-issues/pull/4.diff',
                    'base': {'sha': 'db7adabab3c93cf4c05f35c1df2b716596f82faa'},
                    'head': {'sha': 'f6400f99d7e2094ccd2034c47f72820cef488a1f'}
                }
            })
        }

    @unittest.mock.patch.dict(os.environ, {'STATE_MACHINE_ARN': 'arn:aws:states:us-west-2:123456789012:stateMachine:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_successful_execution(self, mock_boto_client: typing.Any) -> None:
        """Test successful state machine execution with GitHub PR event"""
        # Mock Step Functions client
        mock_sfn = unittest.mock.MagicMock()
        mock_sfn.start_execution.return_value = {
            'executionArn': self.test_execution_arn
        }
        mock_boto_client.return_value = mock_sfn

        # Execute handler
        response = lambda_handler(self.github_pr_event, self.mock_context)

        # Verify response
        self.assertEqual(response['statusCode'], 200)
        self.assertEqual(response['headers']['Content-Type'], 'application/json')

        body = json.loads(response['body'])
        self.assertEqual(body['message'], 'State machine execution started')

        # Verify Step Functions client was called correctly
        mock_boto_client.assert_called_once_with('stepfunctions')
        mock_sfn.start_execution.assert_called_once()

        call_args = mock_sfn.start_execution.call_args[1]
        self.assertEqual(call_args['stateMachineArn'], self.test_state_machine_arn)
        self.assertEqual(call_args['name'], 'PR4-12345678')

        # Verify payload was passed correctly
        input_payload = json.loads(call_args['input'])
        self.assertEqual(input_payload['action'], 'synchronize')
        self.assertEqual(input_payload['number'], 4)
        self.assertIn('wait_seconds', input_payload)
        self.assertGreaterEqual(input_payload['wait_seconds'], 120)
        self.assertLessEqual(input_payload['wait_seconds'], 360)

    @unittest.mock.patch.dict(os.environ, {}, clear=True)
    def test_missing_state_machine_arn(self) -> None:
        """Test error when STATE_MACHINE_ARN environment variable is not set"""
        response = lambda_handler(self.github_pr_event, self.mock_context)

        self.assertEqual(response['statusCode'], 500)
        body = json.loads(response['body'])
        self.assertEqual(body['error'], 'STATE_MACHINE_ARN not configured')

    @unittest.mock.patch.dict(os.environ, {'STATE_MACHINE_ARN': 'arn:aws:states:us-west-2:123456789012:stateMachine:test-processor'})
    def test_invalid_json_body(self) -> None:
        """Test error handling for invalid JSON in request body"""
        invalid_event = {
            'body': '{invalid json}'
        }

        response = lambda_handler(invalid_event, self.mock_context)

        self.assertEqual(response['statusCode'], 400)
        body = json.loads(response['body'])
        self.assertIn('Invalid JSON', body['error'])

    @unittest.mock.patch.dict(os.environ, {'STATE_MACHINE_ARN': 'arn:aws:states:us-west-2:123456789012:stateMachine:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_body_as_dict(self, mock_boto_client: typing.Any) -> None:
        """Test that handler accepts body as already-parsed dict (not just string)"""
        # Mock Step Functions client
        mock_sfn = unittest.mock.MagicMock()
        mock_sfn.start_execution.return_value = {
            'executionArn': self.test_execution_arn
        }
        mock_boto_client.return_value = mock_sfn

        # Event with body as dict (not string)
        event_with_dict_body = {
            'body': {
                'action': 'synchronize',
                'number': 4,
                'pull_request': {
                    'diff_url': 'https://github.com/migurski/boundary-issues/pull/4.diff',
                    'base': {'sha': 'abc123'},
                    'head': {'sha': 'def456'}
                }
            }
        }

        response = lambda_handler(event_with_dict_body, self.mock_context)

        self.assertEqual(response['statusCode'], 200)
        body = json.loads(response['body'])
        self.assertEqual(body['message'], 'State machine execution started')

    @unittest.mock.patch.dict(os.environ, {'STATE_MACHINE_ARN': 'arn:aws:states:us-west-2:123456789012:stateMachine:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_context_aws_request_id(self, mock_boto_client: typing.Any) -> None:
        """
        Test that handler uses context.aws_request_id (not context.request_id).

        This was a bug discovered during integration testing:
        ERROR: 'LambdaContext' object has no attribute 'request_id'
        """
        # Mock Step Functions client
        mock_sfn = unittest.mock.MagicMock()
        mock_sfn.start_execution.return_value = {
            'executionArn': self.test_execution_arn
        }
        mock_boto_client.return_value = mock_sfn

        # Execute handler
        lambda_handler(self.github_pr_event, self.mock_context)

        # Verify execution name uses aws_request_id
        call_args = mock_sfn.start_execution.call_args[1]
        execution_name = call_args['name']

        # Should be PR4-12345678 (first 8 chars of aws_request_id)
        self.assertEqual(execution_name, 'PR4-12345678')
        self.assertTrue(execution_name.startswith('PR4-'))

    @unittest.mock.patch.dict(os.environ, {'STATE_MACHINE_ARN': 'arn:aws:states:us-west-2:123456789012:stateMachine:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_step_functions_failure(self, mock_boto_client: typing.Any) -> None:
        """Test error handling when Step Functions start_execution fails"""
        # Mock Step Functions client to raise exception
        mock_sfn = unittest.mock.MagicMock()
        mock_sfn.start_execution.side_effect = Exception('Step Functions unavailable')
        mock_boto_client.return_value = mock_sfn

        response = lambda_handler(self.github_pr_event, self.mock_context)

        self.assertEqual(response['statusCode'], 500)
        body = json.loads(response['body'])
        self.assertIn('Failed to start execution', body['error'])
        self.assertIn('Step Functions unavailable', body['error'])

    @unittest.mock.patch.dict(os.environ, {'STATE_MACHINE_ARN': 'arn:aws:states:us-west-2:123456789012:stateMachine:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_execution_name_format(self, mock_boto_client: typing.Any) -> None:
        """Test execution name follows expected format: PR{number}-{request_id[:8]}"""
        mock_sfn = unittest.mock.MagicMock()
        mock_sfn.start_execution.return_value = {
            'executionArn': self.test_execution_arn
        }
        mock_boto_client.return_value = mock_sfn

        # Test with specific PR number
        event = self.github_pr_event.copy()
        lambda_handler(event, self.mock_context)

        call_args = mock_sfn.start_execution.call_args[1]
        execution_name = call_args['name']

        # Verify format
        import re
        self.assertIsNotNone(re.match(r'^PR\d+-[a-f0-9]{8}$', execution_name))

        # Test with missing PR number (should use 'unknown')
        event_no_pr = {
            'body': json.dumps({'action': 'opened'})
        }

        mock_sfn.reset_mock()
        lambda_handler(event_no_pr, self.mock_context)

        call_args = mock_sfn.start_execution.call_args[1]
        execution_name = call_args['name']
        self.assertTrue(execution_name.startswith('PRunknown-'))



if __name__ == '__main__':
    unittest.main()
