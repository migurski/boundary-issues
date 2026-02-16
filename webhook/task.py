import json
import logging
import os
import unittest
import unittest.mock

# Note: boto3 is available in AWS Lambda runtime
# For local testing, install via: pip install boto3
import boto3

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


def lambda_handler(event, context):
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
    processor_payload = event.copy()

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

    def setUp(self):
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
            }
        }

    @unittest.mock.patch.dict(os.environ, {'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_invokes_processor_async(self, mock_boto_client):
        """Test that processor is invoked asynchronously with Event invocation type"""
        # Mock Lambda client
        mock_lambda = unittest.mock.MagicMock()
        mock_lambda.invoke.return_value = {
            'StatusCode': 202
        }
        mock_boto_client.return_value = mock_lambda

        # Execute handler
        response = lambda_handler(self.state_machine_event, self.mock_context)

        # Verify response
        self.assertEqual(response['statusCode'], 200)
        self.assertEqual(response['message'], 'Processor invoked asynchronously')

        # Verify Lambda client was called correctly
        mock_boto_client.assert_called_once_with('lambda')
        mock_lambda.invoke.assert_called_once()

        call_args = mock_lambda.invoke.call_args[1]
        self.assertEqual(call_args['FunctionName'], self.test_processor_arn)
        self.assertEqual(call_args['InvocationType'], 'Event')  # Async invocation

    @unittest.mock.patch.dict(os.environ, {'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_passes_task_token_to_processor(self, mock_boto_client):
        """Test that task token is passed through to processor"""
        # Mock Lambda client
        mock_lambda = unittest.mock.MagicMock()
        mock_lambda.invoke.return_value = {
            'StatusCode': 202
        }
        mock_boto_client.return_value = mock_lambda

        # Execute handler
        response = lambda_handler(self.state_machine_event, self.mock_context)

        # Verify task token was passed to processor
        call_args = mock_lambda.invoke.call_args[1]
        payload = json.loads(call_args['Payload'])
        self.assertEqual(payload['taskToken'], self.test_task_token)

    @unittest.mock.patch.dict(os.environ, {'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_passes_through_event_fields(self, mock_boto_client):
        """Test that all event fields are passed through to processor"""
        # Mock Lambda client
        mock_lambda = unittest.mock.MagicMock()
        mock_lambda.invoke.return_value = {
            'StatusCode': 202
        }
        mock_boto_client.return_value = mock_lambda

        # Execute handler
        response = lambda_handler(self.state_machine_event, self.mock_context)

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
    def test_returns_immediately(self, mock_boto_client):
        """Test that handler returns immediately after async invocation"""
        # Mock Lambda client
        mock_lambda = unittest.mock.MagicMock()
        mock_lambda.invoke.return_value = {
            'StatusCode': 202  # Accepted for async invocation
        }
        mock_boto_client.return_value = mock_lambda

        # Execute handler
        response = lambda_handler(self.state_machine_event, self.mock_context)

        # Verify immediate return with success
        self.assertEqual(response['statusCode'], 200)
        self.assertIn('message', response)

        # Verify async invocation was used (Event type)
        call_args = mock_lambda.invoke.call_args[1]
        self.assertEqual(call_args['InvocationType'], 'Event')

    @unittest.mock.patch.dict(os.environ, {}, clear=True)
    def test_missing_processor_arn_env(self):
        """Test error when PROCESSOR_FUNCTION_ARN environment variable is not set"""
        response = lambda_handler(self.state_machine_event, self.mock_context)

        self.assertEqual(response['statusCode'], 500)
        self.assertEqual(response['error'], 'PROCESSOR_FUNCTION_ARN not configured')

    @unittest.mock.patch.dict(os.environ, {'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor'})
    def test_missing_task_token(self):
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
    def test_lambda_invoke_failure(self, mock_boto_client):
        """Test error handling when Lambda invoke fails"""
        # Mock Lambda client to raise exception
        mock_lambda = unittest.mock.MagicMock()
        mock_lambda.invoke.side_effect = Exception('Lambda service unavailable')
        mock_boto_client.return_value = mock_lambda

        response = lambda_handler(self.state_machine_event, self.mock_context)

        self.assertEqual(response['statusCode'], 500)
        self.assertIn('Failed to invoke processor', response['error'])
        self.assertIn('Lambda service unavailable', response['error'])


if __name__ == '__main__':
    unittest.main()
