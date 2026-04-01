#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import typing

import geopandas
import osgeo.gdal
import osgeo.ogr

osgeo.gdal.UseExceptions()
osgeo.ogr.UseExceptions()

BOUNDARIES_NAME = "country-boundaries"
AREAS_NAME = "country-areas"
UNIQUE_PERSPECTIVES_NAME = "unique-perspectives"
D2 = ";"


def code_in_field(code: str, field_value: typing.Any) -> bool:
    if not field_value:
        return False
    return code in str(field_value).split(D2)


def load_perspective_groups(gpkg_path: str) -> tuple[list[str], list[list[str]]]:
    """
    Returns (unique_perspectives, others_groups) where:
    - unique_perspectives: iso3 codes that each have their own perspective_id
    - others_groups: list of iso3 lists that share a perspective_id
    """
    df = geopandas.read_file(gpkg_path, layer=UNIQUE_PERSPECTIVES_NAME)
    groups = df.groupby("perspective_id")["iso3"].apply(list)
    unique_perspectives: list[str] = []
    others_groups: list[list[str]] = []
    for iso3_list in groups:
        if len(iso3_list) == 1:
            unique_perspectives.extend(iso3_list)
        else:
            others_groups.append(sorted(iso3_list))
    return sorted(unique_perspectives), others_groups


def filter_areas(gpkg_path: str, perspective: str) -> geopandas.GeoDataFrame:
    gdf = geopandas.read_file(gpkg_path, layer=AREAS_NAME)
    mask = gdf["perspectives"].apply(lambda v: code_in_field(perspective, v))
    result = gdf[mask][["iso3", "perspectives", "color_index", "geometry"]].copy()
    return result


def filter_boundaries(gpkg_path: str, perspective: str) -> geopandas.GeoDataFrame:
    gdf = geopandas.read_file(gpkg_path, layer=BOUNDARIES_NAME)
    in_stable = gdf["stable"].apply(lambda v: code_in_field(perspective, v))
    in_disputed = gdf["disputed"].apply(lambda v: code_in_field(perspective, v))
    mask = in_stable | in_disputed
    subset = gdf[mask].copy()
    subset["display"] = subset.apply(
        lambda row: "stable" if code_in_field(perspective, row["stable"]) else "disputed",
        axis=1,
    )
    return subset[["display", "geometry"]].copy()


def write_perspective_gpkg(input_path: str, output_path: str, perspective: str) -> None:
    if os.path.exists(output_path):
        os.remove(output_path)

    areas = filter_areas(input_path, perspective)
    boundaries = filter_boundaries(input_path, perspective)

    areas.to_file(output_path, layer="areas", driver="GPKG")
    boundaries.to_file(output_path, layer="boundaries", driver="GPKG")
    print(
        f"Wrote {output_path}: {len(areas)} areas, {len(boundaries)} boundaries",
        file=sys.stderr,
    )


def main(input_path: str, output_dir: str) -> None:
    unique_perspectives, others_groups = load_perspective_groups(input_path)

    for iso3 in unique_perspectives:
        output_path = os.path.join(output_dir, f"perspective-{iso3}.gpkg")
        write_perspective_gpkg(input_path, output_path, iso3)

    for group in others_groups:
        perspective = group[0]  # first alphabetically
        output_path = os.path.join(output_dir, "perspective-Other.gpkg")
        write_perspective_gpkg(input_path, output_path, perspective)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Render per-perspective GeoPackage files from out.gpkg"
    )
    parser.add_argument("--input", default="out.gpkg", help="Path to input GPKG (default: out.gpkg)")
    parser.add_argument("--output-dir", default=".", help="Directory for output files (default: .)")
    args = parser.parse_args()
    main(args.input, args.output_dir)
