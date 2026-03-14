package com.example.politicalviews;

import com.onthegomap.planetiler.FeatureCollector;
import com.onthegomap.planetiler.ForwardingProfile;
import com.onthegomap.planetiler.VectorTile;
import com.onthegomap.planetiler.geo.GeometryException;
import com.onthegomap.planetiler.reader.SourceFeature;
import java.util.List;

/**
 * Points layer: point features from validation-points.geojson.
 *
 * GeoJSON properties: iso3, perspectives, relation ("interior" or "exterior")
 */
public class Points implements ForwardingProfile.LayerPostProcessor {

  public static final String LAYER_NAME = "points";
  public static final String SOURCE_NAME = "points";

  public void process_point(SourceFeature sf, FeatureCollector features) {
    features.point(LAYER_NAME)
      .setAttr("iso3", sf.getString("iso3"))
      .setAttr("perspectives", sf.getString("perspectives"))
      .setAttr("relation", sf.getString("relation"))
      .setZoomRange(0, 7);
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
