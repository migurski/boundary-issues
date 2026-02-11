#!/usr/bin/env python3
"""
One-time script to configure GitHub webhook for the repository.
Usage: python3 setup_github_webhook.py <lambda-function-url>
"""

import os
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path


def load_github_token():
    """Load GitHub token from ../.env file."""
    env_path = Path(__file__).parent.parent / '.env'

    if not env_path.exists():
        print(f"Error: .env file not found at {env_path}")
        sys.exit(1)

    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith('GITHUB_TOKEN='):
                token = line.split('=', 1)[1]
                return token

    print("Error: GITHUB_TOKEN not found in .env file")
    sys.exit(1)


def get_repo_info():
    """Get repository owner and name from git remote."""
    import subprocess

    try:
        # Get the remote URL
        result = subprocess.run(
            ['git', 'config', '--get', 'remote.origin.url'],
            capture_output=True,
            text=True,
            check=True
        )
        remote_url = result.stdout.strip()

        # Parse the URL to get owner/repo
        # Handle both HTTPS and SSH formats
        if remote_url.startswith('git@github.com:'):
            # SSH format: git@github.com:owner/repo.git
            repo_path = remote_url.replace('git@github.com:', '').replace('.git', '')
        elif 'github.com' in remote_url:
            # HTTPS format: https://github.com/owner/repo.git
            repo_path = remote_url.split('github.com/')[-1].replace('.git', '')
        else:
            print(f"Error: Could not parse GitHub URL: {remote_url}")
            sys.exit(1)

        owner, repo = repo_path.split('/')
        return owner, repo

    except subprocess.CalledProcessError as e:
        print(f"Error: Could not get git remote URL: {e}")
        sys.exit(1)


def create_webhook(token, owner, repo, webhook_url):
    """Create a GitHub webhook using the API."""

    api_url = f"https://api.github.com/repos/{owner}/{repo}/hooks"

    payload = {
        "name": "web",
        "active": True,
        "events": ["push"],
        "config": {
            "url": webhook_url,
            "content_type": "json",
            "insecure_ssl": "0"
        }
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json"
    }

    request = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST'
    )

    try:
        with urllib.request.urlopen(request) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        print(f"Error creating webhook: {e.code} {e.reason}")
        print(f"Response: {error_body}")
        sys.exit(1)


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 setup_github_webhook.py <lambda-function-url>")
        sys.exit(1)

    webhook_url = sys.argv[1]

    print("Setting up GitHub webhook...")
    print(f"Webhook URL: {webhook_url}")

    # Load GitHub token
    print("\nLoading GitHub token from .env...")
    token = load_github_token()
    print("Token loaded successfully")

    # Get repository info
    print("\nDetecting repository from git remote...")
    owner, repo = get_repo_info()
    print(f"Repository: {owner}/{repo}")

    # Create webhook
    print(f"\nCreating webhook via GitHub API...")
    result = create_webhook(token, owner, repo, webhook_url)

    print("\n=========================================")
    print("GitHub webhook created successfully!")
    print("=========================================")
    print(f"Webhook ID: {result['id']}")
    print(f"Webhook URL: {result['config']['url']}")
    print(f"Events: {', '.join(result['events'])}")
    print(f"Active: {result['active']}")
    print(f"\nView webhook at: https://github.com/{owner}/{repo}/settings/hooks")
    print("=========================================")


if __name__ == '__main__':
    main()
