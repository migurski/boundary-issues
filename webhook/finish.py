import json
import logging
import os
import unittest
import unittest.mock
import urllib.request
import urllib.error

# Note: boto3 is available in AWS Lambda runtime
# For local testing, install via: pip install boto3
import boto3

# Configure logging
logging.basicConfig(format='%(levelname)s: %(message)s')
logging.getLogger().setLevel(logging.INFO)


def lambda_handler(event, context):
    """
    Finish Lambda handler that updates GitHub PR status with state machine result.
    Called by the state machine after task completion (success or failure).
    """
    logging.info(f"Received event: {json.dumps(event)}")

    # Get GitHub secret ARN from environment
    github_secret_arn = os.environ.get('GITHUB_SECRET_ARN')
    if not github_secret_arn:
        logging.error("GITHUB_SECRET_ARN environment variable not set")
        return {
            'statusCode': 500,
            'error': 'GITHUB_SECRET_ARN not configured'
        }

    # Extract required fields from event
    status_state = event.get('status', 'failure')  # 'success' or 'failure'
    execution_arn = event.get('executionArn')
    statuses_url = event.get('repository', {}).get('statuses_url')
    head_sha = event.get('pull_request', {}).get('head', {}).get('sha')

    if not statuses_url:
        logging.error("repository.statuses_url not found in event")
        return {
            'statusCode': 400,
            'error': 'repository.statuses_url not found in event'
        }

    if not head_sha:
        logging.error("pull_request.head.sha not found in event")
        return {
            'statusCode': 400,
            'error': 'pull_request.head.sha not found in event'
        }

    # Construct AWS console URL for the execution
    if execution_arn:
        # Extract region from ARN: arn:aws:states:REGION:...
        arn_parts = execution_arn.split(':')
        region = arn_parts[3] if len(arn_parts) > 3 else 'us-west-2'
        target_url = f"https://{region}.console.aws.amazon.com/states/home?region={region}#/v2/executions/details/{execution_arn}"
    else:
        target_url = None

    # Set description based on status
    if status_state == 'success':
        description = 'Boundary issues check passed'
    else:
        description = 'Boundary issues check failed'

    # Fetch GitHub token from Secrets Manager
    try:
        secrets_client = boto3.client('secretsmanager')
        secret_response = secrets_client.get_secret_value(SecretId=github_secret_arn)
        github_token = secret_response['SecretString']
        logging.info("Successfully retrieved GitHub token from Secrets Manager")
    except Exception as e:
        logging.error(f"Failed to retrieve GitHub token: {e}")
        return {
            'statusCode': 500,
            'error': f'Failed to retrieve GitHub token: {str(e)}'
        }

    # Replace {sha} placeholder in statuses_url with actual SHA
    status_api_url = statuses_url.replace('{sha}', head_sha)
    logging.info(f"Status API URL: {status_api_url}")

    # Create GitHub status
    status_payload = {
        'state': status_state,
        'description': description,
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

            return {
                'statusCode': 200,
                'message': f'GitHub status updated to {status_state}'
            }

    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        logging.error(f"GitHub API request failed: {e.code} {e.reason}")
        logging.error(f"Response body: {error_body}")
        return {
            'statusCode': 500,
            'error': f'GitHub API request failed: {e.code} {e.reason}'
        }
    except Exception as e:
        logging.error(f"Failed to create GitHub status: {e}")
        return {
            'statusCode': 500,
            'error': f'Failed to create GitHub status: {str(e)}'
        }


class TestLambdaHandler(unittest.TestCase):
    """
    Unit tests for the finish Lambda handler.

    These tests validate:
    - GITHUB_SECRET_ARN must be set in environment
    - Required fields must be present in event (statuses_url, head.sha)
    - GitHub token is fetched from Secrets Manager
    - GitHub status is created via API with correct parameters
    - AWS console URL is constructed correctly from execution ARN
    - Success and failure statuses are handled appropriately
    """

    def setUp(self):
        """Set up test fixtures"""
        self.test_github_secret_arn = 'arn:aws:secretsmanager:us-west-2:123456789012:secret:github-token-abc123'
        self.test_github_token = 'ghp_test1234567890abcdefghijklmnopqrstuvwxyz'
        self.test_execution_arn = 'arn:aws:states:us-west-2:101696101272:execution:boundary-issues-webhook-processor:pr-4-9e59f95b'

        # Mock context object
        self.mock_context = unittest.mock.Mock()
        self.mock_context.aws_request_id = '12345678-1234-1234-1234-123456789012'

        # Sample success event
        self.success_event = {
            'status': 'success',
            'executionArn': self.test_execution_arn,
            'action': 'synchronize',
            'number': 4,
            'pull_request': {
                'diff_url': 'https://github.com/migurski/boundary-issues/pull/4.diff',
                'base': {'sha': 'db7adabab3c93cf4c05f35c1df2b716596f82faa'},
                'head': {'sha': 'f6400f99d7e2094ccd2034c47f72820cef488a1f'}
            },
            'repository': {
                'full_name': 'migurski/boundary-issues',
                'statuses_url': 'https://api.github.com/repos/migurski/boundary-issues/statuses/{sha}'
            }
        }

        # Sample failure event
        self.failure_event = self.success_event.copy()
        self.failure_event['status'] = 'failure'

    @unittest.mock.patch.dict(os.environ, {'GITHUB_SECRET_ARN': 'arn:aws:secretsmanager:us-west-2:123456789012:secret:github-token-abc123'})
    @unittest.mock.patch('urllib.request.urlopen')
    @unittest.mock.patch('boto3.client')
    def test_successful_status_update(self, mock_boto_client, mock_urlopen):
        """Test successful GitHub status update with success state"""
        # Mock Secrets Manager client
        mock_secrets = unittest.mock.MagicMock()
        mock_secrets.get_secret_value.return_value = {
            'SecretString': self.test_github_token
        }
        mock_boto_client.return_value = mock_secrets

        # Mock urllib response
        mock_response = unittest.mock.MagicMock()
        mock_response.read.return_value = json.dumps({'state': 'success'}).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_response

        # Execute handler
        response = lambda_handler(self.success_event, self.mock_context)

        # Verify response
        self.assertEqual(response['statusCode'], 200)
        self.assertIn('GitHub status updated to success', response['message'])

        # Verify Secrets Manager was called
        mock_boto_client.assert_called_once_with('secretsmanager')
        mock_secrets.get_secret_value.assert_called_once_with(
            SecretId=self.test_github_secret_arn
        )

        # Verify GitHub API was called
        mock_urlopen.assert_called_once()

    @unittest.mock.patch.dict(os.environ, {'GITHUB_SECRET_ARN': 'arn:aws:secretsmanager:us-west-2:123456789012:secret:github-token-abc123'})
    @unittest.mock.patch('urllib.request.urlopen')
    @unittest.mock.patch('boto3.client')
    def test_failure_status_update(self, mock_boto_client, mock_urlopen):
        """Test GitHub status update with failure state"""
        # Mock Secrets Manager client
        mock_secrets = unittest.mock.MagicMock()
        mock_secrets.get_secret_value.return_value = {
            'SecretString': self.test_github_token
        }
        mock_boto_client.return_value = mock_secrets

        # Mock urllib response
        mock_response = unittest.mock.MagicMock()
        mock_response.read.return_value = json.dumps({'state': 'failure'}).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_response

        # Execute handler
        response = lambda_handler(self.failure_event, self.mock_context)

        # Verify response
        self.assertEqual(response['statusCode'], 200)
        self.assertIn('GitHub status updated to failure', response['message'])

    @unittest.mock.patch.dict(os.environ, {'GITHUB_SECRET_ARN': 'arn:aws:secretsmanager:us-west-2:123456789012:secret:github-token-abc123'})
    @unittest.mock.patch('urllib.request.urlopen')
    @unittest.mock.patch('boto3.client')
    def test_github_api_payload(self, mock_boto_client, mock_urlopen):
        """Test that GitHub API is called with correct payload"""
        # Mock Secrets Manager client
        mock_secrets = unittest.mock.MagicMock()
        mock_secrets.get_secret_value.return_value = {
            'SecretString': self.test_github_token
        }
        mock_boto_client.return_value = mock_secrets

        # Mock urllib response
        mock_response = unittest.mock.MagicMock()
        mock_response.read.return_value = json.dumps({'state': 'success'}).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_response

        # Execute handler
        lambda_handler(self.success_event, self.mock_context)

        # Verify GitHub API call
        call_args = mock_urlopen.call_args[0]
        request = call_args[0]

        # Verify URL (should have {sha} replaced)
        expected_url = 'https://api.github.com/repos/migurski/boundary-issues/statuses/f6400f99d7e2094ccd2034c47f72820cef488a1f'
        self.assertEqual(request.full_url, expected_url)

        # Verify headers
        self.assertEqual(request.get_header('Authorization'), f'token {self.test_github_token}')
        self.assertEqual(request.get_header('Content-type'), 'application/json')

        # Verify payload
        payload = json.loads(request.data.decode('utf-8'))
        self.assertEqual(payload['state'], 'success')
        self.assertEqual(payload['context'], 'boundary-issues-processor')
        self.assertEqual(payload['description'], 'Boundary issues check passed')
        self.assertIn('console.aws.amazon.com', payload['target_url'])
        self.assertIn(self.test_execution_arn, payload['target_url'])

    @unittest.mock.patch.dict(os.environ, {'GITHUB_SECRET_ARN': 'arn:aws:secretsmanager:us-west-2:123456789012:secret:github-token-abc123'})
    @unittest.mock.patch('urllib.request.urlopen')
    @unittest.mock.patch('boto3.client')
    def test_console_url_construction(self, mock_boto_client, mock_urlopen):
        """Test AWS console URL is constructed correctly from execution ARN"""
        # Mock Secrets Manager client
        mock_secrets = unittest.mock.MagicMock()
        mock_secrets.get_secret_value.return_value = {
            'SecretString': self.test_github_token
        }
        mock_boto_client.return_value = mock_secrets

        # Mock urllib response
        mock_response = unittest.mock.MagicMock()
        mock_response.read.return_value = json.dumps({'state': 'success'}).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_response

        # Execute handler
        lambda_handler(self.success_event, self.mock_context)

        # Verify console URL format
        call_args = mock_urlopen.call_args[0]
        request = call_args[0]
        payload = json.loads(request.data.decode('utf-8'))

        expected_url = f'https://us-west-2.console.aws.amazon.com/states/home?region=us-west-2#/v2/executions/details/{self.test_execution_arn}'
        self.assertEqual(payload['target_url'], expected_url)

    @unittest.mock.patch.dict(os.environ, {}, clear=True)
    def test_missing_github_secret_arn(self):
        """Test error when GITHUB_SECRET_ARN environment variable is not set"""
        response = lambda_handler(self.success_event, self.mock_context)

        self.assertEqual(response['statusCode'], 500)
        self.assertEqual(response['error'], 'GITHUB_SECRET_ARN not configured')

    @unittest.mock.patch.dict(os.environ, {'GITHUB_SECRET_ARN': 'arn:aws:secretsmanager:us-west-2:123456789012:secret:github-token-abc123'})
    def test_missing_statuses_url(self):
        """Test error when repository.statuses_url is missing from event"""
        event_without_statuses_url = {
            'status': 'success',
            'executionArn': self.test_execution_arn,
            'pull_request': {
                'head': {'sha': 'abc123'}
            },
            'repository': {
                'full_name': 'migurski/boundary-issues'
            }
        }

        response = lambda_handler(event_without_statuses_url, self.mock_context)

        self.assertEqual(response['statusCode'], 400)
        self.assertEqual(response['error'], 'repository.statuses_url not found in event')

    @unittest.mock.patch.dict(os.environ, {'GITHUB_SECRET_ARN': 'arn:aws:secretsmanager:us-west-2:123456789012:secret:github-token-abc123'})
    def test_missing_head_sha(self):
        """Test error when pull_request.head.sha is missing from event"""
        event_without_sha = {
            'status': 'success',
            'executionArn': self.test_execution_arn,
            'pull_request': {},
            'repository': {
                'statuses_url': 'https://api.github.com/repos/migurski/boundary-issues/statuses/{sha}'
            }
        }

        response = lambda_handler(event_without_sha, self.mock_context)

        self.assertEqual(response['statusCode'], 400)
        self.assertEqual(response['error'], 'pull_request.head.sha not found in event')

    @unittest.mock.patch.dict(os.environ, {'GITHUB_SECRET_ARN': 'arn:aws:secretsmanager:us-west-2:123456789012:secret:github-token-abc123'})
    @unittest.mock.patch('boto3.client')
    def test_secrets_manager_failure(self, mock_boto_client):
        """Test error handling when Secrets Manager fails"""
        # Mock Secrets Manager client to raise exception
        mock_secrets = unittest.mock.MagicMock()
        mock_secrets.get_secret_value.side_effect = Exception('Secrets Manager unavailable')
        mock_boto_client.return_value = mock_secrets

        response = lambda_handler(self.success_event, self.mock_context)

        self.assertEqual(response['statusCode'], 500)
        self.assertIn('Failed to retrieve GitHub token', response['error'])
        self.assertIn('Secrets Manager unavailable', response['error'])

    @unittest.mock.patch.dict(os.environ, {'GITHUB_SECRET_ARN': 'arn:aws:secretsmanager:us-west-2:123456789012:secret:github-token-abc123'})
    @unittest.mock.patch('urllib.request.urlopen')
    @unittest.mock.patch('boto3.client')
    def test_github_api_failure(self, mock_boto_client, mock_urlopen):
        """Test error handling when GitHub API request fails"""
        # Mock Secrets Manager client
        mock_secrets = unittest.mock.MagicMock()
        mock_secrets.get_secret_value.return_value = {
            'SecretString': self.test_github_token
        }
        mock_boto_client.return_value = mock_secrets

        # Mock urllib to raise HTTPError
        mock_error = urllib.error.HTTPError(
            'https://api.github.com',
            401,
            'Unauthorized',
            {},
            unittest.mock.MagicMock()
        )
        mock_error.read = lambda: b'{"message": "Bad credentials"}'
        mock_urlopen.side_effect = mock_error

        response = lambda_handler(self.success_event, self.mock_context)

        self.assertEqual(response['statusCode'], 500)
        self.assertIn('GitHub API request failed', response['error'])

    @unittest.mock.patch.dict(os.environ, {'GITHUB_SECRET_ARN': 'arn:aws:secretsmanager:us-west-2:123456789012:secret:github-token-abc123'})
    @unittest.mock.patch('urllib.request.urlopen')
    @unittest.mock.patch('boto3.client')
    def test_event_without_execution_arn(self, mock_boto_client, mock_urlopen):
        """Test that handler works even without execution ARN (no target_url)"""
        # Mock Secrets Manager client
        mock_secrets = unittest.mock.MagicMock()
        mock_secrets.get_secret_value.return_value = {
            'SecretString': self.test_github_token
        }
        mock_boto_client.return_value = mock_secrets

        # Mock urllib response
        mock_response = unittest.mock.MagicMock()
        mock_response.read.return_value = json.dumps({'state': 'success'}).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_response

        # Event without executionArn
        event_without_arn = self.success_event.copy()
        del event_without_arn['executionArn']

        # Execute handler
        response = lambda_handler(event_without_arn, self.mock_context)

        # Verify response
        self.assertEqual(response['statusCode'], 200)

        # Verify payload doesn't include target_url
        call_args = mock_urlopen.call_args[0]
        request = call_args[0]
        payload = json.loads(request.data.decode('utf-8'))
        self.assertNotIn('target_url', payload)

    @unittest.mock.patch.dict(os.environ, {'GITHUB_SECRET_ARN': 'arn:aws:secretsmanager:us-west-2:123456789012:secret:github-token-abc123'})
    @unittest.mock.patch('urllib.request.urlopen')
    @unittest.mock.patch('boto3.client')
    def test_default_to_failure_status(self, mock_boto_client, mock_urlopen):
        """Test that status defaults to 'failure' if not specified"""
        # Mock Secrets Manager client
        mock_secrets = unittest.mock.MagicMock()
        mock_secrets.get_secret_value.return_value = {
            'SecretString': self.test_github_token
        }
        mock_boto_client.return_value = mock_secrets

        # Mock urllib response
        mock_response = unittest.mock.MagicMock()
        mock_response.read.return_value = json.dumps({'state': 'failure'}).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_response

        # Event without status field
        event_without_status = self.success_event.copy()
        del event_without_status['status']

        # Execute handler
        response = lambda_handler(event_without_status, self.mock_context)

        # Verify response
        self.assertEqual(response['statusCode'], 200)

        # Verify payload uses 'failure' as default
        call_args = mock_urlopen.call_args[0]
        request = call_args[0]
        payload = json.loads(request.data.decode('utf-8'))
        self.assertEqual(payload['state'], 'failure')


if __name__ == '__main__':
    unittest.main()
