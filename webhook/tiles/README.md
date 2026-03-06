# Political Views Tiles

A demonstration project showing how to use [Protomaps Basemaps](https://github.com/protomaps/basemaps) as a third-party library in another Java application. This project creates PMTiles files containing only the landcover layer, rendered up to zoom level 7.

## Overview

This project mirrors the relationship between Protomaps Basemaps and Planetiler:
- **Planetiler** is a framework published to Maven Central
- **Protomaps Basemaps** uses Planetiler as a library and extends it with map-specific layers
- **This project** uses Protomaps Basemaps as a library and selectively uses its layers

By depending on `com.protomaps.basemap:protomaps-basemap:HEAD`, we gain access to all the layer implementations (Water, Roads, Buildings, Landcover, etc.) and can cherry-pick which ones to use in our custom profile.

## What's Included

This minimal profile includes only:
- **Landcover layer** from Protomaps Basemaps
  - Data source: [Daylight Landcover](https://r2-public.protomaps.com/datasets/daylight-landcover.gpkg) (ESA WorldCover)
  - Zoom levels: 0-7
  - Feature types: urban_area, farmland, grassland, forest, glacier, scrub, barren
  - ~144k features processed into ~9k tiles

## Prerequisites

1. **Java 21+** - Required by Planetiler and Protomaps Basemaps
   ```bash
   java --version
   ```

2. **Maven** - For building the project
   ```bash
   mvn --version
   ```

3. **Protomaps Basemaps** - Must be installed to local Maven repository
   ```bash
   cd ~/Documents/protomaps-basemaps/tiles
   mvn clean install
   ```
   This installs `com.protomaps.basemap:protomaps-basemap:HEAD` to `~/.m2/repository/`

## Building

From this directory:

```bash
mvn clean package
```

This creates:
- `target/political-views-tiles-1.0.0.jar` - Small JAR (5.3 KB)
- `target/political-views-tiles-1.0.0-with-deps.jar` - Executable fat JAR (~83 MB)

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
  download: Download sources [landcover]
  landcover: Process features in data/sources/daylight-landcover.gpkg
  sort: Sort rendered features by tile ID
  archive: Encode each tile and write to PMTiles

Finished in 34s
  # features: 144,514
  # tiles: 8,934
  archive: 11MB
```

## Project Structure

```
.
├── pom.xml                           # Maven configuration
├── README.md                         # This file
├── src/main/java/
│   └── com/example/politicalviews/
│       └── PoliticalViewsProfile.java   # Custom Planetiler profile
├── data/
│   └── sources/
│       └── daylight-landcover.gpkg     # Downloaded data (36MB)
└── target/
    ├── political-views-tiles-1.0.0-with-deps.jar  # Executable JAR
    └── landcover-test.pmtiles                      # Generated tiles (11MB)
```

## Architecture

### How This Works

1. **Dependency Chain:**
   ```
   This Project
     └─ depends on ─> Protomaps Basemaps (local Maven)
                        └─ depends on ─> Planetiler (Maven Central)
   ```

2. **Custom Profile (PoliticalViewsProfile.java):**
   ```java
   public class PoliticalViewsProfile extends ForwardingProfile {
     public PoliticalViewsProfile() {
       // Instantiate the Landcover layer from protomaps-basemaps
       var landcover = new Landcover();
       registerHandler(landcover);
       registerSourceHandler("landcover", landcover::processLandcover);
     }
   }
   ```

3. **Build Process:**
   - Extends Planetiler's `ForwardingProfile`
   - Uses Protomaps Basemap's `Landcover` layer class
   - Registers only the landcover source and handler
   - Planetiler framework handles the ETL pipeline:
     - Download: Fetch daylight-landcover.gpkg
     - Process: Run features through Landcover layer
     - Sort: Order features by tile ID
     - Archive: Write to PMTiles format

### Reusable Components from Protomaps Basemaps

When you depend on `protomaps-basemap`, you get access to:

**Layer Classes:**
- `com.protomaps.basemap.layers.Water`
- `com.protomaps.basemap.layers.Roads`
- `com.protomaps.basemap.layers.Buildings`
- `com.protomaps.basemap.layers.Landcover`
- `com.protomaps.basemap.layers.Landuse`
- `com.protomaps.basemap.layers.Places`
- `com.protomaps.basemap.layers.Pois`
- `com.protomaps.basemap.layers.Transit`
- `com.protomaps.basemap.layers.Boundaries`
- `com.protomaps.basemap.layers.Earth`

**Feature Utilities:**
- `com.protomaps.basemap.feature.CountryCoder` - Country detection
- `com.protomaps.basemap.feature.QrankDb` - Place ranking
- `com.protomaps.basemap.feature.Matcher` - Feature matching utilities

**Name Handling:**
- `com.protomaps.basemap.names.OsmNames` - OSM name processing
- `com.protomaps.basemap.names.NeNames` - Natural Earth names
- `com.protomaps.basemap.names.ScriptSegmenter` - Script detection

**Post-processors:**
- `com.protomaps.basemap.postprocess.Clip` - Geometry clipping
- `com.protomaps.basemap.postprocess.LinkSimplify` - Road network simplification
- `com.protomaps.basemap.postprocess.Area` - Area calculations

## Extending This Project

### Add More Layers

To include additional layers from Protomaps Basemaps:

```java
public PoliticalViewsProfile() {
  // Add landcover
  var landcover = new Landcover();
  registerHandler(landcover);
  registerSourceHandler("landcover", landcover::processLandcover);

  // Add water
  var water = new Water();
  registerHandler(water);
  registerSourceHandler("osm", water::processOsm);

  // Add roads (requires CountryCoder)
  var countryCoder = CountryCoder.fromJarResource();
  var roads = new Roads(countryCoder);
  registerHandler(roads);
  registerSourceHandler("osm", roads::processOsm);
}
```

Then add the corresponding data sources in `run()`:

```java
var planetiler = Planetiler.create(args)
  .addGeoPackageSource("landcover", sourcesDir.resolve("daylight-landcover.gpkg"),
    "https://r2-public.protomaps.com/datasets/daylight-landcover.gpkg")
  .addOsmSource("osm", Path.of("data", "sources", "monaco.osm.pbf"),
    "geofabrik:monaco");
```

### Use Different Data Sources

The Landcover layer also supports:
- **Natural Earth:** `landcover::processNe`
- **Overture Maps:** `landcover::processOverture`

Example using Overture:
```java
planetiler.addParquetSource("pm:overture",
  List.of(Path.of("overture-data.parquet")),
  false,
  fields -> fields.get("id"),
  fields -> fields.get("type")
);
registerSourceHandler("pm:overture", landcover::processOverture);
```

## Comparison to Full Protomaps Basemaps Build

| Aspect | This Project | Protomaps Basemaps (Monaco) |
|--------|--------------|------------------------------|
| **Layers** | 1 (landcover only) | 10 (all layers) |
| **Data Sources** | daylight-landcover.gpkg | OSM + Natural Earth + landcover + water/land |
| **Output Size** | 11 MB (global landcover z0-z7) | ~50 MB (Monaco z0-z15) |
| **Build Time** | 34 seconds | ~2-5 minutes |
| **Dependencies** | protomaps-basemap as library | planetiler as library |

## Troubleshooting

### "Could not find artifact com.protomaps.basemap:protomaps-basemap:jar:HEAD"

You need to install protomaps-basemaps to your local Maven repository:
```bash
cd ~/Documents/protomaps-basemaps/tiles
mvn clean install
```

### Out of Memory Errors

Increase Java heap size:
```bash
java -Xmx8g -jar target/political-views-tiles-1.0.0-with-deps.jar --output=landcover.pmtiles --force
```

### Tile Generation is Slow

The landcover layer only goes to z7 by default. If you've modified maxzoom, note that tile count increases exponentially with each zoom level.

## License

This example project follows the same licensing as Protomaps Basemaps:
- Code: BSD-3
- Tilesets are produced works of OpenStreetMap data under ODbL

## Links

- [Protomaps Basemaps](https://github.com/protomaps/basemaps)
- [Planetiler](https://github.com/onthegomap/planetiler)
- [PMTiles Specification](https://github.com/protomaps/PMTiles)
- [Daylight Landcover](https://daylightmap.org/2023/05/04/landcover.html)

## Approach: Maven Local Install

This project demonstrates **Approach 1: Maven Local Install** for using protomaps-basemaps as a third-party library. This is analogous to how protomaps-basemaps uses Planetiler:

1. **Planetiler** → Published to Maven Central → Protomaps Basemaps pulls as dependency
2. **Protomaps Basemaps** → Installed locally → This project pulls as dependency

Alternative approaches for production use:
- **JitPack:** Auto-build from GitHub (no local install needed)
- **Git Submodule + Multi-Module Maven:** Both projects in parent POM
- **Publish to Private Maven Repository:** For team/organization use
