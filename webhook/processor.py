from __future__ import annotations

import argparse
import boto3
import glob
import geopandas
import json
import logging
import shutil
import subprocess
import os
import tempfile
import typing
import urllib.parse
import yaml

# Configure logging
logging.basicConfig(format='%(levelname)s: %(message)s')
logging.getLogger().setLevel(logging.INFO)


FailCallable = typing.Callable[[str, str], None]


def run_in(cmd: list[str], dirname: str) -> subprocess.CompletedProcess[str]:
    """ Run a command in a directory
    """
    return subprocess.run(cmd, cwd=dirname, capture_output=True, text=True, check=True)


def make_error(message: str) -> dict[str, typing.Any]:
    """ Make a standard error dictionary
    """
    return {'statusCode': 500, 'status': 'error', 'error': message}


def lambda_handler(event: dict[str, typing.Any], context: typing.Any) -> dict[str, typing.Any]:
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

    # Extract event fields
    destination: str = event.get('destination', f"s3://{os.environ.get('DATA_BUCKET')}/default/")
    check_fresh_osm: bool = event.get('checkFreshOSM', False)
    iso3s: typing.Optional[str] = event.get('iso3s')
    task_token: typing.Optional[str] = event.get('taskToken')
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
    else:
        assert github_token is not None

    # Extract PR information
    err2, (pull_request, pr_sha, pr_number, clone_url) = extract_pr_information(event, on_failure)
    if err2:
        return err2
    else:
        assert pull_request is not None and pr_sha is not None and pr_number is not None and clone_url is not None

    with tempfile.TemporaryDirectory(prefix='processor-') as execution_dir:
        # Clone repository
        err3, clone_dir = clone_repository(clone_url, github_token, execution_dir, on_failure)
        if err3:
            return err3
        else:
            assert clone_dir is not None

        # Checkout PR HEAD
        err4, _ = checkout_pr_head(clone_dir, pr_sha, pr_number, on_failure)
        if err4:
            return err4

        # Find changed config files
        err5, changed_configs = find_changed_configs(pull_request, clone_dir, on_failure)
        if err5:
            return err5
        else:
            assert changed_configs is not None

        logging.info(f"check Fresh OSM files: {check_fresh_osm}")

        # Derive ISO3s from changed configs and pass those instead of --configs
        derived_iso3s = extract_iso3s_from_configs(changed_configs, clone_dir)
        iso3s_arg = ','.join(derived_iso3s) if derived_iso3s else iso3s
        logging.info(f"ISO3s derived from changed configs: {derived_iso3s}")

        # Run the script
        err6 = run_build_script(None, check_fresh_osm, clone_dir, on_failure, iso3s_arg)
        if err6:
            return err6

        s3_client = boto3.client('s3')

        # Generate tiles on first run (when checkFreshOSM is not set)
        if not check_fresh_osm:
            err7 = generate_tiles(s3_client, destination, clone_dir, on_failure)
            if err7:
                return err7
            err8 = generate_preview_html(s3_client, destination, clone_dir, on_failure)
            if err8:
                return err8
            err9 = update_status_html(s3_client, destination, clone_dir, on_failure)
            if err9:
                return err9

    # Success!
    success_response = {
        'statusCode': 200,
        'status': 'success',
        'pr_number': pr_number,
        'sha': pr_sha,
        'message': f'Successfully processed PR #{pr_number} at {pr_sha}',
        'iso3s': iso3s_arg
    }

    if task_token and sfn_client:
        sfn_client.send_task_success(
            taskToken=task_token, output=json.dumps(success_response)
        )

    return success_response


def fetch_github_token(on_failure: FailCallable) -> tuple[dict[str, typing.Any]|None, str|None]:
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


def extract_pr_information(event: dict[str, typing.Any], on_failure: FailCallable) -> tuple[dict[str, typing.Any]|None, tuple[dict[str, typing.Any], str, int, str] | tuple[None, None, None, None]]:
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


def clone_repository(clone_url: str, github_token: str, execution_dir: str, on_failure: FailCallable) -> tuple[dict[str, typing.Any]|None, str|None]:
    """ Clone repository to temp """
    try:
        parsed_url = urllib.parse.urlparse(clone_url)
        repo_url = urllib.parse.urlunparse((parsed_url.scheme, f'{github_token}@github.com', *parsed_url[2:]))
        clone_dir = tempfile.mkdtemp(dir=execution_dir, prefix='repo-')

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


def checkout_pr_head(clone_dir: str, pr_sha: str, pr_number: int, on_failure: FailCallable) -> tuple[dict[str, typing.Any]|None, None]:
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


def find_changed_configs(pull_request: dict[str, typing.Any], clone_dir: str, on_failure: FailCallable) -> tuple[dict[str, typing.Any]|None, list[str]|None]:
    """ Find changed config files in the PR """
    try:
        base_sha = pull_request.get('base', {}).get('sha')
        head_sha = pull_request.get('head', {}).get('sha')

        if not base_sha or not head_sha:
            raise ValueError("Missing base or head SHA for diff")

        logging.info(f"Finding changed configs between {base_sha} and {head_sha}")
        run_in(['git', 'fetch', '--depth=1', 'origin', base_sha], clone_dir)
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


def extract_iso3s_from_configs(changed_configs: list[str], clone_dir: str) -> list[str]:
    """Extract all ISO3 codes referenced in the given config files."""
    iso3_set: set[str] = set()
    for config_path in changed_configs:
        full_path = os.path.join(clone_dir, config_path)
        try:
            with open(full_path) as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                continue
            for iso3, country_data in data.items():
                if iso3 != 'base':
                    iso3_set.add(iso3)
                if not isinstance(country_data, dict):
                    continue
                for section in ('perspectives', 'interior-points', 'exterior-points'):
                    sub = country_data.get(section)
                    if isinstance(sub, dict):
                        for key in sub:
                            if key != 'base':
                                iso3_set.add(key)
        except Exception:
            logging.warning(f"Could not parse config {config_path} for ISO3 extraction")
    return sorted(iso3_set)


def run_build_script(changed_configs: list[str] | None, check_fresh_osm: bool, clone_dir: str, on_failure: FailCallable, iso3s: typing.Optional[str] = None) -> dict[str, typing.Any]|None:
    """ Run build-country-polygon.py with appropriate arguments """
    cache_base_url = os.environ.get('CACHE_BASE_URL')
    try:
        if changed_configs is None and not iso3s:
            logging.info("No configs or ISO3s to process, skipping build-country-polygon.py")
        else:
            cmd = ['./build-country-polygon.py']
            if changed_configs is not None:
                cmd += ['--configs'] + changed_configs
            if check_fresh_osm:
                cmd += ['--check-fresh-osm']
            if cache_base_url:
                cmd += ['--cache-base-url', cache_base_url]
            if iso3s:
                cmd += ['--iso3s', iso3s]
            logging.info(f"Running {' '.join(cmd)}")
            result = run_in(cmd, clone_dir)
            logging.info(f"Run output: {result.stdout}")
            logging.info("Successfully ran build-country-polygon.py")
        return None
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to run build-country-polygon.py: {e}")
        logging.error(f"STDOUT: {e.stdout}")
        logging.error(f"STDERR: {e.stderr}")
        on_failure('ScriptExecutionError', e.stderr or str(e))
        return make_error(f'Failed to run build-country-polygon.py: {e.stderr}')
    except ValueError as e:
        logging.error(f"{e}")
        on_failure('ScriptValidationError', str(e))
        return make_error(str(e))



def generate_tiles(s3_client: typing.Any, destination: typing.Optional[str], clone_dir: str, on_failure: FailCallable) -> dict[str, typing.Any]|None:
    """ Run the Planetiler JAR to generate preview.pmtiles, then upload to S3 if s3_client is provided """
    try:
        gpkg_path = os.path.join(clone_dir, 'out.gpkg')

        if not os.path.exists(gpkg_path):
            logging.info("No out.gpkg found, skipping tile generation")
            return None

        output_path = os.path.join(clone_dir, 'preview.pmtiles')
        with tempfile.TemporaryDirectory(dir=clone_dir, prefix='data-') as data_dir:
            # /var/data is read-only at Lambda runtime; copy GPKG to temp so SQLite can write sidecar files
            bundled_landcover = '/var/data/daylight-landcover.gpkg'
            landcover_file = f'{data_dir}/daylight-landcover.gpkg'
            if os.path.exists(bundled_landcover) and not os.path.exists(landcover_file):
                shutil.copy2(bundled_landcover, landcover_file)

            cmd = [
                'java', '-jar', '/var/task/tiles.jar',
                f'--data={data_dir}',
                f'--tmpdir={data_dir}/tmp',
                f'--output={output_path}',
                f'--landcover_path={landcover_file}',
                '--download',
                '--force',
                '--maxzoom', '12',
                f'--gpkg={gpkg_path}',
            ]
            logging.info(f"Running tile generation: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            logging.info(f"Tile generation output: {result.stdout}")

        if s3_client is None or destination is None:
            logging.info(f"Skipping S3 upload; preview.pmtiles written to {output_path}")
            return None

        parsed = urllib.parse.urlparse(destination)
        s3_client.put_object(
            Bucket=parsed.netloc,
            Key=os.path.join(parsed.path, 'preview.log').lstrip('/'),
            Body=result.stdout.encode('utf-8'),
            ContentType='text/plain',
            ACL='public-read',
            StorageClass='INTELLIGENT_TIERING',
        )

        # Upload preview.pmtiles to S3 alongside the CSVs
        key = os.path.join(parsed.path, 'preview.pmtiles').lstrip('/')
        logging.info(f"Uploading {output_path} to s3://{parsed.netloc}/{key}")
        s3_client.upload_file(
            Filename=output_path,
            Bucket=parsed.netloc,
            Key=key,
            ExtraArgs=dict(ACL='public-read', StorageClass='INTELLIGENT_TIERING'),
        )
        logging.info("Successfully uploaded preview.pmtiles")
        return None

    except subprocess.CalledProcessError as e:
        logging.error(f"Tile generation failed: {e}")
        logging.error(f"STDOUT: {e.stdout}")
        logging.error(f"STDERR: {e.stderr}")
        on_failure('TileGenerationError', e.stderr or str(e))
        return make_error(f'Tile generation failed: {e.stderr}')
    except Exception as e:
        logging.error(f"Tile generation failed: {e}")
        on_failure('TileGenerationError', str(e))
        return make_error(f'Tile generation failed: {str(e)}')


def generate_preview_html(s3_client: typing.Any, destination: str|None, clone_dir: str, on_failure: FailCallable) -> dict[str, typing.Any]|None:
    """ Generate preview.html and upload to S3 alongside preview.pmtiles """
    try:
        gpkg_path = os.path.join(clone_dir, 'out.gpkg')
        perspective_set: set[str] = set()
        if os.path.exists(gpkg_path):
            for layer_name in ('country-areas', 'validation-points'):
                try:
                    gdf = geopandas.read_file(gpkg_path, layer=layer_name)
                    for perspectives_val in gdf['perspectives']:
                        for code in str(perspectives_val).split(';'):
                            code = code.strip()
                            if code:
                                perspective_set.add(code)
                except Exception:
                    pass
        iso3s: dict[str, list[str]] = {}
        if os.path.exists(gpkg_path):
            try:
                gdf_boundaries = geopandas.read_file(gpkg_path, layer='country-boundaries')
                for _, row in gdf_boundaries.iterrows():
                    for col in ('stable', 'disputed', 'nonexistent'):
                        for code in str(row[col]).split(';'):
                            code = code.strip()
                            if code:
                                perspective_set.add(code)
                                if code not in iso3s:
                                    iso3s[code] = []
                                iso3s[code].append(col)
            except Exception:
                pass

        # Group codes by their pattern fingerprint; singleton groups get individual buttons
        patterns: dict[tuple[str, ...], list[str]] = {}
        for code, appearances in iso3s.items():
            key = tuple(appearances)
            if key not in patterns:
                patterns[key] = []
            patterns[key].append(code)

        unique_perspectives = sorted(code for codes in patterns.values() if len(codes) == 1 for code in codes)
        others_perspectives = sorted(
            (code for codes in patterns.values() if len(codes) > 1 for code in codes),
        ) + sorted(set(perspective_set) - set(iso3s.keys()))
        unique_perspectives_json = json.dumps(unique_perspectives)
        others_perspectives_json = json.dumps(others_perspectives)

        html = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Preview</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@5.15.0/dist/maplibre-gl.css">
<script src="https://unpkg.com/maplibre-gl@5.15.0/dist/maplibre-gl.js"></script>
<script src="https://unpkg.com/pmtiles@4.3.0/dist/pmtiles.js"></script>
<style>
body { margin: 0; }
#map { width: 100vw; height: 100vh; }
#controls { position: absolute; top: 10px; left: 10px; background: rgba(255,255,255,0.9); padding: 8px 12px; border-radius: 4px; font-family: sans-serif; font-size: 13px; z-index: 1; }
#controls label { font-size: 12px; width: 50px; height: 1.2em; display: inline-block; margin: 3px 0; cursor: pointer; white-space: nowrap }
#controls { width: 75px }
@media (min-width: 1000px) { #controls { width: 110px } }
@media (min-width: 1100px) { #controls { width: 160px } }
</style>
</head>
<body>
<div id="map"></div>
<div id="controls">
  <strong>Perspective</strong>
  <div id="perspective-radios"></div>
</div>
<script>
const protocol = new pmtiles.Protocol();
maplibregl.addProtocol('pmtiles', protocol.tile);

const map = new maplibregl.Map({
  container: 'map',
  hash: true,
  center: [0, 20],
  zoom: 2,
  style: {
    version: 8,
    sources: {
      protomaps: {
        type: 'vector',
        url: 'pmtiles://preview.pmtiles',
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
      }
    },
    layers: [
      {
        "id": "background", "type": "background",
        "paint": { "background-color": "#97DCE8" }
      },
      {
        "id": "landcover", "type": "fill",
        "source": "protomaps", "source-layer": "landcover",
        "paint": {
          "fill-color": ["match", ["get", "kind"],
            "grassland", "rgba(210, 239, 207, 1)",
            "barren",    "rgba(255, 243, 215, 1)",
            "urban_area","rgba(230, 230, 230, 1)",
            "farmland",  "rgba(216, 239, 210, 1)",
            "glacier",   "rgba(255, 255, 255, 1)",
            "scrub",     "rgba(234, 239, 210, 1)",
            "rgba(196, 231, 210, 1)"
          ],
          "fill-opacity": ["interpolate", ["linear"], ["zoom"], 5, 1, 7, 0]
        }
      },
      {
        "id": "areas", "type": "fill",
        "source": "protomaps", "source-layer": "areas",
        "paint": {
          "fill-color": ["match", ["%", ["get", "color_index"], 24],
            // https://phillips.shef.ac.uk/pub/cpt-city/cb/qual/set2_08
            0, "#7DC0A6",
            1, "#ED936B",
            2, "#919FC7",
            3, "#DA8EC0",
            4, "#B0D767",
            5, "#F9DA56",
            6, "#E0C59A",
            7, "#B3B3B3",
            // https://phillips.shef.ac.uk/pub/cpt-city/cb/qual/dark2_08
            8, "#4B9C79",
            9, "#CA6627",
            10, "#7470AE",
            11, "#D43E88",
            12, "#75A43A",
            13, "#DDAD3B",
            14, "#9F7831",
            15, "#666666",
            // https://phillips.shef.ac.uk/pub/cpt-city/cb/qual/pastel2_08
            16, "#BCE1CE",
            17, "#F5CFB0",
            18, "#CDD5E6",
            19, "#EDCCE3",
            20, "#E9F4CD",
            21, "#FDF2B6",
            22, "#EEE2CE",
            23, "#CCCCCC",
            "#000000"
          ],
          "fill-opacity": 0.35
        }
      },
      {
        "id": "boundaries-agreed", "type": "line",
        "source": "protomaps", "source-layer": "boundaries",
        "filter": ["boolean", false],
        "paint": {
          "line-color": "rgba(0, 0, 0, 1)",
          "line-width": 2
        },
        "layout": {
          "line-cap": "round",
          "line-join": "round"
        }
      },
      {
        "id": "boundaries-disputed", "type": "line",
        "source": "protomaps", "source-layer": "boundaries",
        "filter": ["boolean", false],
        "paint": {
          "line-color": "rgba(0, 0, 0, 1)",
          "line-width": 2,
          "line-dasharray": [1, 3]
        },
        "layout": {
          "line-cap": "round",
          "line-join": "round"
        }
      },
      {
        "id": "validation-points-interior", "type": "circle",
        "source": "protomaps", "source-layer": "points",
        "filter": ["==", ["get", "relation"], "interior"],
        "paint": {
          "circle-color": "rgba(0, 0, 0, 1)",
          "circle-radius": 5,
          "circle-stroke-color": "rgba(255, 255, 255, 0.65)",
          "circle-stroke-width": 1.5
        }
      },
      {
        "id": "validation-points-labels", "type": "symbol",
        "source": "protomaps", "source-layer": "points",
        "filter": ["==", ["get", "relation"], "interior"],
        "layout": {
          "text-field": ["get", "iso3"],
          "text-font": ["Noto Sans Regular"],
          "text-size": 12,
          "text-offset": [0, 1],
          "text-anchor": "top"
        },
        "paint": {
          "text-color": "rgba(0, 0, 0, 1)",
          "text-halo-color": "rgba(255, 255, 255, 0.65)",
          "text-halo-width": 1.5
        }
      }
    ]
  }
});
map.addControl(new maplibregl.NavigationControl());

const unique_perspectives = $UNIQUE_PERSPECTIVES_JSON$;
const others_perspectives = $OTHERS_PERSPECTIVES_JSON$;

function apply_perspective(perspective) {
  map.setFilter('areas', ["in", perspective, ["get", "perspectives"]]);
  map.setFilter('boundaries-agreed', ["in", perspective, ["get", "stable"]]);
  map.setFilter('boundaries-disputed', ["in", perspective, ["get", "disputed"]]);
  map.setFilter('validation-points-interior', ["all",
    ["==", ["get", "relation"], "interior"],
    ["in", perspective, ["get", "perspectives"]]
  ]);
  map.setFilter('validation-points-labels', ["all",
    ["==", ["get", "relation"], "interior"],
    ["in", perspective, ["get", "perspectives"]]
  ]);
}

const radios_div = document.getElementById('perspective-radios');
var first_value = null;

unique_perspectives.forEach(function(p, i) {
  const label = document.createElement('label');
  const input = document.createElement('input');
  input.type = 'radio';
  input.name = 'perspective';
  input.value = p;
  if (i === 0) {
    input.checked = true;
    first_value = p;
  }
  input.addEventListener('change', function() {
    if (this.checked) { apply_perspective(this.value); }
  });
  label.appendChild(input);
  label.appendChild(document.createTextNode(' ' + p));
  radios_div.appendChild(label);
});

if (others_perspectives.length > 0) {
  const label = document.createElement('label');
  const input = document.createElement('input');
  input.type = 'radio';
  input.name = 'perspective';
  input.value = others_perspectives[0];
  if (unique_perspectives.length === 0) {
    input.checked = true;
    first_value = others_perspectives[0];
  }
  input.addEventListener('change', function() {
    if (this.checked) { apply_perspective(this.value); }
  });
  label.appendChild(input);
  label.appendChild(document.createTextNode(' Others'));
  radios_div.appendChild(label);
  if (first_value === null) { first_value = others_perspectives[0]; }
}

map.on('load', function() {
  if (first_value !== null) {
    apply_perspective(first_value);
  }
});
</script>
</body>
</html>""".replace('$UNIQUE_PERSPECTIVES_JSON$', unique_perspectives_json).replace('$OTHERS_PERSPECTIVES_JSON$', others_perspectives_json)

        with open(os.path.join(clone_dir, 'preview.html'), "w") as file:
            file.write(html)

        if s3_client is None or destination is None:
            return None

        parsed = urllib.parse.urlparse(destination)
        html_key = os.path.join(parsed.path, 'preview.html').lstrip('/')
        logging.info(f"Uploading preview.html to s3://{parsed.netloc}/{html_key}")
        s3_client.put_object(
            Bucket=parsed.netloc,
            Key=html_key,
            Body=html.encode('utf-8'),
            ContentType='text/html',
            ACL='public-read',
            StorageClass='INTELLIGENT_TIERING',
        )
        logging.info("Successfully uploaded preview.html")
        return None

    except Exception as e:
        logging.error(f"Failed to generate preview HTML: {e}")
        on_failure('PreviewHTMLError', str(e))
        return make_error(f'Failed to generate preview HTML: {str(e)}')

def update_status_html(s3_client: typing.Any, destination: str|None, clone_dir: str, on_failure: FailCallable) -> dict[str, typing.Any]|None:
    status_text = 'First check looks fine. Waiting until second check.'

    try:
        with open(os.path.join(clone_dir, 'status.html'), "w") as file:
            file.write(status_text)

        if s3_client is None or destination is None:
            return None

        parsed = urllib.parse.urlparse(destination)
        s3_client.put_object(
            Bucket=parsed.netloc,
            Key=os.path.join(parsed.path, 'status.html').lstrip('/'),
            ACL='public-read',
            ContentType='text/html',
            Body=status_text.encode('utf8'),
            StorageClass='INTELLIGENT_TIERING',
        )
        logging.info("Successfully updated status.html")
        return None

    except Exception as e:
        logging.error(f"Failed to update status HTML: {e}")
        on_failure('StatusHTMLError', str(e))
        return make_error(f'Failed to update status HTML: {str(e)}')


def main() -> int:
    """ Standalone CLI entry point: build tiles locally without S3 uploads """
    parser = argparse.ArgumentParser(description='Build political boundary tiles locally')
    parser.add_argument('-c', '--configs', nargs='*', help='Config YAML paths (default: config-*.yaml)')
    parser.add_argument('-i', '--iso3s', help='Comma-delimited list of ISO3 codes to filter on (e.g. "PLT,ESP,FRA,ITA")')
    args = parser.parse_args()

    def on_failure(error: str, cause: str) -> None:
        logging.error(f"{error}: {cause}")

    if args.configs:
        changed_configs = args.configs
    else:
        changed_configs = sorted(glob.glob('config-*.yaml'))

    logging.info(f"Using configs: {changed_configs}")

    s3_client, destination, clone_dir = None, None, '.'

    err1 = run_build_script(changed_configs, False, clone_dir, on_failure, args.iso3s)
    if err1:
        return 1

    err2 = generate_tiles(s3_client, destination, clone_dir, on_failure)
    if err2:
        return 1

    err3 = generate_preview_html(s3_client, destination, clone_dir, on_failure)
    if err3:
        return 1

    err4 = update_status_html(s3_client, destination, clone_dir, on_failure)
    if err4:
        return 1

    return 0


if __name__ == '__main__':
    exit(main())
