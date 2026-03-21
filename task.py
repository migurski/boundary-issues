from __future__ import annotations

import json
import logging
import os
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


class SupersededCommit(Exception):
    pass


def fetch_github_token(github_secret_arn: str) -> str:
    secrets_client = boto3.client('secretsmanager')
    secret_response = secrets_client.get_secret_value(SecretId=github_secret_arn)
    return str(secret_response['SecretString'])


def get_latest_pr_sha(repo_full_name: str, pr_number: int, github_token: str) -> str:
    url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/commits"
    request = urllib.request.Request(
        url,
        headers={
            'Authorization': f'token {github_token}',
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'boundary-issues-webhook'
        }
    )
    with urllib.request.urlopen(request) as response:
        commits = json.loads(response.read())
    return str(commits[-1]['sha'])


def post_superseded_status(statuses_url: str, head_sha: str, github_token: str) -> None:
    status_api_url = statuses_url.replace('{sha}', head_sha)
    status_payload = {
        'state': 'error',
        'description': 'Superseded by newer commit',
        'context': 'boundary-issues-processor'
    }
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
    try:
        with urllib.request.urlopen(request) as response:
            logging.info(f"Posted superseded status: {response.read().decode('utf-8')}")
    except urllib.error.HTTPError as e:
        logging.error(f"Failed to post superseded status: {e.code} {e.reason} {e.read().decode('utf-8')}")


def write_status_html(destination: str, message: str) -> None:
    """
    Write a message to status.html in the S3 destination.

    Args:
        destination: s3:// URL where results go
        message: Message to write to status.html
    """
    try:
        parsed_url = urllib.parse.urlparse(destination)
        s3_client = boto3.client('s3')
        target_path = os.path.join(parsed_url.path, 'status.html')

        s3_client.put_object(
            Bucket=parsed_url.netloc,
            Key=target_path.lstrip('/'),
            ACL='public-read',
            ContentType='text/html',
            Body=message.encode('utf8'),
            StorageClass='INTELLIGENT_TIERING',
        )

        logging.info(f"Successfully wrote to status.html: {message}")

    except Exception as e:
        logging.error(f"Failed to write to status.html: {e}")
        # Don't fail the whole handler if S3 write fails
        pass


def lambda_handler(event: dict[str, typing.Any], context: typing.Any) -> dict[str, typing.Any]:
    """
    Task Lambda handler that is called by the state machine with a task token.
    Invokes the processor function asynchronously and returns immediately.
    The processor will call sendTaskSuccess/sendTaskFailure when done.
    """
    logging.info(f"Received event: {json.dumps(event)}")

    # Get processor function ARN from environment
    processor_arn = os.environ.get('PROCESSOR_FUNCTION_ARN')
    if not processor_arn:
        logging.error("PROCESSOR_FUNCTION_ARN environment variable not set")
        return {
            'statusCode': 500,
            'error': 'PROCESSOR_FUNCTION_ARN not configured'
        }

    # Extract task token from event
    task_token = event.get('taskToken')
    if not task_token:
        logging.error("taskToken not found in event")
        return {
            'statusCode': 400,
            'error': 'taskToken not found in event'
        }

    # Prepare payload for processor
    # Pass through the original event fields plus the task token
    # Convert taskSequence to checkFreshOSM for processor
    task_sequence = event.get('taskSequence')
    processor_payload = event.copy()

    # Remove taskSequence and add checkFreshOSM instead
    processor_payload.pop('taskSequence', None)
    if task_sequence == 'second':
        processor_payload['checkFreshOSM'] = True

        # Check whether our commit is still the latest on the PR
        github_secret_arn = os.environ.get('GITHUB_SECRET_ARN')
        if github_secret_arn:
            github_token = fetch_github_token(github_secret_arn)
            repo_full_name = event.get('repository', {}).get('full_name')
            pr_number = event.get('number')
            our_sha = event.get('pull_request', {}).get('head', {}).get('sha')
            statuses_url = event.get('repository', {}).get('statuses_url', '')
            if repo_full_name and pr_number and our_sha:
                latest_sha = get_latest_pr_sha(repo_full_name, pr_number, github_token)
                if latest_sha != our_sha:
                    post_superseded_status(statuses_url, our_sha, github_token)
                    raise SupersededCommit(
                        f"Superseded: newer commit {latest_sha} exists on PR #{pr_number}"
                    )

    logging.info(f"Invoking processor function asynchronously: {processor_arn}")
    logging.info(f"Payload: {json.dumps(processor_payload)}")

    # Initialize Lambda client
    lambda_client = boto3.client('lambda')

    # Invoke processor function asynchronously (Event invocation type)
    try:
        response = lambda_client.invoke(
            FunctionName=processor_arn,
            InvocationType='Event',  # Async invocation
            Payload=json.dumps(processor_payload)
        )

        logging.info(f"Processor invoked successfully, StatusCode: {response['StatusCode']}")

        # Write to index.html if this is the first task
        task_sequence = event.get('taskSequence')
        destination = event.get('destination')

        if task_sequence == 'first' and destination:
            logging.info("Writing first status to status.html")
            write_status_html(destination, 'Starting first check.')
        elif task_sequence != 'first' and destination:
            logging.info("Writing second status to status.html")
            write_status_html(destination, 'First check looks fine. Starting next check.')

        return {
            'statusCode': 200,
            'message': 'Processor invoked asynchronously'
        }

    except Exception as e:
        logging.error(f"Failed to invoke processor function: {e}")
        return {
            'statusCode': 500,
            'error': f'Failed to invoke processor: {str(e)}'
        }


class TestLambdaHandler(unittest.TestCase):
    """
    Unit tests for the task Lambda handler.

    These tests validate:
    - PROCESSOR_FUNCTION_ARN must be set in environment
    - Task token must be present in event
    - Processor is invoked asynchronously (Event invocation type)
    - Task token is passed through to processor
    - Event fields are passed through to processor
    - Handler returns immediately after invoking processor
    """

    def setUp(self) -> None:
        """Set up test fixtures"""
        self.test_processor_arn = 'arn:aws:lambda:us-west-2:123456789012:function:test-processor'
        self.test_task_token = 'AAAAKgAAAAIAAAAAAAAAAe6fhGHwvKI4Jh0BrxnlCGDEBd02g='

        # Mock context object
        self.mock_context = unittest.mock.Mock()
        self.mock_context.aws_request_id = '12345678-1234-1234-1234-123456789012'

        # Sample event from state machine with task token
        self.state_machine_event = {
            'taskToken': self.test_task_token,
            'action': 'synchronize',
            'number': 4,
            'pull_request': {
                'diff_url': 'https://github.com/migurski/boundary-issues/pull/4.diff',
                'base': {'sha': 'db7adabab3c93cf4c05f35c1df2b716596f82faa'},
                'head': {'sha': 'f6400f99d7e2094ccd2034c47f72820cef488a1f'}
            },
            'repository': {
                'full_name': 'migurski/boundary-issues'
            },
            'destination': 's3://test-bucket/test-path/',
            'taskSequence': 'first'
        }

    @unittest.mock.patch.dict(os.environ, {'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_invokes_processor_async(self, mock_boto_client: typing.Any) -> None:
        """Test that processor is invoked asynchronously with Event invocation type"""
        # Mock Lambda client
        mock_lambda = unittest.mock.MagicMock()
        mock_lambda.invoke.return_value = {
            'StatusCode': 202
        }

        # Mock S3 client
        mock_s3 = unittest.mock.MagicMock()
        mock_s3.get_bucket_location.return_value = {'LocationConstraint': 'us-west-2'}

        # Configure boto3.client to return the appropriate mock
        def client_factory(service_name: str) -> typing.Any:
            if service_name == 'lambda':
                return mock_lambda
            elif service_name == 's3':
                return mock_s3
            return unittest.mock.MagicMock()

        mock_boto_client.side_effect = client_factory

        # Execute handler
        response = lambda_handler(self.state_machine_event, self.mock_context)

        # Verify response
        self.assertEqual(response['statusCode'], 200)
        self.assertEqual(response['message'], 'Processor invoked asynchronously')

        # Verify Lambda client was called correctly
        mock_lambda.invoke.assert_called_once()

        call_args = mock_lambda.invoke.call_args[1]
        self.assertEqual(call_args['FunctionName'], self.test_processor_arn)
        self.assertEqual(call_args['InvocationType'], 'Event')  # Async invocation

    @unittest.mock.patch.dict(os.environ, {'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_passes_task_token_to_processor(self, mock_boto_client: typing.Any) -> None:
        """Test that task token is passed through to processor"""
        # Mock Lambda client
        mock_lambda = unittest.mock.MagicMock()
        mock_lambda.invoke.return_value = {
            'StatusCode': 202
        }

        # Mock S3 client
        mock_s3 = unittest.mock.MagicMock()
        mock_s3.get_bucket_location.return_value = {'LocationConstraint': 'us-west-2'}

        # Configure boto3.client to return the appropriate mock
        def client_factory(service_name: str) -> typing.Any:
            if service_name == 'lambda':
                return mock_lambda
            elif service_name == 's3':
                return mock_s3
            return unittest.mock.MagicMock()

        mock_boto_client.side_effect = client_factory

        # Execute handler
        lambda_handler(self.state_machine_event, self.mock_context)

        # Verify task token was passed to processor
        call_args = mock_lambda.invoke.call_args[1]
        payload = json.loads(call_args['Payload'])
        self.assertEqual(payload['taskToken'], self.test_task_token)

    @unittest.mock.patch.dict(os.environ, {'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_passes_through_event_fields(self, mock_boto_client: typing.Any) -> None:
        """Test that all event fields are passed through to processor"""
        # Mock Lambda client
        mock_lambda = unittest.mock.MagicMock()
        mock_lambda.invoke.return_value = {
            'StatusCode': 202
        }

        # Mock S3 client
        mock_s3 = unittest.mock.MagicMock()
        mock_s3.get_bucket_location.return_value = {'LocationConstraint': 'us-west-2'}

        # Configure boto3.client to return the appropriate mock
        def client_factory(service_name: str) -> typing.Any:
            if service_name == 'lambda':
                return mock_lambda
            elif service_name == 's3':
                return mock_s3
            return unittest.mock.MagicMock()

        mock_boto_client.side_effect = client_factory

        # Execute handler
        lambda_handler(self.state_machine_event, self.mock_context)

        # Verify all fields were passed through
        call_args = mock_lambda.invoke.call_args[1]
        payload = json.loads(call_args['Payload'])

        self.assertEqual(payload['action'], 'synchronize')
        self.assertEqual(payload['number'], 4)
        self.assertEqual(payload['pull_request']['diff_url'],
                        'https://github.com/migurski/boundary-issues/pull/4.diff')
        self.assertEqual(payload['repository']['full_name'],
                        'migurski/boundary-issues')

    @unittest.mock.patch.dict(os.environ, {'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_returns_immediately(self, mock_boto_client: typing.Any) -> None:
        """Test that handler returns immediately after async invocation"""
        # Mock Lambda client
        mock_lambda = unittest.mock.MagicMock()
        mock_lambda.invoke.return_value = {
            'StatusCode': 202  # Accepted for async invocation
        }

        # Mock S3 client
        mock_s3 = unittest.mock.MagicMock()
        mock_s3.get_bucket_location.return_value = {'LocationConstraint': 'us-west-2'}

        # Configure boto3.client to return the appropriate mock
        def client_factory(service_name: str) -> typing.Any:
            if service_name == 'lambda':
                return mock_lambda
            elif service_name == 's3':
                return mock_s3
            return unittest.mock.MagicMock()

        mock_boto_client.side_effect = client_factory

        # Execute handler
        response = lambda_handler(self.state_machine_event, self.mock_context)

        # Verify immediate return with success
        self.assertEqual(response['statusCode'], 200)
        self.assertIn('message', response)

        # Verify async invocation was used (Event type)
        call_args = mock_lambda.invoke.call_args[1]
        self.assertEqual(call_args['InvocationType'], 'Event')

    @unittest.mock.patch.dict(os.environ, {}, clear=True)
    def test_missing_processor_arn_env(self) -> None:
        """Test error when PROCESSOR_FUNCTION_ARN environment variable is not set"""
        response = lambda_handler(self.state_machine_event, self.mock_context)

        self.assertEqual(response['statusCode'], 500)
        self.assertEqual(response['error'], 'PROCESSOR_FUNCTION_ARN not configured')

    @unittest.mock.patch.dict(os.environ, {'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor'})
    def test_missing_task_token(self) -> None:
        """Test error when task token is missing from event"""
        event_without_token = {
            'action': 'synchronize',
            'number': 4
        }

        response = lambda_handler(event_without_token, self.mock_context)

        self.assertEqual(response['statusCode'], 400)
        self.assertEqual(response['error'], 'taskToken not found in event')

    @unittest.mock.patch.dict(os.environ, {'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_lambda_invoke_failure(self, mock_boto_client: typing.Any) -> None:
        """Test error handling when Lambda invoke fails"""
        # Mock Lambda client to raise exception
        mock_lambda = unittest.mock.MagicMock()
        mock_lambda.invoke.side_effect = Exception('Lambda service unavailable')
        mock_boto_client.return_value = mock_lambda

        response = lambda_handler(self.state_machine_event, self.mock_context)

        self.assertEqual(response['statusCode'], 500)
        self.assertIn('Failed to invoke processor', response['error'])
        self.assertIn('Lambda service unavailable', response['error'])

    @unittest.mock.patch.dict(os.environ, {'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_writes_to_status_html_for_first_task(self, mock_boto_client: typing.Any) -> None:
        """Test that status.html is written when taskSequence='first'"""
        # Mock Lambda client
        mock_lambda = unittest.mock.MagicMock()
        mock_lambda.invoke.return_value = {'StatusCode': 202}

        # Mock S3 client
        mock_s3 = unittest.mock.MagicMock()
        mock_s3.get_bucket_location.return_value = {'LocationConstraint': 'us-west-2'}

        # Configure boto3.client to return the appropriate mock
        def client_factory(service_name: str) -> typing.Any:
            if service_name == 'lambda':
                return mock_lambda
            elif service_name == 's3':
                return mock_s3
            return unittest.mock.MagicMock()

        mock_boto_client.side_effect = client_factory

        # Execute handler with taskSequence='first'
        response = lambda_handler(self.state_machine_event, self.mock_context)

        # Verify success
        self.assertEqual(response['statusCode'], 200)

        # Verify S3 put_object was called
        mock_s3.put_object.assert_called_once()
        call_kwargs = mock_s3.put_object.call_args[1]
        self.assertEqual(call_kwargs['Bucket'], 'test-bucket')
        self.assertEqual(call_kwargs['Key'], 'test-path/status.html')
        self.assertEqual(call_kwargs['Body'], b'Starting first check.')
        self.assertEqual(call_kwargs['ContentType'], 'text/html')

    @unittest.mock.patch.dict(os.environ, {'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_writes_to_status_html_for_second_task(self, mock_boto_client: typing.Any) -> None:
        """Test that status.html is written with second-task message when taskSequence='second'"""
        # Mock Lambda client
        mock_lambda = unittest.mock.MagicMock()
        mock_lambda.invoke.return_value = {'StatusCode': 202}

        # Mock S3 client
        mock_s3 = unittest.mock.MagicMock()

        # Configure boto3.client to return the appropriate mock
        def client_factory(service_name: str) -> typing.Any:
            if service_name == 'lambda':
                return mock_lambda
            elif service_name == 's3':
                return mock_s3
            return unittest.mock.MagicMock()

        mock_boto_client.side_effect = client_factory

        # Modify event to have taskSequence='second'
        event = self.state_machine_event.copy()
        event['taskSequence'] = 'second'

        # Execute handler
        response = lambda_handler(event, self.mock_context)

        # Verify success
        self.assertEqual(response['statusCode'], 200)

        # Verify S3 put_object was called with second-task message
        mock_s3.put_object.assert_called_once()
        call_kwargs = mock_s3.put_object.call_args[1]
        self.assertEqual(call_kwargs['Key'], 'test-path/status.html')
        self.assertEqual(call_kwargs['Body'], b'First check looks fine. Starting next check.')

    @unittest.mock.patch.dict(os.environ, {'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_does_not_write_to_status_html_without_destination(self, mock_boto_client: typing.Any) -> None:
        """Test that status.html is NOT written when destination is missing"""
        # Mock Lambda client
        mock_lambda = unittest.mock.MagicMock()
        mock_lambda.invoke.return_value = {'StatusCode': 202}

        # Mock S3 client
        mock_s3 = unittest.mock.MagicMock()

        # Configure boto3.client to return the appropriate mock
        def client_factory(service_name: str) -> typing.Any:
            if service_name == 'lambda':
                return mock_lambda
            elif service_name == 's3':
                return mock_s3
            return unittest.mock.MagicMock()

        mock_boto_client.side_effect = client_factory

        # Modify event to remove destination
        event = self.state_machine_event.copy()
        del event['destination']

        # Execute handler
        response = lambda_handler(event, self.mock_context)

        # Verify success
        self.assertEqual(response['statusCode'], 200)

        # Verify S3 put_object was NOT called
        mock_s3.put_object.assert_not_called()


    @unittest.mock.patch.dict(os.environ, {
        'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor',
        'GITHUB_SECRET_ARN': 'arn:aws:secretsmanager:us-west-2:123456789012:secret:github-token'
    })
    @unittest.mock.patch('boto3.client')
    @unittest.mock.patch('urllib.request.urlopen')
    def test_second_task_cancels_when_stale(
        self, mock_urlopen: typing.Any, mock_boto_client: typing.Any
    ) -> None:
        """Test that SupersededCommit is raised and status POST made when a newer commit exists"""
        mock_lambda = unittest.mock.MagicMock()
        mock_s3 = unittest.mock.MagicMock()
        mock_secrets = unittest.mock.MagicMock()
        mock_secrets.get_secret_value.return_value = {'SecretString': 'test-token'}

        def client_factory(service_name: str) -> typing.Any:
            if service_name == 'lambda':
                return mock_lambda
            elif service_name == 's3':
                return mock_s3
            elif service_name == 'secretsmanager':
                return mock_secrets
            return unittest.mock.MagicMock()

        mock_boto_client.side_effect = client_factory

        # First urlopen call returns commits (GET), second is status POST
        commits_response = unittest.mock.MagicMock()
        commits_response.read.return_value = json.dumps([
            {'sha': 'aaaaaa'},
            {'sha': 'newer-sha-9999'}  # different from our_sha
        ]).encode()
        commits_response.__enter__ = lambda s: s
        commits_response.__exit__ = unittest.mock.Mock(return_value=False)

        status_response = unittest.mock.MagicMock()
        status_response.read.return_value = b'{}'
        status_response.__enter__ = lambda s: s
        status_response.__exit__ = unittest.mock.Mock(return_value=False)

        mock_urlopen.side_effect = [commits_response, status_response]

        event = self.state_machine_event.copy()
        event['taskSequence'] = 'second'
        event['repository'] = {
            'full_name': 'migurski/boundary-issues',
            'statuses_url': 'https://api.github.com/repos/migurski/boundary-issues/statuses/{sha}'
        }

        with self.assertRaises(SupersededCommit):
            lambda_handler(event, self.mock_context)

        # Verify two urlopen calls: GET commits, POST status
        self.assertEqual(mock_urlopen.call_count, 2)
        # Verify processor was NOT invoked
        mock_lambda.invoke.assert_not_called()

    @unittest.mock.patch.dict(os.environ, {
        'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor',
        'GITHUB_SECRET_ARN': 'arn:aws:secretsmanager:us-west-2:123456789012:secret:github-token'
    })
    @unittest.mock.patch('boto3.client')
    @unittest.mock.patch('urllib.request.urlopen')
    def test_second_task_proceeds_when_current(
        self, mock_urlopen: typing.Any, mock_boto_client: typing.Any
    ) -> None:
        """Test that processor is invoked normally when our commit is still the latest"""
        mock_lambda = unittest.mock.MagicMock()
        mock_lambda.invoke.return_value = {'StatusCode': 202}
        mock_s3 = unittest.mock.MagicMock()
        mock_secrets = unittest.mock.MagicMock()
        mock_secrets.get_secret_value.return_value = {'SecretString': 'test-token'}

        def client_factory(service_name: str) -> typing.Any:
            if service_name == 'lambda':
                return mock_lambda
            elif service_name == 's3':
                return mock_s3
            elif service_name == 'secretsmanager':
                return mock_secrets
            return unittest.mock.MagicMock()

        mock_boto_client.side_effect = client_factory

        our_sha = typing.cast(dict[str, typing.Any], self.state_machine_event['pull_request'])['head']['sha']
        commits_response = unittest.mock.MagicMock()
        commits_response.read.return_value = json.dumps([
            {'sha': 'older-sha'},
            {'sha': our_sha}  # matches — we are current
        ]).encode()
        commits_response.__enter__ = lambda s: s
        commits_response.__exit__ = unittest.mock.Mock(return_value=False)

        mock_urlopen.return_value = commits_response

        event = self.state_machine_event.copy()
        event['taskSequence'] = 'second'
        event['repository'] = {
            'full_name': 'migurski/boundary-issues',
            'statuses_url': 'https://api.github.com/repos/migurski/boundary-issues/statuses/{sha}'
        }

        response = lambda_handler(event, self.mock_context)

        self.assertEqual(response['statusCode'], 200)
        mock_lambda.invoke.assert_called_once()

    @unittest.mock.patch.dict(os.environ, {
        'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor',
    })
    @unittest.mock.patch('boto3.client')
    @unittest.mock.patch('urllib.request.urlopen')
    def test_first_task_skips_staleness_check(
        self, mock_urlopen: typing.Any, mock_boto_client: typing.Any
    ) -> None:
        """Test that staleness check is not performed for the first task"""
        mock_lambda = unittest.mock.MagicMock()
        mock_lambda.invoke.return_value = {'StatusCode': 202}
        mock_s3 = unittest.mock.MagicMock()

        def client_factory(service_name: str) -> typing.Any:
            if service_name == 'lambda':
                return mock_lambda
            elif service_name == 's3':
                return mock_s3
            return unittest.mock.MagicMock()

        mock_boto_client.side_effect = client_factory

        response = lambda_handler(self.state_machine_event, self.mock_context)

        self.assertEqual(response['statusCode'], 200)
        # No GitHub API calls for first task
        mock_urlopen.assert_not_called()
        mock_lambda.invoke.assert_called_once()


if __name__ == '__main__':
    unittest.main()
