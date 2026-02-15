import boto3
import json
import subprocess
import os
import sys


def handler(event, context):
    """
    Docker Lambda handler that processes GitHub PR events.

    This function:
    1. Fetches GitHub token from AWS Secrets Manager
    2. Parses PR information from the event
    3. Clones the repository
    4. Checks out the PR HEAD commit
    5. Logs success/failure
    """
    print(f"Received event: {json.dumps(event)}")

    # Fetch GitHub token from Secrets Manager
    try:
        secrets_client = boto3.client('secretsmanager')
        secret_arn = os.environ.get('GITHUB_SECRET_ARN')

        if not secret_arn:
            raise ValueError("GITHUB_SECRET_ARN environment variable not set")

        print(f"Fetching secret from: {secret_arn}")
        secret_response = secrets_client.get_secret_value(SecretId=secret_arn)
        github_token = secret_response['SecretString']
        print("Successfully retrieved GitHub token from Secrets Manager")

    except Exception as e:
        print(f"ERROR: Failed to retrieve GitHub token: {e}")
        return {
            'statusCode': 500,
            'status': 'error',
            'error': f'Failed to retrieve GitHub token: {str(e)}'
        }

    # Extract PR information
    try:
        pull_request = event.get('pull_request', {})
        head_info = pull_request.get('head', {})
        pr_sha = head_info.get('sha')
        pr_number = event.get('number')
        diff_url = pull_request.get('diff_url', '')

        if not pr_sha:
            raise ValueError("No PR SHA found in event payload")

        # Extract repository from diff_url (e.g., https://github.com/owner/repo/pull/4.diff)
        import re
        match = re.match(r'https://github\.com/([^/]+)/([^/]+)/pull/', diff_url)
        if match:
            repo_owner = match.group(1)
            repo_name = match.group(2)
            repo_full_name = f"{repo_owner}/{repo_name}"
        else:
            # Fallback to hardcoded repo if we can't extract it
            repo_full_name = "migurski/boundary-issues"
            print(f"WARNING: Could not extract repo from diff_url: {diff_url}, using fallback: {repo_full_name}")

        print(f"Processing PR #{pr_number}, HEAD SHA: {pr_sha}, Repo: {repo_full_name}")

    except Exception as e:
        print(f"ERROR: Failed to parse PR information: {e}")
        return {
            'statusCode': 400,
            'status': 'error',
            'error': f'Failed to parse PR information: {str(e)}'
        }

    # Clone repository
    try:
        repo_url = f"https://{github_token}@github.com/{repo_full_name}.git"
        clone_dir = '/tmp/repo'

        # Clean up any previous clone
        subprocess.run(['rm', '-rf', clone_dir], check=False)

        print(f"Cloning repository to {clone_dir}")
        result = subprocess.run(
            ['git', 'clone', '--depth', '1', repo_url, clone_dir],
            capture_output=True,
            text=True,
            check=True
        )
        print(f"Clone output: {result.stdout}")

    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to clone repository: {e}")
        print(f"STDOUT: {e.stdout}")
        print(f"STDERR: {e.stderr}")
        return {
            'statusCode': 500,
            'status': 'error',
            'error': f'Failed to clone repository: {e.stderr}'
        }

    # Checkout PR HEAD
    try:
        print(f"Checking out commit {pr_sha}")
        result = subprocess.run(
            ['git', 'fetch', 'origin', pr_sha],
            cwd=clone_dir,
            capture_output=True,
            text=True,
            check=True
        )
        print(f"Fetch output: {result.stdout}")

        result = subprocess.run(
            ['git', 'checkout', pr_sha],
            cwd=clone_dir,
            capture_output=True,
            text=True,
            check=True
        )
        print(f"Checkout output: {result.stdout}")

        # Verify checkout
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=clone_dir,
            capture_output=True,
            text=True,
            check=True
        )
        current_sha = result.stdout.strip()
        print(f"Current HEAD: {current_sha}")

        if current_sha != pr_sha:
            raise ValueError(f"Checkout verification failed: expected {pr_sha}, got {current_sha}")

        print(f"Successfully checked out PR #{pr_number} at {pr_sha}")

    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to checkout commit: {e}")
        print(f"STDOUT: {e.stdout}")
        print(f"STDERR: {e.stderr}")
        return {
            'statusCode': 500,
            'status': 'error',
            'error': f'Failed to checkout commit: {e.stderr}'
        }
    except ValueError as e:
        print(f"ERROR: {e}")
        return {
            'statusCode': 500,
            'status': 'error',
            'error': str(e)
        }

    # Success!
    return {
        'statusCode': 200,
        'status': 'success',
        'pr_number': pr_number,
        'sha': pr_sha,
        'message': f'Successfully processed PR #{pr_number} at {pr_sha}'
    }
