package com.example.politicalviews;

import com.onthegomap.planetiler.ForwardingProfile;
import com.onthegomap.planetiler.Planetiler;
import com.onthegomap.planetiler.config.Arguments;
import com.protomaps.basemap.layers.Landcover;
import java.io.IOException;
import java.nio.file.Path;

/**
 * Custom Planetiler profile that uses only the Landcover layer from Protomaps Basemap.
 *
 * This demonstrates using protomaps-basemaps as a third-party library, similar to how
 * protomaps-basemaps uses Planetiler as a library.
 */
public class PoliticalViewsProfile extends ForwardingProfile {

  public PoliticalViewsProfile() {
    // Register the Landcover layer from protomaps-basemaps
    var landcover = new Landcover();
    registerHandler(landcover);
    registerSourceHandler("landcover", landcover::processLandcover);
  }

  @Override
  public String name() {
    return "Political Views Profile";
  }

  @Override
  public String description() {
    return "Minimal profile using only landcover layer from Protomaps Basemap";
  }

  @Override
  public String version() {
    return "1.0.0";
  }

  @Override
  public boolean isOverlay() {
    return false;
  }

  @Override
  public String attribution() {
    return """
      <a href="https://www.openstreetmap.org/copyright" target="_blank">&copy; OpenStreetMap</a>
      """.trim();
  }

  public static void main(String[] args) throws IOException {
    // Check for help flag
    for (String arg : args) {
      if ("--version".equals(arg) || "-v".equals(arg)) {
        printVersion();
        System.exit(0);
      }

      if ("--help".equals(arg) || "-h".equals(arg)) {
        printHelp();
        System.exit(0);
      }
    }
    run(Arguments.fromArgsOrConfigFile(args));
  }

  private static void printVersion() {
    PoliticalViewsProfile profile = new PoliticalViewsProfile();
    System.out.println(profile.version());
  }

  private static void printHelp() {
    PoliticalViewsProfile profile = new PoliticalViewsProfile();
    System.out.println(String.format("""
      %s v%s
      %s

      Usage:
        java -jar political-views-tiles-1.0.0-with-deps.jar [options]

      Options:
        --help, -h              Show this help message and exit
        --version, -v           Show version and exit
        --output=<path>         Output file path (default: output.pmtiles)
        --maxzoom=<n>           Maximum zoom level (default: 7)
        --force                 Overwrite existing output file

      Example:
        java -jar political-views-tiles-1.0.0-with-deps.jar --output=landcover.pmtiles --force

      For a complete list of Planetiler options, see:
        https://github.com/onthegomap/planetiler#usage
      """, profile.name(), profile.version(), profile.description()));
  }

  static void run(Arguments args) throws IOException {
    // Set default maxzoom to 7 (landcover only goes to z7)
    args = args.orElse(Arguments.of("maxzoom", 7));

    Path dataDir = Path.of("data");
    Path sourcesDir = dataDir.resolve("sources");

    // Add the daylight-landcover.gpkg source
    // This will be downloaded automatically if not present
    var planetiler = Planetiler.create(args)
      .addGeoPackageSource("landcover", sourcesDir.resolve("daylight-landcover.gpkg"),
        "https://r2-public.protomaps.com/datasets/daylight-landcover.gpkg");

    // Set the profile and output
    String outputName = args.getString("output", "Output PMTiles path", "output.pmtiles");

    planetiler
      .setProfile(new PoliticalViewsProfile())
      .setOutput(Path.of(outputName))
      .run();
  }
}
