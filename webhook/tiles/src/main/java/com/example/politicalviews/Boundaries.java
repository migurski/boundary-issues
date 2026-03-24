package com.example.politicalviews;

import com.onthegomap.planetiler.FeatureCollector;
import com.onthegomap.planetiler.ForwardingProfile;
import com.onthegomap.planetiler.VectorTile;
import com.onthegomap.planetiler.geo.GeometryException;
import com.onthegomap.planetiler.reader.SourceFeature;
import java.util.List;

/**
 * Boundaries layer: linestring features from country-boundaries.geojson.
 *
 * GeoJSON properties: stable, disputed, nonexistent (semicolon-delimited ISO3 strings), index (long)
 *
 * Each row from the source CSV produces one GeoJSON feature.
 * stable: countries that agree this boundary exists (solid line)
 * disputed: countries that see this boundary as contested (dashed line)
 * nonexistent: countries that don't recognize this boundary (hidden)
 */
public class Boundaries implements ForwardingProfile.LayerPostProcessor {

  public static final String LAYER_NAME = "boundaries";
  public static final String SOURCE_NAME = "boundaries";

  public void process_boundary(SourceFeature sf, FeatureCollector features) {
    features.line(LAYER_NAME)
      .setAttr("index", sf.getLong("index"))
      .setAttr("stable", sf.getString("stable"))
      .setAttr("disputed", sf.getString("disputed"))
      .setAttr("nonexistent", sf.getString("nonexistent"))
      .setZoomRange(0, 18)
      .setMinPixelSize(1.0);
  }

  @Override
  public String name() {
    return LAYER_NAME;
  }

  @Override
  public List<VectorTile.Feature> postProcess(int zoom, List<VectorTile.Feature> items)
      throws GeometryException {
    return items;
  }
}
