from __future__ import annotations

import boto3
import json
import logging
import subprocess
import os
import typing
import urllib.parse

# Configure logging
logging.basicConfig(format='%(levelname)s: %(message)s')
logging.getLogger().setLevel(logging.INFO)


FailCallable = typing.Callable[[str, str], None]


def run_in(cmd: list[str], dirname: str) -> subprocess.CompletedProcess[str]:
    """ Run a command in a directory
    """
    return subprocess.run(cmd, cwd=dirname, capture_output=True, text=True, check=True)


def make_error(message: str) -> dict:
    """ Make a standard error dictionary
    """
    return {'statusCode': 500, 'status': 'error', 'error': message}


def handler(event: dict, context: typing.Any) -> dict:
    """
    Docker Lambda handler that processes GitHub PR events.

    This function:
    1. Fetches GitHub token from AWS Secrets Manager
    2. Parses PR information from the event
    3. Clones the repository
    4. Checks out the PR HEAD commit
    5. Logs success/failure
    6. Sends task success/failure to Step Functions (if taskToken present)
    """
    logging.info(f"Received event: {json.dumps(event)}")

    # Extract task token if present (for Step Functions integration)
    task_token = event.get('taskToken')
    sfn_client = None

    if task_token:
        logging.info("Task token found, will send callback to Step Functions")
        sfn_client = boto3.client('stepfunctions')

    # Create failure callback for Step Functions
    def on_failure(error: str, cause: str) -> None:
        if task_token and sfn_client:
            sfn_client.send_task_failure(
                taskToken=task_token,
                error=error,
                cause=cause
            )

    # Fetch GitHub token from Secrets Manager
    err1, github_token = fetch_github_token(on_failure)
    if err1:
        return err1

    # Extract PR information
    err2, (pull_request, pr_sha, pr_number, clone_url) = extract_pr_information(event, on_failure)
    if err2:
        return err2

    # Clone repository
    err3, clone_dir = clone_repository(clone_url, github_token, on_failure)
    if err3:
        return err3

    # Checkout PR HEAD
    err4, _ = checkout_pr_head(clone_dir, pr_sha, pr_number, on_failure)
    if err4:
        return err4

    # Find changed config files
    err5, changed_configs = find_changed_configs(pull_request, clone_dir, on_failure)
    if err5:
        return err5

    # Determine if we should ignore local files
    ignore_locals = event.get('ignoreLocals', False)
    logging.info(f"Ignore local files: {ignore_locals}")

    # Run the script
    err6, _ = run_build_script(changed_configs, ignore_locals, clone_dir, on_failure)
    if err6:
        return err6

    # Upload to S3
    err7, _ = upload_to_s3(event, clone_dir, on_failure)
    if err7:
        return err7

    # Success!
    success_response = {
        'statusCode': 200,
        'status': 'success',
        'pr_number': pr_number,
        'sha': pr_sha,
        'message': f'Successfully processed PR #{pr_number} at {pr_sha}',
        'changedConfigs': changed_configs
    }

    if task_token and sfn_client:
        sfn_client.send_task_success(
            taskToken=task_token, output=json.dumps(success_response)
        )

    return success_response


def fetch_github_token(on_failure: FailCallable) -> tuple[dict|None, str|None]:
    """ Fetch GitHub token from Secrets Manager """
    try:
        secrets_client = boto3.client('secretsmanager')
        secret_arn = os.environ.get('GITHUB_SECRET_ARN')

        if not secret_arn:
            raise ValueError("GITHUB_SECRET_ARN environment variable not set")

        logging.info(f"Fetching secret from: {secret_arn}")
        secret_response = secrets_client.get_secret_value(SecretId=secret_arn)
        github_token = secret_response['SecretString']
        logging.info("Successfully retrieved GitHub token from Secrets Manager")
        return None, github_token

    except Exception as e:
        logging.error(f"Failed to retrieve GitHub token: {e}")
        on_failure('GitHubTokenError', str(e))
        return make_error(f'Failed to retrieve GitHub token: {str(e)}'), None


def extract_pr_information(event: dict, on_failure: FailCallable) -> tuple[dict|None, tuple[dict, str, int, str] | tuple[None, None, None, None]]:
    """ Extract PR information from event """
    try:
        pull_request = event.get('pull_request', {})
        head_info = pull_request.get('head', {})
        pr_sha = head_info.get('sha')
        pr_number = event.get('number')
        repository = event.get('repository', {})
        clone_url = repository.get('clone_url', '')

        if not pr_sha:
            raise ValueError("No PR SHA found in event payload")

        logging.info(f"Processing PR #{pr_number}, HEAD SHA: {pr_sha}, URL: {clone_url}")
        return None, (pull_request, pr_sha, pr_number, clone_url)

    except Exception as e:
        logging.error(f"Failed to parse PR information: {e}")
        error_response = {
            'statusCode': 400,
            'status': 'error',
            'error': f'Failed to parse PR information: {str(e)}'
        }
        on_failure('PRParseError', str(e))
        return error_response, (None, None, None, None)


def clone_repository(clone_url: str, github_token: str, on_failure: FailCallable) -> tuple[dict|None, str|None]:
    """ Clone repository to /tmp/repo """
    try:
        parsed_url = urllib.parse.urlparse(clone_url)
        repo_url = urllib.parse.urlunparse((parsed_url.scheme, f'{github_token}@github.com', *parsed_url[2:]))
        clone_dir = '/tmp/repo'

        # Clean up any previous clone
        subprocess.run(['rm', '-rf', clone_dir], check=False)

        logging.info(f"Cloning repository to {clone_dir}")
        result = run_in(['git', 'clone', '--depth', '1', repo_url, clone_dir], '.')
        logging.info(f"Clone output: {result.stdout}")
        return None, clone_dir

    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to clone repository: {e}")
        logging.error(f"STDOUT: {e.stdout}")
        logging.error(f"STDERR: {e.stderr}")
        on_failure('GitCloneError', e.stderr or str(e))
        return make_error(f'Failed to clone repository: {e.stderr}'), None


def checkout_pr_head(clone_dir: str, pr_sha: str, pr_number: int, on_failure: FailCallable) -> tuple[dict|None, None]:
    """ Checkout PR HEAD commit """
    try:
        logging.info(f"Checking out commit {pr_sha}")
        result = run_in(['git', 'fetch', 'origin', pr_sha], clone_dir)
        logging.info(f"Fetch output: {result.stdout}")

        result = run_in(['git', 'checkout', pr_sha], clone_dir)
        logging.info(f"Checkout output: {result.stdout}")

        # Verify checkout
        result = run_in(['git', 'rev-parse', 'HEAD'], clone_dir)
        current_sha = result.stdout.strip()
        logging.info(f"Current HEAD: {current_sha}")

        if current_sha != pr_sha:
            raise ValueError(f"Checkout verification failed: expected {pr_sha}, got {current_sha}")

        logging.info(f"Successfully checked out PR #{pr_number} at {pr_sha}")
        return None, None

    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to checkout commit: {e}")
        logging.error(f"STDOUT: {e.stdout}")
        logging.error(f"STDERR: {e.stderr}")
        on_failure('GitCheckoutError', e.stderr or str(e))
        return make_error(f'Failed to checkout commit: {e.stderr}'), None
    except ValueError as e:
        logging.error(f"{e}")
        on_failure('CheckoutVerificationError', str(e))
        return make_error(str(e)), None


def find_changed_configs(pull_request: dict, clone_dir: str, on_failure: FailCallable) -> tuple[dict|None, list[str]|None]:
    """ Find changed config files in the PR """
    try:
        base_sha = pull_request.get('base', {}).get('sha')
        head_sha = pull_request.get('head', {}).get('sha')

        if not base_sha or not head_sha:
            raise ValueError("Missing base or head SHA for diff")

        logging.info(f"Finding changed configs between {base_sha} and {head_sha}")
        diff_result = run_in(['git', 'diff', '--name-only', f'{base_sha}...{head_sha}'], clone_dir)

        changed_files = diff_result.stdout.strip().split('\n')
        changed_configs = [f for f in changed_files if f.startswith('config') and f.endswith('.yaml')]

        logging.info(f"Changed config files: {changed_configs}")
        return None, changed_configs

    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to find changed configs: {e}")
        logging.error(f"STDOUT: {e.stdout}")
        logging.error(f"STDERR: {e.stderr}")
        on_failure('GitDiffError', e.stderr or str(e))
        return make_error(f'Failed to find changed configs: {e.stderr}'), None
    except ValueError as e:
        logging.error(f"{e}")
        on_failure('GitDiffValidationError', str(e))
        return make_error(str(e)), None

def run_build_script(changed_configs: list[str], ignore_locals: bool, clone_dir: str, on_failure: FailCallable) -> tuple[dict|None, None]:
    """ Run build-country-polygon.py with appropriate arguments """
    try:
        if not changed_configs:
            logging.info("No config files changed, skipping build-country-polygon.py")
            # Successfully skip processing - nothing to do
            pass
        else:
            if ignore_locals:
                logging.info(f"Running build-country-polygon.py with --configs {' '.join(changed_configs)} --ignore-locals")
                result = run_in(['./build-country-polygon.py', '--configs'] + changed_configs + ['--ignore-locals'], clone_dir)
            else:
                logging.info(f"Running build-country-polygon.py with --configs {' '.join(changed_configs)}")
                result = run_in(['./build-country-polygon.py', '--configs'] + changed_configs, clone_dir)
            logging.info(f"Run output: {result.stdout}")
            logging.info("Successfully ran build-country-polygon.py")
        return None, None
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to run build-country-polygon.py: {e}")
        logging.error(f"STDOUT: {e.stdout}")
        logging.error(f"STDERR: {e.stderr}")
        on_failure('ScriptExecutionError', e.stderr or str(e))
        return make_error(f'Failed to run build-country-polygon.py: {e.stderr}'), None
    except ValueError as e:
        logging.error(f"{e}")
        on_failure('ScriptValidationError', str(e))
        return make_error(str(e)), None

def upload_to_s3(event: dict, clone_dir: str, on_failure: FailCallable) -> tuple[dict|None, None]:
    """ Upload generated CSV files to S3 """
    try:
        destination = event.get('destination', f"s3://{os.environ.get('DATA_BUCKET')}/default/")
        parsed = urllib.parse.urlparse(destination)
        s3_client = boto3.client('s3')
        for name in ('country-areas.csv', 'country-boundaries.csv'):
            local_path = os.path.join(clone_dir, name)
            if not os.path.exists(local_path):
                logging.info(f"Skipping nonexistent {local_path}")
                continue
            logging.info(f"Uploading {local_path} to {destination}")
            s3_client.upload_file(
                Filename=local_path,
                Bucket=parsed.netloc,
                Key=os.path.join(parsed.path, name).lstrip('/'),
                ExtraArgs=dict(ACL='public-read', StorageClass='INTELLIGENT_TIERING'),
            )
        return None, None
    except Exception as e:
        logging.error(f"{e}")
        on_failure('ScriptValidationError', str(e))
        return make_error(str(e)), None
