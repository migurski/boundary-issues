#!/usr/bin/env python3
"""
AWS Lambda handler for executing build-country-polygon.py in response to Step Functions.
This handler downloads necessary files from S3, executes the boundary processing script,
and uploads results back to S3.
"""
import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

import boto3

# Initialize S3 client
s3_client = boto3.client('s3')

# Environment variables (set in CloudFormation)
DATA_BUCKET = os.environ.get('DATA_BUCKET', '')
OUTPUT_BUCKET = os.environ.get('OUTPUT_BUCKET', DATA_BUCKET)


def download_from_s3(bucket, key, local_path):
    """Download a file from S3 to local path."""
    print(f"Downloading s3://{bucket}/{key} to {local_path}")
    s3_client.download_file(bucket, key, local_path)
    return local_path


def upload_to_s3(local_path, bucket, key):
    """Upload a file from local path to S3."""
    print(f"Uploading {local_path} to s3://{bucket}/{key}")
    s3_client.upload_file(local_path, bucket, key)
    return f"s3://{bucket}/{key}"


def lambda_handler(event, context):
    """
    Lambda handler function invoked by Step Functions.

    Expected event format:
    {
        "config_bucket": "bucket-name",
        "config_key": "path/to/config.yaml",
        "boundaries_bucket": "bucket-name" (optional, defaults to config_bucket),
        "boundaries_key": "country-boundaries.csv" (optional),
        "areas_bucket": "bucket-name" (optional, defaults to config_bucket),
        "areas_key": "country-areas.csv" (optional),
        "output_bucket": "bucket-name" (optional, defaults to OUTPUT_BUCKET env var),
        "output_prefix": "output/" (optional, defaults to empty string)
    }

    Returns:
    {
        "statusCode": 200,
        "body": {
            "message": "Success",
            "outputs": ["s3://bucket/key1", "s3://bucket/key2"]
        }
    }
    """
    print(f"Received event: {json.dumps(event)}")

    try:
        # Parse event parameters
        config_bucket = event.get('config_bucket', DATA_BUCKET)
        config_key = event.get('config_key', 'config.yaml')
        boundaries_bucket = event.get('boundaries_bucket', config_bucket)
        boundaries_key = event.get('boundaries_key', 'country-boundaries.csv')
        areas_bucket = event.get('areas_bucket', config_bucket)
        areas_key = event.get('areas_key', 'country-areas.csv')
        output_bucket = event.get('output_bucket', OUTPUT_BUCKET or config_bucket)
        output_prefix = event.get('output_prefix', '')

        if not config_bucket:
            raise ValueError("config_bucket must be specified in event or DATA_BUCKET environment variable")

        # Create temporary working directory
        with tempfile.TemporaryDirectory() as tmpdir:
            print(f"Working directory: {tmpdir}")
            os.chdir(tmpdir)

            # Download required files from S3
            config_path = download_from_s3(config_bucket, config_key, 'config.yaml')
            boundaries_path = download_from_s3(boundaries_bucket, boundaries_key, 'country-boundaries.csv')
            areas_path = download_from_s3(areas_bucket, areas_key, 'country-areas.csv')

            # Locate build-country-polygon.py script
            script_path = '/var/task/build-country-polygon.py'
            if not os.path.exists(script_path):
                raise FileNotFoundError(f"Script not found at {script_path}")

            # Execute the build-country-polygon.py script
            print(f"Executing {script_path}")
            result = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                timeout=270  # 4.5 minutes (leave buffer for Lambda timeout)
            )

            # Log output
            print("=== STDOUT ===")
            print(result.stdout)
            print("=== STDERR ===")
            print(result.stderr)

            if result.returncode != 0:
                raise RuntimeError(f"Script failed with return code {result.returncode}")

            # Upload generated outputs to S3
            outputs = []
            output_files = [
                'country-boundaries.csv',
                'country-areas.csv'
            ]

            for filename in output_files:
                if os.path.exists(filename):
                    s3_key = f"{output_prefix}{filename}" if output_prefix else filename
                    s3_uri = upload_to_s3(filename, output_bucket, s3_key)
                    outputs.append(s3_uri)
                else:
                    print(f"Warning: Expected output file {filename} not found")

            # Look for any other generated files (GeoJSON, etc.)
            for filepath in Path('.').glob('*.geojson'):
                s3_key = f"{output_prefix}{filepath.name}" if output_prefix else filepath.name
                s3_uri = upload_to_s3(str(filepath), output_bucket, s3_key)
                outputs.append(s3_uri)

            return {
                'statusCode': 200,
                'body': {
                    'message': 'Successfully processed country boundaries',
                    'outputs': outputs,
                    'config_used': f"s3://{config_bucket}/{config_key}"
                }
            }

    except Exception as e:
        print(f"Error: {str(e)}")
        print(traceback.format_exc())
        return {
            'statusCode': 500,
            'body': {
                'message': 'Error processing country boundaries',
                'error': str(e),
                'traceback': traceback.format_exc()
            }
        }
