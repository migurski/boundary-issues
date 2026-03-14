# Plan: Generate PMTiles in processor.py and upload to S3

## Context

The pipeline processes GitHub PRs that modify country boundary/area config files. It clones the repo, runs `build-country-polygon.py`, and uploads the resulting CSVs to S3. The state machine calls the processor twice: a "first" run (which runs `build-country-polygon.py` without `--ignore-locals`, producing CSVs from local data) and a "second" run (which runs after a 5-minute wait with fresh OSM data). Tile generation should happen on the **first** run (`taskSequence == "first"`), since that's when CSVs are produced from local files (not ignoring locals).

The JAR is already baked into the Docker image at `/var/task/tiles.jar`. It currently only processes landcover from `daylight-landcover.gpkg`. We need to add two more layers — "areas" and "boundaries" — sourced from the CSVs that `build-country-polygon.py` produces.

## Step 1: Modify PoliticalViewsProfile.java — add "areas" and "boundaries" layers

The JAR currently only has a `Landcover` layer from a GeoPackage source. We need to add two CSV-backed layers:

- **"areas"** layer: polygon features from `country-areas.csv`
- **"boundaries"** layer: linestring features from `country-boundaries.csv`

**CSV format (confirmed):** Both CSVs have columns `iso3, perspectives, geometry` where `geometry` is a WKT string (MULTIPOLYGON for areas, MULTILINESTRING for boundaries).

**Action:** Add two new source handlers / layer classes. Planetiler has a CSV reader that can handle WKT geometry — implement via a custom `SimpleReader` or `NaturalEarthReader`-style approach.

**CLI interface:** Add `--areas` and `--boundaries` arguments pointing to the CSV file paths.

**Files to modify:**
- `webhook/tiles/src/main/java/com/example/politicalviews/PoliticalViewsProfile.java`
- New: `Areas.java` (polygon layer), `Boundaries.java` (linestring layer)

## Step 2: Modify processor.py — add `generate_tiles()` step

Add a new function `generate_tiles(event, clone_dir, on_failure)` that:

1. Confirms `country-areas.csv` and `country-boundaries.csv` exist in `clone_dir`
2. Runs the JAR:
   ```
   java -jar /var/task/tiles.jar \
     --areas=<clone_dir>/country-areas.csv \
     --boundaries=<clone_dir>/country-boundaries.csv \
     --data=/tmp/tiles-data \
     --output=/tmp/preview.pmtiles \
     --force
   ```
   `--data` points to a writable directory for the landcover GeoPackage cache (auto-downloaded if absent).
3. Uploads `/tmp/preview.pmtiles` to S3 at the same destination path alongside the CSVs.

**Trigger condition:** Call `generate_tiles()` only when `event.get('taskSequence') == 'first'`.

In `handler()`, insert after `upload_to_s3()`:
```python
if event.get('taskSequence') == 'first':
    err8, _ = generate_tiles(event, clone_dir, on_failure)
    if err8:
        return err8
```

**File to modify:** `processor.py`

## Critical files

| File | Change |
|------|--------|
| `processor.py` | Add `generate_tiles()`, call it when `taskSequence == 'first'` |
| `webhook/tiles/src/main/java/com/example/politicalviews/PoliticalViewsProfile.java` | Add `--areas`, `--boundaries`, `--data` CLI args and register new sources |
| `webhook/tiles/src/main/java/com/example/politicalviews/Areas.java` (new) | Polygon layer from areas CSV |
| `webhook/tiles/src/main/java/com/example/politicalviews/Boundaries.java` (new) | Linestring layer from boundaries CSV |

## Verification

1. Locally invoke the JAR with `--areas`, `--boundaries`, `--output` against existing CSVs to confirm PMTiles generation.
2. Run `processor.py` handler with a synthetic event containing `taskSequence: "first"` to confirm the tile generation step runs.
3. Confirm `preview.pmtiles` appears in S3 alongside CSVs after a real PR triggers the first invocation.
