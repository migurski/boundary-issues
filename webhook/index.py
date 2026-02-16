import json
import os
import unittest
import unittest.mock

# Note: boto3 is available in AWS Lambda runtime
# For local testing, install via: pip install boto3
import boto3


def lambda_handler(event, context):
    """
    Webhook Lambda handler that receives GitHub events and triggers state machine.
    """
    print(f"Received event: {json.dumps(event)}")

    # Initialize Step Functions client
    sfn = boto3.client('stepfunctions')

    # Get state machine ARN from environment
    state_machine_arn = os.environ.get('STATE_MACHINE_ARN')
    if not state_machine_arn:
        print("ERROR: STATE_MACHINE_ARN environment variable not set")
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

        print(f"Parsed payload: {json.dumps(payload)}")

    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse request body: {e}")
        return {
            'statusCode': 400,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': 'Invalid JSON in request body'})
        }

    # Start state machine execution
    try:
        execution_name = f"pr-{payload.get('number', 'unknown')}-{context.aws_request_id[:8]}"
        print(f"Starting state machine execution: {execution_name}")

        response = sfn.start_execution(
            stateMachineArn=state_machine_arn,
            name=execution_name,
            input=json.dumps(payload)
        )

        execution_arn = response['executionArn']
        print(f"State machine execution started: {execution_arn}")

        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'message': 'State machine execution started',
                'executionArn': execution_arn
            })
        }

    except Exception as e:
        print(f"ERROR: Failed to start state machine execution: {e}")
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': f'Failed to start execution: {str(e)}'})
        }


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

    def setUp(self):
        """Set up test fixtures"""
        os.environ['AWS_REGION'] = 'us-west-2'
        self.test_state_machine_arn = 'arn:aws:states:us-west-2:123456789012:stateMachine:test-processor'
        self.test_execution_arn = 'arn:aws:states:us-west-2:123456789012:execution:test-processor:pr-4-12345678'

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
                    'diff_url': 'https://github.com/migurski/boundary-issues/pull/4.diff',
                    'base': {'sha': 'db7adabab3c93cf4c05f35c1df2b716596f82faa'},
                    'head': {'sha': 'f6400f99d7e2094ccd2034c47f72820cef488a1f'}
                }
            })
        }

    @unittest.mock.patch.dict(os.environ, {'STATE_MACHINE_ARN': 'arn:aws:states:us-west-2:123456789012:stateMachine:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_successful_execution(self, mock_boto_client):
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
        self.assertEqual(body['executionArn'], self.test_execution_arn)

        # Verify Step Functions client was called correctly
        mock_boto_client.assert_called_once_with('stepfunctions')
        mock_sfn.start_execution.assert_called_once()

        call_args = mock_sfn.start_execution.call_args[1]
        self.assertEqual(call_args['stateMachineArn'], self.test_state_machine_arn)
        self.assertEqual(call_args['name'], 'pr-4-12345678')

        # Verify payload was passed correctly
        input_payload = json.loads(call_args['input'])
        self.assertEqual(input_payload['action'], 'synchronize')
        self.assertEqual(input_payload['number'], 4)

    @unittest.mock.patch.dict(os.environ, {}, clear=True)
    def test_missing_state_machine_arn(self):
        """Test error when STATE_MACHINE_ARN environment variable is not set"""
        response = lambda_handler(self.github_pr_event, self.mock_context)

        self.assertEqual(response['statusCode'], 500)
        body = json.loads(response['body'])
        self.assertEqual(body['error'], 'STATE_MACHINE_ARN not configured')

    @unittest.mock.patch.dict(os.environ, {'STATE_MACHINE_ARN': 'arn:aws:states:us-west-2:123456789012:stateMachine:test-processor'})
    def test_invalid_json_body(self):
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
    def test_body_as_dict(self, mock_boto_client):
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
    def test_context_aws_request_id(self, mock_boto_client):
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
        response = lambda_handler(self.github_pr_event, self.mock_context)

        # Verify execution name uses aws_request_id
        call_args = mock_sfn.start_execution.call_args[1]
        execution_name = call_args['name']

        # Should be pr-4-12345678 (first 8 chars of aws_request_id)
        self.assertEqual(execution_name, 'pr-4-12345678')
        self.assertTrue(execution_name.startswith('pr-4-'))

    @unittest.mock.patch.dict(os.environ, {'STATE_MACHINE_ARN': 'arn:aws:states:us-west-2:123456789012:stateMachine:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_step_functions_failure(self, mock_boto_client):
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
    def test_execution_name_format(self, mock_boto_client):
        """Test execution name follows expected format: pr-{number}-{request_id[:8]}"""
        mock_sfn = unittest.mock.MagicMock()
        mock_sfn.start_execution.return_value = {
            'executionArn': self.test_execution_arn
        }
        mock_boto_client.return_value = mock_sfn

        # Test with specific PR number
        event = self.github_pr_event.copy()
        response = lambda_handler(event, self.mock_context)

        call_args = mock_sfn.start_execution.call_args[1]
        execution_name = call_args['name']

        # Verify format
        import re
        self.assertIsNotNone(re.match(r'^pr-\d+-[a-f0-9]{8}$', execution_name))

        # Test with missing PR number (should use 'unknown')
        event_no_pr = {
            'body': json.dumps({'action': 'opened'})
        }

        mock_sfn.reset_mock()
        response = lambda_handler(event_no_pr, self.mock_context)

        call_args = mock_sfn.start_execution.call_args[1]
        execution_name = call_args['name']
        self.assertTrue(execution_name.startswith('pr-unknown-'))


if __name__ == '__main__':
    unittest.main()
