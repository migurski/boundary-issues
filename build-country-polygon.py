#!/usr/bin/env python3
from __future__ import annotations

import csv
import functools
import gzip
import os
import sys
import urllib.request

import osgeo.gdal
import osgeo.ogr

osgeo.gdal.UseExceptions()

CONFIGS = {
    "CHN": {
        "base": [
            ["plus", "relation", 270056], # China
        ],
        "perspectives": {
            "CHN": [
                ["plus", "relation", 7935380], # Trans-Karakoram Tract
                ["plus", "relation", 2713466], # Demchok sector
                ["plus", "relation", 2713483], # Gue-Kaurik
                ["plus", "relation", 2713485], # Shipki La
                ["plus", "relation", 2713676], # Nilang-Jadhang
                ["plus", "relation", 2713484], # Barahoti
                ["plus", "relation", 3202329], # South Tibet
            ],
            "IND": [
                ["minus", "relation", 7935380], # Trans-Karakoram Tract
                ["minus", "relation", 2713465], # Aksai Chin
                ["minus", "relation", 2713466], # Demchok sector
            ],
            "PAK": [
                ["plus", "relation", 2713466], # Demchok sector, touches PAK
            ],
        },
    },
    "IND": {
        "base": [
            ["plus", "relation", 304716], # India
        ],
        "perspectives": {
            "CHN": [
                ["minus", "relation", 7935380], # Trans-Karakoram Tract
                ["minus", "relation", 2713466], # Demchok sector
                ["minus", "relation", 2713483], # Gue-Kaurik
                ["minus", "relation", 2713485], # Shipki La
                ["minus", "relation", 2713676], # Nilang-Jadhang
                ["minus", "relation", 2713484], # Barahoti
                ["minus", "relation", 3202329], # South Tibet
            ],
            "IND": [
                ["plus", "relation", 13414393], # Pakistani-Administered Kashmir
                ["plus", "relation", 7935380], # Trans-Karakoram Tract
                ["plus", "relation", 2713465], # Aksai Chin
                ["plus", "relation", 2713466], # Demchok sector
            ],
            "PAK": [
                ["minus", "relation", 5515045], # Ladakh
                ["minus", "relation", 1943188], # Jammu and Kashmir
            ],
        },
    },
    "NPL": {
        "base": [
            ["plus", "relation", 184633], # Nepal
        ],
        "perspectives": {},
    },
    "PAK": {
        "base": [
            ["plus", "relation", 307573], # Pakistan
        ],
        "perspectives": {
            "PAK": [
                ["plus", "relation", 5515045], # Ladakh
                ["plus", "relation", 1943188], # Jammu and Kashmir
                ["minus", "relation", 2713466], # Demchok sector, claimed by CHN
            ],
            "IND": [
                ["minus", "relation", 13414393], # Pakistani-Administered Kashmir
            ],
        },
    },
    "RUS": {
        "base": [
            ["plus", "relation", 60189], # Russia (includes Crimea in OSM)
            ["minus", "relation", 3788824], # Crimea
        ],
        "perspectives": {
            "RUS": [
                ["plus", "relation", 3788824], # Crimea
            ],
            "UKR": [
                ["minus", "relation", 3788824], # Crimea
            ],
        },
    },
    "UKR": {
        "base": [
            ["plus", "relation", 60199], # Ukraine (includes Crimea in OSM)
        ],
        "perspectives": {
            "RUS": [
                ["minus", "relation", 3788824], # Crimea
            ],
        },
    },
}

# CONFIGS = {
#     "IND": {
#         "base": [
#             ["plus", "relation", "fake-IND"],
#         ],
#         "perspectives": {
#             "IND": [
#                 ["plus", "relation", "fake-PAK-Kashmir"],
#             ],
#             "PAK": [
#                 ["minus", "relation", "fake-IND-Kashmir"],
#             ],
#         },
#     },
#     "PAK": {
#         "base": [
#             ["plus", "relation", "fake-PAK"],
#         ],
#         "perspectives": {
#             "PAK": [
#                 ["plus", "relation", "fake-IND-Kashmir"],
#             ],
#             "IND": [
#                 ["minus", "relation", "fake-PAK-Kashmir"],
#             ],
#         },
#     },
#     "RUS": {
#         "base": [
#             ["plus", "relation", "fake-RUS"],
#             ["minus", "relation", "fake-Crimea"],
#         ],
#         "perspectives": {
#             "RUS": [
#                 ["plus", "relation", "fake-Crimea"],
#             ],
#             "UKR": [
#                 ["minus", "relation", "fake-Crimea"],
#             ],
#         },
#     },
#     "UKR": {
#         "base": [
#             ["plus", "relation", "fake-UKR"],
#         ],
#         "perspectives": {
#             "RUS": [
#                 ["minus", "relation", "fake-Crimea"],
#             ],
#         },
#     },
# }

def make_shape(el_type, osm_id):
    local_path = os.path.join("data/sources", el_type, f"{osm_id}.osm.xml.gz")
    if not os.path.exists(local_path):
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with gzip.open(local_path, "wb", compresslevel=9) as file:
            url = f"https://api.openstreetmap.org/api/0.6/{el_type}/{osm_id}/full"
            print("Downloading", url, file=sys.stderr)
            file.write(urllib.request.urlopen(url).read())
    ds = osgeo.ogr.Open(f"/vsigzip/{local_path}")
    lyr = ds.GetLayer("multipolygons")
    geometries = [feat.GetGeometryRef().Clone() for feat in lyr]
    return functools.reduce(lambda g1, g2: g1.Union(g2), geometries)

def combine_shapes(shapes: list[tuple[str, str, int|str]]) -> osgeo.ogr.Geometry:
    assert shapes[0][0] == "plus"
    return functools.reduce(combine_pair, shapes, osgeo.ogr.CreateGeometryFromWkt('POLYGON EMPTY'))

def combine_pair(geom1: osgeo.ogr.Geometry, shape2: tuple[str, str, int|str]) -> osgeo.ogr.Geometry:
    direction2, el_type2, osm_id2 = shape2
    geom2 = make_shape(el_type2, osm_id2)
    if direction2 == "plus" and geom1 is None:
        geom3 = geom2.Clone()
    elif direction2 == "plus" and geom1 is not None:
        geom3 = geom1.Union(geom2)
    elif direction2 == "minus" and geom1 is not None:
        geom3 = geom1.Difference(geom2)
    else:
        raise ValueError((geom1, direction2))
    return geom3

def main():
    with open("country-disputes.csv", "w") as file:
        rows = csv.DictWriter(file, ("iso3", "geometry"))
        rows.writeheader()
        for (iso3a, config) in CONFIGS.items():
            self_geom = combine_shapes(config["base"] + config["perspectives"].get(iso3a, []))
            base_geom = combine_shapes(config["base"])
            diff_geom = self_geom.Difference(base_geom)
            diff_line = diff_geom.Boundary().Difference(self_geom.Boundary())
            print((iso3a,), str(diff_line)[:128], file=sys.stderr)

            dispute_lines = osgeo.ogr.CreateGeometryFromWkt('LINESTRING EMPTY') # diff_line.Clone()
            for (iso3b, shapes) in config["perspectives"].items():
                if iso3b == iso3a:
                    continue
                pov_geom = combine_shapes(config["base"] + shapes)
                pov_line = pov_geom.Boundary().Difference(self_geom.Boundary()).Difference(base_geom.Boundary())
                print((iso3a, iso3b), str(pov_line)[:128], file=sys.stderr)
                dispute_lines = dispute_lines.Union(pov_line)

            row = dict(iso3=iso3a)
            print("Writing", row, file=sys.stderr)
            rows.writerow({**row, "geometry": dispute_lines.ExportToWkt()})

    return

    with open("country-polygons.csv", "w") as file:
        rows = csv.DictWriter(file, ("iso3", "perspective", "geometry"))
        rows.writeheader()
        for (iso3a, config) in CONFIGS.items():
            direction, el_type, osm_id = config["base"][0]
            assert direction == "plus"
            geom1 = make_shape(el_type, osm_id)
            for (direction, el_type, osm_id) in config["base"][1:]:
                if direction == "plus":
                    geom1 = geom1.Union(make_shape(el_type, osm_id))
                elif direction == "minus":
                    geom1 = geom1.Difference(make_shape(el_type, osm_id))

            # "Neutral" point of view = anyone without a defined perspective
            neutral_pov = set(CONFIGS.keys()) - set(config["perspectives"].keys())
            row = dict(iso3=iso3a, perspective=",".join(sorted(neutral_pov)))
            print("Writing", row, file=sys.stderr)
            rows.writerow({**row, "geometry": geom1.ExportToWkt()})

            # Generate perspectives
            for (iso3b, shapes) in config["perspectives"].items():
                geom2 = geom1.Clone()
                for (direction, el_type, osm_id) in shapes:
                    if direction == "plus":
                        geom2 = geom2.Union(make_shape(el_type, osm_id))
                    elif direction == "minus":
                        geom2 = geom2.Difference(make_shape(el_type, osm_id))
                row = dict(iso3=iso3a, perspective=iso3b)
                print("Writing", row, file=sys.stderr)
                rows.writerow({**row, "geometry": geom2.ExportToWkt()})

if __name__ == "__main__":
    exit(main())
