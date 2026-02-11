import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """
    AWS Lambda handler for GitHub webhook events.
    Logs the complete webhook payload and returns a 200 OK response.
    """
    logger.info("Received webhook event")
    logger.info(f"Event: {json.dumps(event, indent=2)}")

    # Log headers if available
    if 'headers' in event:
        logger.info(f"Headers: {json.dumps(event['headers'], indent=2)}")

    # Log body if available
    if 'body' in event:
        logger.info(f"Body: {event['body']}")

        # Try to parse body as JSON for better logging
        try:
            if event.get('isBase64Encoded', False):
                logger.info("Body is base64 encoded")
            else:
                body_json = json.loads(event['body'])
                logger.info(f"Parsed body: {json.dumps(body_json, indent=2)}")
        except (json.JSONDecodeError, TypeError):
            logger.info("Body is not valid JSON")

    # Return success response
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json'
        },
        'body': json.dumps({
            'message': 'Webhook received successfully'
        })
    }
