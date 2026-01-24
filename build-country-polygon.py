#!/usr/bin/env python3
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
    "IND": {
        "base": [
            ["plus", "relation", 304716], # India
        ],
        "perspectives": {
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

def main():
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
