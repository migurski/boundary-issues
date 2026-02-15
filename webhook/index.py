import boto3
import json
import os


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
        execution_name = f"pr-{payload.get('number', 'unknown')}-{context.request_id[:8]}"
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
