# Political Views Tiles

A minimal Planetiler-based project that generates PMTiles files containing landcover data from [Daylight](https://daylightmap.org/)'s ESA WorldCover dataset, rendered up to zoom level 7.

## Overview

This project demonstrates building custom vector tiles with [Planetiler](https://github.com/onthegomap/planetiler), implementing a single landcover layer that processes GeoPackage data. The implementation is based on the landcover layer from [Protomaps Basemaps](https://github.com/protomaps/basemaps) but depends only on Planetiler.

## What's Included

This minimal profile includes:
- **Landcover layer** processing ESA WorldCover data
  - Data source: [Daylight Landcover](https://r2-public.protomaps.com/datasets/daylight-landcover.gpkg) (36MB download)
  - Zoom levels: 0-7
  - Feature types: urban_area, farmland, grassland, forest, glacier, scrub, barren
  - ~144k features processed into ~9k tiles

## Prerequisites

1. **Java 21+** - Required by Planetiler
   ```bash
   java --version
   ```

2. **Maven** - For building the project
   ```bash
   mvn --version
   ```

## Building

From this directory:

```bash
mvn clean package
```

This creates:
- `target/political-views-tiles-1.0.0.jar` - Small JAR (6 KB)
- `target/political-views-tiles-1.0.0-with-deps.jar` - Executable fat JAR (~83 MB)

Build time: ~17 seconds

## Usage

### Generate a PMTiles file

```bash
java -jar target/political-views-tiles-1.0.0-with-deps.jar \\
  --output=landcover.pmtiles \\
  --force \\
  --download
```

**Command-line options:**
- `--output=<path>` - Output PMTiles file path (default: `output.pmtiles`)
- `--force` - Overwrite existing output file
- `--download` - Download data sources if not present
- `--maxzoom=<n>` - Maximum zoom level (default: 7, landcover only goes to z7)

All standard [Planetiler options](https://github.com/onthegomap/planetiler#usage) are supported.

### View help

```bash
java -jar target/political-views-tiles-1.0.0-with-deps.jar --help
```

### Check version

```bash
java -jar target/political-views-tiles-1.0.0-with-deps.jar --version
```

## Example Output

Running the default build:

```
Building PoliticalViewsProfile profile into landcover-test.pmtiles
  landcover: Process features in data/sources/daylight-landcover.gpkg
  sort: Sort rendered features by tile ID
  archive: Encode each tile and write to PMTiles

Finished in 24s
  # features: 144,514
  # tiles: 8,934
  archive: 11MB
```

## Project Structure

```
.
├── pom.xml                           # Maven configuration
├── README.md                         # This file
├── src/main/java/com/example/politicalviews/
│   ├── Landcover.java                # Landcover layer implementation
│   └── PoliticalViewsProfile.java    # Planetiler profile
├── data/
│   └── sources/
│       └── daylight-landcover.gpkg   # Downloaded data (36MB)
└── target/
    ├── political-views-tiles-1.0.0-with-deps.jar  # Executable JAR
    └── landcover-test.pmtiles                      # Generated tiles (11MB)
```

## Architecture

### Planetiler Profile

The core of this project is `PoliticalViewsProfile`, a custom Planetiler profile:

```java
public class PoliticalViewsProfile extends ForwardingProfile {
  public PoliticalViewsProfile() {
    var landcover = new Landcover();
    registerHandler(landcover);
    registerSourceHandler("landcover", landcover::process_landcover);
  }
}
```

It extends `ForwardingProfile` and registers a single layer handler for processing landcover features.

### Landcover Layer

The `Landcover` class implements `ForwardingProfile.LayerPostProcessor` and:

1. **Maps ESA WorldCover classes** to standard kind names:
   - `urban` → `urban_area`
   - `crop` → `farmland`
   - `grass` → `grassland`
   - `trees` → `forest`
   - `snow` → `glacier`
   - `shrub` → `scrub`
   - `barren` → `barren`

2. **Processes GeoPackage features** from the "class" field in daylight-landcover.gpkg

3. **Post-processes tiles** by merging nearby polygons to reduce tile size

4. **Emits features** with:
   - Zoom range: 0-7
   - Sort keys for consistent ordering
   - Pixel tolerance: 0.2
   - Buffer: 0.0625

### Build Process

The Planetiler ETL pipeline:

1. **Download** - Fetch daylight-landcover.gpkg (if --download flag set)
2. **Process** - Read GeoPackage features and apply Landcover layer logic
3. **Sort** - Order processed features by tile ID
4. **Archive** - Encode and write to PMTiles format

## Extending This Project

### Add More Layers

To add additional layers, create new layer classes that implement `ForwardingProfile.LayerPostProcessor`:

```java
public class Water implements ForwardingProfile.LayerPostProcessor {
  public void process_osm(SourceFeature sf, FeatureCollector features) {
    // Process OSM water features
    if (sf.hasTag("natural", "water")) {
      features.polygon("water")
        .setAttr("kind", "water")
        .setZoomRange(0, 14);
    }
  }

  @Override
  public String name() {
    return "water";
  }

  @Override
  public List<VectorTile.Feature> postProcess(int zoom, List<VectorTile.Feature> items) {
    return FeatureMerge.mergeNearbyPolygons(items, 1.0, 1.0, 0.5, 0.0625);
  }
}
```

Then register it in the profile:

```java
public PoliticalViewsProfile() {
  var landcover = new Landcover();
  registerHandler(landcover);
  registerSourceHandler("landcover", landcover::process_landcover);

  var water = new Water();
  registerHandler(water);
  registerSourceHandler("osm", water::process_osm);
}
```

And add the data source:

```java
var planetiler = Planetiler.create(args)
  .addGeoPackageSource("landcover", sourcesDir.resolve("daylight-landcover.gpkg"),
    "https://r2-public.protomaps.com/datasets/daylight-landcover.gpkg")
  .addOsmSource("osm", Path.of("data", "sources", "monaco.osm.pbf"),
    "geofabrik:monaco");
```

### Process Different Data Sources

Planetiler supports multiple input formats:
- **OSM PBF** - `addOsmSource()`
- **GeoPackage** - `addGeoPackageSource()`
- **Shapefile** - `addShapefileSource()`
- **Parquet** - `addParquetSource()`

Example processing Overture Maps data:

```java
planetiler.addParquetSource("overture",
  List.of(Path.of("overture-land.parquet")),
  false,
  fields -> fields.get("id"),
  fields -> fields.get("type")
);

registerSourceHandler("overture", landcover::process_overture);
```

### Customize Processing Logic

Modify `Landcover.java` to customize the processing:

- **Change zoom range**: Modify `.setZoomRange(0, 7)` to process different zoom levels
- **Add attributes**: Use `.setAttr(key, value)` to add more feature properties
- **Filter features**: Add conditionals in `process_landcover()` to skip certain features
- **Adjust simplification**: Modify `PIXEL_TOLERANCE` and `BUFFER` constants

## Performance Notes

**Build Performance:**
- **Landcover processing**: 12 seconds (144k features)
- **Sort**: 0.4 seconds
- **Archive**: 10 seconds
- **Total**: 24 seconds

**Output Size:**
- **Uncompressed features**: 19 MB
- **Compressed PMTiles**: 11 MB
- **Compression ratio**: ~58%

**Scaling:**
- Each zoom level quadruples the number of potential tiles
- Landcover data only goes to z7 (8,934 tiles)
- Processing all 10 layers to z15 (like full Protomaps Basemaps) takes 2-5 minutes for Monaco

## Comparison to Protomaps Basemaps

| Aspect | This Project | Protomaps Basemaps (Monaco) |
|--------|--------------|------------------------------|
| **Dependencies** | Planetiler only | Planetiler + custom utilities |
| **Layers** | 1 (landcover) | 10 (all map layers) |
| **Data Sources** | daylight-landcover.gpkg | OSM + Natural Earth + landcover + water/land polygons |
| **Output Size** | 11 MB (global landcover z0-z7) | ~50 MB (Monaco z0-z15) |
| **Build Time** | 24 seconds | ~2-5 minutes |
| **Code Size** | ~200 lines | ~10,000+ lines |

## Troubleshooting

### Out of Memory Errors

Increase Java heap size:
```bash
java -Xmx8g -jar target/political-views-tiles-1.0.0-with-deps.jar --output=landcover.pmtiles --force
```

### Tile Generation is Slow

The landcover layer only goes to z7 by default. If you've modified maxzoom, note that tile count increases exponentially (4^zoom) with each zoom level.

### Data Download Fails

If the automatic download fails, manually download the GeoPackage:
```bash
mkdir -p data/sources
curl -o data/sources/daylight-landcover.gpkg \\
  https://r2-public.protomaps.com/datasets/daylight-landcover.gpkg
```

## License

This example project follows the same licensing as Protomaps Basemaps:
- **Code**: BSD-3
- **Tilesets** are produced works of OpenStreetMap data under ODbL

## Links

- [Planetiler](https://github.com/onthegomap/planetiler) - The framework powering this project
- [Protomaps Basemaps](https://github.com/protomaps/basemaps) - Inspiration for the landcover layer
- [PMTiles Specification](https://github.com/protomaps/PMTiles) - Tile archive format
- [Daylight Landcover](https://daylightmap.org/2023/05/04/landcover.html) - ESA WorldCover data source
- [Planetiler Documentation](https://github.com/onthegomap/planetiler/blob/main/PLANET.md) - Creating custom profiles
