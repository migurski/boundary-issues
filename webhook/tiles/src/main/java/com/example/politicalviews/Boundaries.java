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
 * GeoJSON properties: iso3a, iso3b, perspectives, disputed (boolean)
 *
 * Each row from the source CSV produces up to two GeoJSON features:
 * one for agreed_geometry (disputed=false) and one for disputed_geometry (disputed=true).
 */
public class Boundaries implements ForwardingProfile.LayerPostProcessor {

  public static final String LAYER_NAME = "boundaries";
  public static final String SOURCE_NAME = "boundaries";

  public void process_boundary(SourceFeature sf, FeatureCollector features) {
    features.line(LAYER_NAME)
      .setAttr("index", sf.getLong("index"))
      .setAttr("iso3a", sf.getString("iso3a"))
      .setAttr("iso3b", sf.getString("iso3b"))
      .setAttr("perspectives", sf.getString("perspectives"))
      .setAttr("disputed", sf.getBoolean("disputed"))
      .setZoomRange(0, 7)
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
