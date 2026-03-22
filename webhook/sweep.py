from __future__ import annotations

import gzip
import json
import logging
import os
import random
import time
import typing
import unittest
import unittest.mock
import urllib.error
import urllib.request

import boto3
import yaml

logging.basicConfig(format='%(levelname)s: %(message)s')
logging.getLogger().setLevel(logging.INFO)


def fetch_github_token(github_secret_arn: str) -> str:
    secrets_client = boto3.client('secretsmanager')
    secret_response = secrets_client.get_secret_value(SecretId=github_secret_arn)
    return str(secret_response['SecretString'])


def github_request(url: str, github_token: str) -> typing.Any:
    request = urllib.request.Request(
        url,
        headers={
            'Authorization': f'token {github_token}',
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'boundary-issues-sweep',
        },
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def collect_relation_ids(repo: str, github_token: str) -> set[int]:
    """Return all OSM relation IDs referenced across open PRs and main branch."""
    shas: set[str] = set()

    # Open PRs
    pulls = github_request(
        f'https://api.github.com/repos/{repo}/pulls?state=open&per_page=100',
        github_token,
    )
    for pr in pulls:
        sha = pr.get('head', {}).get('sha')
        if sha:
            shas.add(sha)

    # main branch
    main = github_request(
        f'https://api.github.com/repos/{repo}/branches/main',
        github_token,
    )
    main_sha = main.get('commit', {}).get('sha')
    if main_sha:
        shas.add(main_sha)

    relation_ids: set[int] = set()
    for sha in shas:
        tree = github_request(
            f'https://api.github.com/repos/{repo}/git/trees/{sha}?recursive=0',
            github_token,
        )
        for item in tree.get('tree', []):
            path = item.get('path', '')
            if path.startswith('config') and path.endswith('.yaml'):
                raw_url = f'https://raw.githubusercontent.com/{repo}/{sha}/{path}'
                raw_req = urllib.request.Request(
                    raw_url,
                    headers={'Authorization': f'token {github_token}', 'User-Agent': 'boundary-issues-sweep'},
                )
                with urllib.request.urlopen(raw_req) as resp:
                    config = yaml.safe_load(resp.read())
                relation_ids.update(_extract_relation_ids(config))

    return relation_ids


def _extract_relation_ids(config: typing.Any) -> list[int]:
    """Walk config dict and return all relation IDs."""
    ids: list[int] = []
    if not isinstance(config, dict):
        return ids
    for country_val in config.values():
        if not isinstance(country_val, dict):
            continue
        for section_key, section_val in country_val.items():
            if section_key == 'base':
                ids.extend(_scan_shape_list(section_val))
            elif section_key == 'perspectives':
                if isinstance(section_val, dict):
                    for perspective_list in section_val.values():
                        ids.extend(_scan_shape_list(perspective_list))
    return ids


def _scan_shape_list(shape_list: typing.Any) -> list[int]:
    ids: list[int] = []
    if not isinstance(shape_list, list):
        return ids
    for item in shape_list:
        if isinstance(item, list) and len(item) >= 3 and item[1] == 'relation':
            try:
                ids.append(int(item[2]))
            except (ValueError, TypeError):
                pass
    return ids


def find_stale_relations(
    relation_ids: set[int],
    data_bucket: str,
    max_age_seconds: float = 86400.0,
) -> list[int]:
    """Return relation IDs whose S3 cache is missing or older than max_age_seconds."""
    s3 = boto3.client('s3')
    now = time.time()
    stale: list[int] = []
    for osm_id in relation_ids:
        key = f'cache/relation/{osm_id}.osm.xml.gz'
        try:
            head = s3.head_object(Bucket=data_bucket, Key=key)
            age = now - head['LastModified'].timestamp()
            if age > max_age_seconds:
                stale.append(osm_id)
        except Exception as exc:
            # Accept both botocore ClientError 404 and any other missing-object signal
            logging.debug(f'Cache miss for relation {osm_id}: {exc}')
            stale.append(osm_id)
    return stale


def download_relation(osm_id: int) -> bytes:
    """Download relation XML from OSM API with retries."""
    url = f'https://api.openstreetmap.org/api/0.6/relation/{osm_id}/full'
    last_exc: Exception = RuntimeError('no attempts made')
    for delay in (None, 10, 20):
        if delay is not None:
            logging.info(f'Retrying {url} after {delay}s')
            time.sleep(delay)
        else:
            time.sleep(5)
        try:
            logging.info(f'Downloading {url}')
            data: bytes = urllib.request.urlopen(url).read()
            return data
        except Exception as exc:
            last_exc = exc
    raise last_exc


def upload_to_cache(osm_id: int, data: bytes, data_bucket: str) -> None:
    s3 = boto3.client('s3')
    key = f'cache/relation/{osm_id}.osm.xml.gz'
    s3.put_object(
        Bucket=data_bucket,
        Key=key,
        Body=gzip.compress(data, compresslevel=9),
        ContentType='application/gzip',
        ACL='public-read',
        StorageClass='INTELLIGENT_TIERING',
    )
    logging.info(f'Uploaded s3://{data_bucket}/{key}')


def lambda_handler(event: dict[str, typing.Any], context: typing.Any) -> dict[str, typing.Any]:
    github_secret_arn = os.environ.get('GITHUB_SECRET_ARN', '')
    data_bucket = os.environ.get('DATA_BUCKET', '')
    github_repo = os.environ.get('GITHUB_REPO', '')

    if not github_secret_arn or not data_bucket or not github_repo:
        logging.error('Missing required environment variables')
        return {'statusCode': 500, 'error': 'Missing GITHUB_SECRET_ARN, DATA_BUCKET, or GITHUB_REPO'}

    github_token = fetch_github_token(github_secret_arn)
    relation_ids = collect_relation_ids(github_repo, github_token)
    logging.info(f'Found {len(relation_ids)} unique relation IDs across all branches')

    stale = find_stale_relations(relation_ids, data_bucket)
    logging.info(f'{len(stale)} stale relations out of {len(relation_ids)}')

    if not stale:
        logging.info('No stale relations; nothing to do')
        return {'statusCode': 200, 'message': 'No stale relations'}

    chosen = random.choice(stale)
    logging.info(f'Selected relation {chosen} for refresh')

    data = download_relation(chosen)
    upload_to_cache(chosen, data, data_bucket)

    return {'statusCode': 200, 'message': f'Cached relation {chosen}'}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSweepFunction(unittest.TestCase):

    def _make_mock_boto_client(
        self,
        secret_string: str = 'test-token',
        head_responses: typing.Optional[dict[int, typing.Any]] = None,
    ) -> typing.Any:
        """Return a boto3.client side_effect factory."""
        if head_responses is None:
            head_responses = {}

        mock_secrets = unittest.mock.MagicMock()
        mock_secrets.get_secret_value.return_value = {'SecretString': secret_string}

        mock_s3 = unittest.mock.MagicMock()

        def head_object_side_effect(**kwargs: typing.Any) -> typing.Any:
            key: str = kwargs.get('Key', '')
            for osm_id, val in head_responses.items():
                if str(osm_id) in key:
                    if isinstance(val, Exception):
                        raise val
                    return val
            # Default: 404-like exception
            error = Exception('Not found')
            error.response = {'Error': {'Code': '404'}}  # type: ignore[attr-defined]
            raise error

        mock_s3.head_object.side_effect = head_object_side_effect

        def client_factory(service_name: str, **_: typing.Any) -> typing.Any:
            if service_name == 'secretsmanager':
                return mock_secrets
            elif service_name == 's3':
                return mock_s3
            return unittest.mock.MagicMock()

        return client_factory, mock_s3

    @unittest.mock.patch.dict(os.environ, {
        'GITHUB_SECRET_ARN': 'arn:aws:secretsmanager:us-east-1:123:secret:tok',
        'DATA_BUCKET': 'test-bucket',
        'GITHUB_REPO': 'org/repo',
    })
    @unittest.mock.patch('boto3.client')
    @unittest.mock.patch('urllib.request.urlopen')
    def test_picks_and_uploads_stale_relation(
        self, mock_urlopen: typing.Any, mock_boto_client: typing.Any
    ) -> None:
        import datetime

        client_factory, mock_s3 = self._make_mock_boto_client(
            head_responses={
                # 184633 is stale (older than 24h)
                184633: {'LastModified': datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)},
            }
        )
        mock_boto_client.side_effect = client_factory

        # GitHub API responses
        pulls_resp = _make_url_mock(json.dumps([{'head': {'sha': 'abc123'}}]).encode())
        main_resp = _make_url_mock(json.dumps({'commit': {'sha': 'abc123'}}).encode())
        tree_resp = _make_url_mock(json.dumps({'tree': [{'path': 'config.yaml'}]}).encode())
        config_resp = _make_url_mock(b'NPL:\n  base:\n    - [plus, relation, 184633]\n')
        osm_resp = _make_url_mock(b'<osm>xml</osm>')

        mock_urlopen.side_effect = [pulls_resp, main_resp, tree_resp, config_resp, osm_resp]

        result = lambda_handler({}, unittest.mock.MagicMock())

        self.assertEqual(result['statusCode'], 200)
        self.assertIn('184633', result['message'])
        mock_s3.put_object.assert_called_once()
        call_kwargs = mock_s3.put_object.call_args[1]
        self.assertEqual(call_kwargs['Bucket'], 'test-bucket')
        self.assertIn('184633', call_kwargs['Key'])

    @unittest.mock.patch.dict(os.environ, {
        'GITHUB_SECRET_ARN': 'arn:aws:secretsmanager:us-east-1:123:secret:tok',
        'DATA_BUCKET': 'test-bucket',
        'GITHUB_REPO': 'org/repo',
    })
    @unittest.mock.patch('boto3.client')
    @unittest.mock.patch('urllib.request.urlopen')
    def test_no_upload_when_all_fresh(
        self, mock_urlopen: typing.Any, mock_boto_client: typing.Any
    ) -> None:
        import datetime

        fresh_time = datetime.datetime.now(tz=datetime.timezone.utc)
        client_factory, mock_s3 = self._make_mock_boto_client(
            head_responses={184633: {'LastModified': fresh_time}}
        )
        mock_boto_client.side_effect = client_factory

        pulls_resp = _make_url_mock(json.dumps([{'head': {'sha': 'abc123'}}]).encode())
        main_resp = _make_url_mock(json.dumps({'commit': {'sha': 'abc123'}}).encode())
        tree_resp = _make_url_mock(json.dumps({'tree': [{'path': 'config.yaml'}]}).encode())
        config_resp = _make_url_mock(b'NPL:\n  base:\n    - [plus, relation, 184633]\n')

        mock_urlopen.side_effect = [pulls_resp, main_resp, tree_resp, config_resp]

        result = lambda_handler({}, unittest.mock.MagicMock())

        self.assertEqual(result['statusCode'], 200)
        self.assertIn('No stale', result['message'])
        mock_s3.put_object.assert_not_called()

    @unittest.mock.patch.dict(os.environ, {}, clear=True)
    def test_missing_env_vars(self) -> None:
        result = lambda_handler({}, unittest.mock.MagicMock())
        self.assertEqual(result['statusCode'], 500)

    def test_extract_relation_ids_base_and_perspectives(self) -> None:
        config = {
            'FRA': {
                'base': [
                    ['plus', 'relation', 2202162],
                    ['minus', 'relation', 365331],
                ],
                'perspectives': {
                    'FRA': [['plus', 'relation', 2202162]],
                },
            }
        }
        ids = _extract_relation_ids(config)
        self.assertIn(2202162, ids)
        self.assertIn(365331, ids)

    def test_scan_shape_list_ignores_non_relation(self) -> None:
        shape_list = [
            ['plus', 'way', 12345],
            ['plus', 'relation', 99999],
        ]
        ids = _scan_shape_list(shape_list)
        self.assertEqual(ids, [99999])


def _make_url_mock(data: bytes) -> unittest.mock.MagicMock:
    mock = unittest.mock.MagicMock()
    mock.read.return_value = data
    mock.__enter__ = lambda s: s
    mock.__exit__ = unittest.mock.Mock(return_value=False)
    return mock


if __name__ == '__main__':
    unittest.main()
