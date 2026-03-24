package com.example.politicalviews;

import com.onthegomap.planetiler.FeatureCollector;
import com.onthegomap.planetiler.ForwardingProfile;
import com.onthegomap.planetiler.VectorTile;
import com.onthegomap.planetiler.geo.GeometryException;
import com.onthegomap.planetiler.reader.SourceFeature;
import java.util.List;

/**
 * Areas layer: polygon features from country-areas.geojson.
 *
 * GeoJSON properties: iso3, perspectives
 */
public class Areas implements ForwardingProfile.LayerPostProcessor {

  public static final String LAYER_NAME = "areas";
  public static final String SOURCE_NAME = "areas";

  public void process_area(SourceFeature sf, FeatureCollector features) {
    features.polygon(LAYER_NAME)
      .setAttr("index", sf.getLong("index"))
      .setAttr("iso3", sf.getString("iso3"))
      .setAttr("perspectives", sf.getString("perspectives"))
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
