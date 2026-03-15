from __future__ import annotations

import boto3
import csv
import itertools
import json
import logging
import subprocess
import os
import sys
import typing
import urllib.parse
import osgeo.ogr

csv.field_size_limit(sys.maxsize)

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

    # Determine if we should check Fresh OSM files
    check_fresh_osm = event.get('checkFreshOSM', False)
    logging.info(f"check Fresh OSM files: {check_fresh_osm}")

    # Run the script
    err6, _ = run_build_script(changed_configs, check_fresh_osm, clone_dir, on_failure)
    if err6:
        return err6

    # Upload to S3
    err7, _ = upload_to_s3(event, clone_dir, on_failure)
    if err7:
        return err7

    # Generate tiles on first run (when checkFreshOSM is not set)
    if not check_fresh_osm:
        err8, _ = convert_csvs_to_geojson(clone_dir, on_failure)
        if err8:
            return err8
        err9, _ = generate_tiles(event, clone_dir, on_failure)
        if err9:
            return err9
        err10, _ = generate_preview_html(event, clone_dir, on_failure)
        if err10:
            return err10

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


def run_build_script(changed_configs: list[str], check_fresh_osm: bool, clone_dir: str, on_failure: FailCallable) -> tuple[dict|None, None]:
    """ Run build-country-polygon.py with appropriate arguments """
    try:
        if not changed_configs:
            logging.info("No config files changed, skipping build-country-polygon.py")
            # Successfully skip processing - nothing to do
            pass
        else:
            if check_fresh_osm:
                logging.info(f"Running build-country-polygon.py with --configs {' '.join(changed_configs)} --check-fresh-osm")
                result = run_in(['./build-country-polygon.py', '--configs'] + changed_configs + ['--check-fresh-osm'], clone_dir)
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
        for name in ('country-areas.csv', 'country-boundaries.csv', 'validation-points.csv'):
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


def convert_csvs_to_geojson(clone_dir: str, on_failure: FailCallable) -> tuple[dict|None, None]:
    """ Convert country-areas.csv and country-boundaries.csv to GeoJSON files in clone_dir """
    try:
        osgeo.ogr.UseExceptions()

        areas_csv = os.path.join(clone_dir, 'country-areas.csv')
        boundaries_csv = os.path.join(clone_dir, 'country-boundaries.csv')

        # Convert areas CSV (iso3, perspectives, geometry)
        areas_geojson = os.path.join(clone_dir, 'country-areas.geojson')
        if os.path.exists(areas_csv):
            feature_ids = itertools.count(1)
            features = []
            with open(areas_csv, newline='') as f:
                for row in csv.DictReader(f):
                    wkt = row.get('geometry', '')
                    if not wkt:
                        continue
                    geom = osgeo.ogr.CreateGeometryFromWkt(wkt)
                    if geom is None:
                        continue
                    features.append({
                        'type': 'Feature',
                        'geometry': json.loads(geom.ExportToJson()),
                        'properties': {
                            'index': next(feature_ids),
                            'iso3': row.get('iso3', ''),
                            'perspectives': row.get('perspectives', ''),
                        },
                    })
            with open(areas_geojson, 'w') as f:
                json.dump({'type': 'FeatureCollection', 'features': features}, f)
            logging.info(f"Wrote {len(features)} area features to {areas_geojson}")
        else:
            logging.info(f"Skipping areas conversion: {areas_csv} not found")

        # Convert boundaries CSV (iso3a, iso3b, perspectives, agreed_geometry, disputed_geometry)
        boundaries_geojson = os.path.join(clone_dir, 'country-boundaries.geojson')
        if os.path.exists(boundaries_csv):
            feature_ids = itertools.count(1)
            features = []
            with open(boundaries_csv, newline='') as f:
                for row in csv.DictReader(f):
                    iso3a = row.get('iso3a', '')
                    iso3b = row.get('iso3b', '')
                    perspectives = row.get('perspectives', '')
                    for col, disputed in [('agreed_geometry', False), ('disputed_geometry', True)]:
                        wkt = row.get(col, '')
                        if not wkt:
                            continue
                        geom = osgeo.ogr.CreateGeometryFromWkt(wkt)
                        if geom is None:
                            continue
                        features.append({
                            'type': 'Feature',
                            'geometry': json.loads(geom.ExportToJson()),
                            'properties': {
                                'index': next(feature_ids),
                                'iso3a': iso3a,
                                'iso3b': iso3b,
                                'perspectives': perspectives,
                                'disputed': disputed,
                            },
                        })
            with open(boundaries_geojson, 'w') as f:
                json.dump({'type': 'FeatureCollection', 'features': features}, f)
            logging.info(f"Wrote {len(features)} boundary features to {boundaries_geojson}")
        else:
            logging.info(f"Skipping boundaries conversion: {boundaries_csv} not found")

        # Convert validation-points CSV (iso3, perspectives, relation, geometry)
        points_csv = os.path.join(clone_dir, 'validation-points.csv')
        points_geojson = os.path.join(clone_dir, 'validation-points.geojson')
        if os.path.exists(points_csv):
            feature_ids = itertools.count(1)
            features = []
            with open(points_csv, newline='') as f:
                for row in csv.DictReader(f):
                    wkt = row.get('geometry', '')
                    if not wkt:
                        continue
                    geom = osgeo.ogr.CreateGeometryFromWkt(wkt)
                    if geom is None:
                        continue
                    features.append({
                        'type': 'Feature',
                        'geometry': json.loads(geom.ExportToJson()),
                        'properties': {
                            'index': next(feature_ids),
                            'iso3': row.get('iso3', ''),
                            'perspectives': row.get('perspectives', ''),
                            'relation': row.get('relation', ''),
                        },
                    })
            with open(points_geojson, 'w') as f:
                json.dump({'type': 'FeatureCollection', 'features': features}, f)
            logging.info(f"Wrote {len(features)} validation point features to {points_geojson}")
        else:
            logging.info(f"Skipping validation-points conversion: {points_csv} not found")

        return None, None

    except Exception as e:
        logging.error(f"Failed to convert CSVs to GeoJSON: {e}")
        on_failure('GeoJSONConversionError', str(e))
        return make_error(f'Failed to convert CSVs to GeoJSON: {str(e)}'), None


def generate_tiles(event: dict, clone_dir: str, on_failure: FailCallable) -> tuple[dict|None, None]:
    """ Run the Planetiler JAR to generate preview.pmtiles, then upload to S3 """
    try:
        areas_geojson = os.path.join(clone_dir, 'country-areas.geojson')
        boundaries_geojson = os.path.join(clone_dir, 'country-boundaries.geojson')
        points_geojson = os.path.join(clone_dir, 'validation-points.geojson')

        if not os.path.exists(areas_geojson) and not os.path.exists(boundaries_geojson):
            logging.info("No GeoJSON files found, skipping tile generation")
            return None, None

        output_path = '/tmp/preview.pmtiles'
        data_dir = '/tmp/tiles-data'
        os.makedirs(data_dir, exist_ok=True)

        cmd = [
            'java', '-jar', '/var/task/tiles.jar',
            f'--data={data_dir}',
            f'--tmpdir={data_dir}/tmp',
            f'--output={output_path}',
            '--download',
            '--force',
            '--maxzoom', '5',
        ]
        if os.path.exists(areas_geojson):
            cmd.append(f'--areas={areas_geojson}')
        if os.path.exists(boundaries_geojson):
            cmd.append(f'--boundaries={boundaries_geojson}')
        if os.path.exists(points_geojson):
            cmd.append(f'--points={points_geojson}')

        logging.info(f"Running tile generation: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        logging.info(f"Tile generation output: {result.stdout}")
        parsed = urllib.parse.urlparse(destination)
        s3_client = boto3.client('s3')
        s3_client.put_object(
            Bucket=parsed.netloc,
            Key=os.path.join(parsed.path, 'preview.log').lstrip('/'),
            Body=result.stdout.encode('utf-8'),
            ContentType='text/plain',
            ACL='public-read',
            StorageClass='INTELLIGENT_TIERING',
        )

        # Upload preview.pmtiles to S3 alongside the CSVs
        destination = event.get('destination', f"s3://{os.environ.get('DATA_BUCKET')}/default/")
        key = os.path.join(parsed.path, 'preview.pmtiles').lstrip('/')
        logging.info(f"Uploading {output_path} to s3://{parsed.netloc}/{key}")
        s3_client.upload_file(
            Filename=output_path,
            Bucket=parsed.netloc,
            Key=key,
            ExtraArgs=dict(ACL='public-read', StorageClass='INTELLIGENT_TIERING'),
        )
        logging.info("Successfully uploaded preview.pmtiles")
        return None, None

    except subprocess.CalledProcessError as e:
        logging.error(f"Tile generation failed: {e}")
        logging.error(f"STDOUT: {e.stdout}")
        logging.error(f"STDERR: {e.stderr}")
        on_failure('TileGenerationError', e.stderr or str(e))
        return make_error(f'Tile generation failed: {e.stderr}'), None
    except Exception as e:
        logging.error(f"Tile generation failed: {e}")
        on_failure('TileGenerationError', str(e))
        return make_error(f'Tile generation failed: {str(e)}'), None


def generate_preview_html(event: dict, clone_dir: str, on_failure: FailCallable) -> tuple[dict|None, None]:
    """ Generate preview.html and upload to S3 alongside preview.pmtiles """
    try:
        destination = event.get('destination', f"s3://{os.environ.get('DATA_BUCKET')}/default/")
        parsed = urllib.parse.urlparse(destination)
        s3_client = boto3.client('s3')

        csv_names = ('country-areas.csv', 'country-boundaries.csv', 'validation-points.csv')
        perspective_set = set()
        for csv_name in csv_names:
            csv_path = os.path.join(clone_dir, csv_name)
            if not os.path.exists(csv_path):
                continue
            with open(csv_path, newline='') as f:
                for row in csv.DictReader(f):
                    for code in row.get('perspectives', '').split(';'):
                        code = code.strip()
                        if code:
                            perspective_set.add(code)
        all_perspectives = sorted(perspective_set)
        perspectives_json = json.dumps(all_perspectives)

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
#controls label { display: block; margin: 3px 0; cursor: pointer; }
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
        url: 'pmtiles://preview.pmtiles'
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
          "fill-color": ["match", ["%", ["get", "index"], 8],
            0, "#7DC0A6",
            1, "#ED936B",
            2, "#919FC7",
            3, "#DA8EC0",
            4, "#B0D767",
            5, "#F9DA56",
            6, "#E0C59A",
            7, "#B3B3B3"
          ],
          "fill-opacity": 0.15
        }
      },
      {
        "id": "boundaries-agreed", "type": "line",
        "source": "protomaps", "source-layer": "boundaries",
        "filter": ["==", ["get", "disputed"], false],
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
        "filter": ["==", ["get", "disputed"], true],
        "paint": {
          "line-color": "rgba(0, 0, 0, 0.35)",
          "line-width": 6,
          "line-dasharray": [1, 2]
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
          "circle-color": "rgba(0, 200, 0, 1)",
          "circle-radius": 6,
          "circle-stroke-color": "rgba(255, 255, 255, 1)",
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
          "text-size": 11,
          "text-offset": [0, 1.2],
          "text-anchor": "top"
        },
        "paint": {
          "text-color": "rgba(0, 100, 0, 1)",
          "text-halo-color": "rgba(255, 255, 255, 1)",
          "text-halo-width": 1.5
        }
      }
    ]
  }
});
map.addControl(new maplibregl.NavigationControl());

const perspectives = $PERSPECTIVES_JSON$;

function perspective_filter(perspective) {
  return ["in", perspective, ["get", "perspectives"]];
}

function apply_perspective(perspective) {
  map.setFilter('areas', perspective_filter(perspective));
  map.setFilter('boundaries-agreed', ["all",
    ["==", ["get", "disputed"], false],
    perspective_filter(perspective)
  ]);
  map.setFilter('boundaries-disputed', ["all",
    ["==", ["get", "disputed"], true],
    perspective_filter(perspective)
  ]);
  map.setFilter('validation-points-interior', ["all",
    ["==", ["get", "relation"], "interior"],
    perspective_filter(perspective)
  ]);
  map.setFilter('validation-points-labels', ["all",
    ["==", ["get", "relation"], "interior"],
    perspective_filter(perspective)
  ]);
}

const radios_div = document.getElementById('perspective-radios');
perspectives.forEach(function(p, i) {
  const label = document.createElement('label');
  const input = document.createElement('input');
  input.type = 'radio';
  input.name = 'perspective';
  input.value = p;
  if (i === 0) { input.checked = true; }
  input.addEventListener('change', function() {
    if (this.checked) { apply_perspective(this.value); }
  });
  label.appendChild(input);
  label.appendChild(document.createTextNode(' ' + p));
  radios_div.appendChild(label);
});

map.on('load', function() {
  if (perspectives.length > 0) {
    apply_perspective(perspectives[0]);
  }
});
</script>
</body>
</html>""".replace('$PERSPECTIVES_JSON$', perspectives_json)

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
        return None, None

    except Exception as e:
        logging.error(f"Failed to generate preview HTML: {e}")
        on_failure('PreviewHTMLError', str(e))
        return make_error(f'Failed to generate preview HTML: {str(e)}'), None
