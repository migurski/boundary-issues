package com.example.politicalviews;

import com.onthegomap.planetiler.ForwardingProfile;
import com.onthegomap.planetiler.Planetiler;
import com.onthegomap.planetiler.config.Arguments;
import java.io.IOException;
import java.nio.file.Path;

/**
 * Custom Planetiler profile that processes landcover, areas, boundaries, and validation points.
 *
 * This profile implements:
 * - A landcover layer from daylight-landcover.gpkg
 * - An areas layer from the country-areas layer of out.gpkg (polygon features)
 * - A boundaries layer from the country-boundaries layer of out.gpkg (linestring features)
 * - A points layer from the validation-points layer of out.gpkg (point features)
 */
public class PoliticalViewsProfile extends ForwardingProfile {

  public PoliticalViewsProfile() {
    var landcover = new Landcover();
    registerHandler(landcover);
    registerSourceHandler("landcover", landcover::process_landcover);

    var areas = new Areas();
    registerHandler(areas);
    registerSourceHandler("political", areas);

    var boundaries = new Boundaries();
    registerHandler(boundaries);
    registerSourceHandler("political", boundaries);

    var points = new Points();
    registerHandler(points);
    registerSourceHandler("political", points);
  }

  @Override
  public String name() {
    return "Political Views Profile";
  }

  @Override
  public String description() {
    return "Profile using Planetiler to generate landcover, areas, and boundary tiles";
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
    for (String arg : args) {
      if ("--version".equals(arg) || "-v".equals(arg)) {
        System.out.println(new PoliticalViewsProfile().version());
        System.exit(0);
      }
      if ("--help".equals(arg) || "-h".equals(arg)) {
        printHelp();
        System.exit(0);
      }
    }
    run(Arguments.fromArgsOrConfigFile(args));
  }

  private static void printHelp() {
    var profile = new PoliticalViewsProfile();
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
        --gpkg=<path>           Path to out.gpkg (contains country-areas, country-boundaries, validation-points layers)
        --data=<path>           Directory for downloaded data files (default: data)

      Example:
        java -jar political-views-tiles-1.0.0-with-deps.jar \\
          --gpkg=out.gpkg \\
          --output=preview.pmtiles --force

      For a complete list of Planetiler options, see:
        https://github.com/onthegomap/planetiler#usage
      """, profile.name(), profile.version(), profile.description()));
  }

  static void run(Arguments args) throws IOException {
    args = args.orElse(Arguments.of("maxzoom", 7));

    String data_dir = args.getString("data", "Directory for downloaded data files", "data");
    Path sources_dir = Path.of(data_dir).resolve("sources");

    String gpkg_path = args.getString("gpkg", "Path to out.gpkg", "");
    String output_name = args.getString("output", "Output PMTiles path", "output.pmtiles");
    String landcover_path = args.getString("landcover_path", "Path to daylight-landcover.gpkg",
      sources_dir.resolve("daylight-landcover.gpkg").toString());

    var planetiler = Planetiler.create(args)
      .addGeoPackageSource("landcover", Path.of(landcover_path),
        "https://r2-public.protomaps.com/datasets/daylight-landcover.gpkg");

    if (!gpkg_path.isBlank()) {
      planetiler.addGeoPackageSource("political", Path.of(gpkg_path), "");
    }

    planetiler
      .setProfile(new PoliticalViewsProfile())
      .setOutput(Path.of(output_name))
      .run();
  }
}
