import json
import logging
import os
import unittest
import unittest.mock
import urllib.parse

# Note: boto3 is available in AWS Lambda runtime
# For local testing, install via: pip install boto3
import boto3

# Configure logging
logging.basicConfig(format='%(levelname)s: %(message)s')
logging.getLogger().setLevel(logging.INFO)


def write_index_html(destination, message):
    """
    Write a message to index.html in the S3 destination.

    Args:
        destination: s3:// URL where results go
        message: Message to write to index.html
    """
    try:
        parsed_url = urllib.parse.urlparse(destination)
        s3_client = boto3.client('s3')
        region_name = s3_client.get_bucket_location(Bucket=parsed_url.netloc)['LocationConstraint']
        target_path = os.path.join(parsed_url.path, 'index.html')

        s3_client.put_object(
            Bucket=parsed_url.netloc,
            Key=target_path.lstrip('/'),
            ACL='public-read',
            ContentType='text/html',
            Body=message.encode('utf8'),
            StorageClass='INTELLIGENT_TIERING',
        )

        logging.info(f"Successfully wrote to index.html: {message}")

    except Exception as e:
        logging.error(f"Failed to write to index.html: {e}")
        # Don't fail the whole handler if S3 write fails
        pass


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
    # Convert taskSequence to ignoreLocals for processor
    task_sequence = event.get('taskSequence')
    processor_payload = event.copy()

    # Remove taskSequence and add ignoreLocals instead
    processor_payload.pop('taskSequence', None)
    if task_sequence == 'second':
        processor_payload['ignoreLocals'] = True

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
            logging.info("Writing 'Settling in for a long wait' to index.html")
            write_index_html(destination, 'Settling in for a long wait')

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
            },
            'destination': 's3://test-bucket/test-path/',
            'taskSequence': 'first'
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

        # Mock S3 client
        mock_s3 = unittest.mock.MagicMock()
        mock_s3.get_bucket_location.return_value = {'LocationConstraint': 'us-west-2'}

        # Configure boto3.client to return the appropriate mock
        def client_factory(service_name):
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
    def test_passes_task_token_to_processor(self, mock_boto_client):
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
        def client_factory(service_name):
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
    def test_passes_through_event_fields(self, mock_boto_client):
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
        def client_factory(service_name):
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
    def test_returns_immediately(self, mock_boto_client):
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
        def client_factory(service_name):
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

    @unittest.mock.patch.dict(os.environ, {'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_writes_to_index_html_for_first_task(self, mock_boto_client):
        """Test that index.html is written when taskSequence='first'"""
        # Mock Lambda client
        mock_lambda = unittest.mock.MagicMock()
        mock_lambda.invoke.return_value = {'StatusCode': 202}

        # Mock S3 client
        mock_s3 = unittest.mock.MagicMock()
        mock_s3.get_bucket_location.return_value = {'LocationConstraint': 'us-west-2'}

        # Configure boto3.client to return the appropriate mock
        def client_factory(service_name):
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
        self.assertEqual(call_kwargs['Key'], 'test-path/index.html')
        self.assertEqual(call_kwargs['Body'], b'Settling in for a long wait')
        self.assertEqual(call_kwargs['ContentType'], 'text/html')

    @unittest.mock.patch.dict(os.environ, {'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_does_not_write_to_index_html_for_second_task(self, mock_boto_client):
        """Test that index.html is NOT written when taskSequence='second'"""
        # Mock Lambda client
        mock_lambda = unittest.mock.MagicMock()
        mock_lambda.invoke.return_value = {'StatusCode': 202}

        # Mock S3 client
        mock_s3 = unittest.mock.MagicMock()

        # Configure boto3.client to return the appropriate mock
        def client_factory(service_name):
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

        # Verify S3 put_object was NOT called
        mock_s3.put_object.assert_not_called()

    @unittest.mock.patch.dict(os.environ, {'PROCESSOR_FUNCTION_ARN': 'arn:aws:lambda:us-west-2:123456789012:function:test-processor'})
    @unittest.mock.patch('boto3.client')
    def test_does_not_write_to_index_html_without_task_sequence(self, mock_boto_client):
        """Test that index.html is NOT written when taskSequence is missing"""
        # Mock Lambda client
        mock_lambda = unittest.mock.MagicMock()
        mock_lambda.invoke.return_value = {'StatusCode': 202}

        # Mock S3 client
        mock_s3 = unittest.mock.MagicMock()

        # Configure boto3.client to return the appropriate mock
        def client_factory(service_name):
            if service_name == 'lambda':
                return mock_lambda
            elif service_name == 's3':
                return mock_s3
            return unittest.mock.MagicMock()

        mock_boto_client.side_effect = client_factory

        # Modify event to remove taskSequence
        event = self.state_machine_event.copy()
        del event['taskSequence']

        # Execute handler
        response = lambda_handler(event, self.mock_context)

        # Verify success
        self.assertEqual(response['statusCode'], 200)

        # Verify S3 put_object was NOT called
        mock_s3.put_object.assert_not_called()


if __name__ == '__main__':
    unittest.main()
