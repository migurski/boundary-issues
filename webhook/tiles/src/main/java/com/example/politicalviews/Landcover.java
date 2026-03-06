package com.example.politicalviews;

import com.onthegomap.planetiler.FeatureCollector;
import com.onthegomap.planetiler.FeatureMerge;
import com.onthegomap.planetiler.ForwardingProfile;
import com.onthegomap.planetiler.VectorTile;
import com.onthegomap.planetiler.geo.GeometryException;
import com.onthegomap.planetiler.reader.SourceFeature;
import java.util.List;
import java.util.Map;

/**
 * Landcover layer for processing ESA WorldCover data from Daylight.
 *
 * Based on the Landcover layer from Protomaps Basemaps:
 * https://github.com/protomaps/basemaps/blob/main/tiles/src/main/java/com/protomaps/basemap/layers/Landcover.java
 *
 * This implementation depends only on Planetiler and processes the "class" field
 * from daylight-landcover.gpkg GeoPackage files.
 */
public class Landcover implements ForwardingProfile.LayerPostProcessor {

  public static final String LAYER_NAME = "landcover";

  // Constants from Earth layer
  private static final double BUFFER = 0.0625;
  private static final double PIXEL_TOLERANCE = 0.2;

  // Map ESA WorldCover class names to standard kind names
  private static final Map<String, String> KIND_MAPPING = Map.of(
    "urban", "urban_area",
    "crop", "farmland",
    "grass", "grassland",
    "trees", "forest",
    "snow", "glacier",
    "shrub", "scrub"
  );

  // Sort keys to order features consistently in the tile archive
  private static final Map<String, Integer> SORT_KEY_MAPPING = Map.of(
    "barren", 0,
    "snow", 1,
    "crop", 2,
    "shrub", 3,
    "grass", 4,
    "trees", 5
  );

  /**
   * Process landcover features from the Daylight landcover GeoPackage.
   *
   * @param sf Source feature from the GeoPackage
   * @param features Feature collector to emit processed features
   */
  public void process_landcover(SourceFeature sf, FeatureCollector features) {
    String daylight_class = sf.getString("class");
    String kind = KIND_MAPPING.getOrDefault(daylight_class, daylight_class);

    // Polygons are disjoint and non-overlapping, but order them in archive in consistent way
    Integer sort_key = SORT_KEY_MAPPING.getOrDefault(daylight_class, 6);

    features.polygon(LAYER_NAME)
      .setId(1L + sort_key)
      .setAttr("kind", kind)
      .setZoomRange(0, 7)
      .setSortKey(sort_key)
      .setMinPixelSize(1.0)
      .setPixelTolerance(PIXEL_TOLERANCE);
  }

  @Override
  public String name() {
    return LAYER_NAME;
  }

  @Override
  public List<VectorTile.Feature> postProcess(int zoom, List<VectorTile.Feature> items) throws GeometryException {
    // Merge nearby polygons to reduce tile size
    return FeatureMerge.mergeNearbyPolygons(items, 1.0, 1.0, 0.5, BUFFER);
  }
}
