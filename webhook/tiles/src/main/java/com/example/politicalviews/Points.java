package com.example.politicalviews;

import com.onthegomap.planetiler.FeatureCollector;
import com.onthegomap.planetiler.ForwardingProfile;
import com.onthegomap.planetiler.VectorTile;
import com.onthegomap.planetiler.expression.Expression;
import com.onthegomap.planetiler.geo.GeometryException;
import com.onthegomap.planetiler.reader.SourceFeature;
import java.util.List;

/**
 * Points layer: point features from the validation-points layer of out.gpkg.
 *
 * Properties: iso3, perspectives, relation ("interior" or "exterior")
 */
public class Points implements ForwardingProfile.FeatureProcessor, ForwardingProfile.LayerPostProcessor {

  public static final String LAYER_NAME = "points";
  public static final String SOURCE_NAME = "political";

  @Override
  public Expression filter() {
    return Expression.matchSourceLayer("validation-points");
  }

  @Override
  public void processFeature(SourceFeature sf, FeatureCollector features) {
    features.point(LAYER_NAME)
      .setAttr("color_index", sf.getLong("color_index"))
      .setAttr("iso3", sf.getString("iso3"))
      .setAttr("perspectives", sf.getString("perspectives"))
      .setAttr("relation", sf.getString("relation"))
      .setZoomRange(0, 18);
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
