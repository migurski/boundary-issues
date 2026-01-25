#!/usr/bin/env python3
from __future__ import annotations

import csv
import functools
import gzip
import os
import sys
import unittest
import urllib.request

import geopandas
import osgeo.gdal
import osgeo.ogr
import shapely.wkt

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

FAKE_CONFIGS = {
    "IND": {
        "base": [
            ["plus", "relation", "fake-IND"],
        ],
        "perspectives": {
            "IND": [
                ["plus", "relation", "fake-PAK-Kashmir"],
            ],
            "PAK": [
                ["minus", "relation", "fake-IND-Kashmir"],
            ],
        },
    },
    "PAK": {
        "base": [
            ["plus", "relation", "fake-PAK"],
        ],
        "perspectives": {
            "PAK": [
                ["plus", "relation", "fake-IND-Kashmir"],
            ],
            "IND": [
                ["minus", "relation", "fake-PAK-Kashmir"],
            ],
        },
    },
    "RUS": {
        "base": [
            ["plus", "relation", "fake-RUS"],
            ["minus", "relation", "fake-Crimea"],
        ],
        "perspectives": {
            "RUS": [
                ["plus", "relation", "fake-Crimea"],
            ],
            "UKR": [
                ["minus", "relation", "fake-Crimea"],
            ],
        },
    },
    "UKR": {
        "base": [
            ["plus", "relation", "fake-UKR"],
        ],
        "perspectives": {
            "RUS": [
                ["minus", "relation", "fake-Crimea"],
            ],
        },
    },
}

class TestCase (unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        main(FAKE_CONFIGS)

    def test_borders(self):
        with open("country-borders.csv", "r") as file:
            borders, disputes = {}, {}
            file.readline()
            for row in csv.reader(file):
                key = tuple(row[:-2])
                borders[key] = osgeo.ogr.CreateGeometryFromWkt(row[-2])
                disputes[key] = osgeo.ogr.CreateGeometryFromWkt(row[-1])

        # A point along the border of fake Jammu/Kashmir and fake Himanchal Pradesh
        self.assertTrue(borders[("IND", "PAK", "PAK")].Contains(make_point(3, 2)))
        self.assertFalse(borders[("IND", "PAK", "IND")].Contains(make_point(3, 2)))
        self.assertTrue(disputes[("IND", "PAK", "RUS,UKR")].Contains(make_point(3, 2)))

        # A point along the border of fake Azad Kashmir and fake Islamabad
        self.assertTrue(borders[("IND", "PAK", "IND")].Contains(make_point(2, 3)))
        self.assertFalse(borders[("IND", "PAK", "PAK")].Contains(make_point(2, 3)))
        self.assertTrue(disputes[("IND", "PAK", "RUS,UKR")].Contains(make_point(2, 3)))

        # A point along the fake Line Of Control
        self.assertFalse(borders[("IND", "PAK", "IND")].Contains(make_point(2.5, 2.5)))
        self.assertFalse(borders[("IND", "PAK", "PAK")].Contains(make_point(2.5, 2.5)))
        self.assertTrue(disputes[("IND", "PAK", "RUS,UKR")].Contains(make_point(2.5, 2.5)))

        # A point along the border of fake Crimea and fake Russia
        self.assertTrue(borders[("RUS", "UKR", "UKR")].Contains(make_point(-2, 2)))
        self.assertFalse(borders[("RUS", "UKR", "RUS")].Contains(make_point(-2, 2)))
        self.assertTrue(disputes[("RUS", "UKR", "IND,PAK")].Contains(make_point(-2, 2)))

        # A point along the border of fake Crimea and fake Ukraine
        self.assertTrue(borders[("RUS", "UKR", "RUS")].Contains(make_point(-3, 1)))
        self.assertFalse(borders[("RUS", "UKR", "UKR")].Contains(make_point(-3, 1)))
        self.assertTrue(disputes[("RUS", "UKR", "IND,PAK")].Contains(make_point(-3, 1)))

    def test_disputes(self):
        with open("country-disputes.csv", "r") as file:
            file.readline()
            rows = {
                tuple(row[:-1]): osgeo.ogr.CreateGeometryFromWkt(row[-1])
                for row in csv.reader(file)
            }

        # A point along the border of fake Jammu/Kashmir and fake Himanchal Pradesh
        self.assertTrue(rows[("IND",)].Contains(make_point(3, 2)))
        self.assertFalse(rows[("PAK",)].Contains(make_point(3, 2)))

        # A point along the border of fake Azad Kashmir and fake Islamabad
        self.assertTrue(rows[("PAK",)].Contains(make_point(2, 3)))

def make_point(x, y):
    return osgeo.ogr.CreateGeometryFromWkt(f"POINT ({x} {y})")

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

def write_country_borders(configs):
    df = geopandas.read_file("country-polygons.csv")
    print(df, file=sys.stderr)

    geometry = geopandas.GeoSeries.from_wkt(df.geometry)
    gdf = geopandas.GeoDataFrame(data=df, geometry=geometry)
    pov_borders: dict[tuple[int, int], set[int]] = {}
    iso3_neighbors: set[tuple[str, str]] = set()

    for pov in set(gdf.iso3.values):
        print("POV:", pov, file=sys.stderr)
        gdf_pov = gdf[gdf.perspective.str.contains(pov)]
        for i, row in geopandas.sjoin(gdf_pov, gdf_pov, predicate="touches").iterrows():
            i1, i2 = (min(i, row.index_right), max(i, row.index_right))
            iso3a, iso3b = gdf.loc[i1].iso3, gdf.loc[i2].iso3
            iso3_neighbors.add((iso3a, iso3b))
            if (i1, i2) in pov_borders:
                pov_borders[(i1, i2)].add(pov)
            else:
                pov_borders[(i1, i2)] = {pov}
        print(pov_borders, file=sys.stderr)

    with open("country-borders.csv", mode="w") as file:
        rows = csv.DictWriter(file, fieldnames=("iso3a", "iso3b", "perspectives", "geometry"))
        rows.writeheader()

        for (i1, i2), povs in pov_borders.items():
            iso3a, iso3b = gdf.loc[i1].iso3, gdf.loc[i2].iso3
            geom1, geom2 = gdf.loc[i1].geometry, gdf.loc[i2].geometry
            linestring = geom1.intersection(geom2)

            row = dict(iso3a=iso3a, iso3b=iso3b, perspectives=",".join(sorted(povs)))
            print("Writing", row, file=sys.stderr)
            rows.writerow({**row, "geometry": shapely.wkt.dumps(linestring)})

def write_country_disputes(configs):
    with open("country-disputes.csv", "w") as file:
        rows = csv.DictWriter(file, ("iso3", "geometry"))
        rows.writeheader()
        for (iso3a, config) in configs.items():
            self_geom = combine_shapes(config["base"] + config["perspectives"].get(iso3a, []))
            base_geom = combine_shapes(config["base"])

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

def write_country_polygons(configs):
    with open("country-polygons.csv", "w") as file:
        rows = csv.DictWriter(file, ("iso3", "perspective", "geometry"))
        rows.writeheader()
        for (iso3a, config) in configs.items():
            direction, el_type, osm_id = config["base"][0]
            assert direction == "plus"
            geom1 = make_shape(el_type, osm_id)
            for (direction, el_type, osm_id) in config["base"][1:]:
                if direction == "plus":
                    geom1 = geom1.Union(make_shape(el_type, osm_id))
                elif direction == "minus":
                    geom1 = geom1.Difference(make_shape(el_type, osm_id))

            # "Neutral" point of view = anyone without a defined perspective
            neutral_pov = set(configs.keys()) - set(config["perspectives"].keys())
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

def main(configs):
    write_country_disputes(configs)
    write_country_polygons(configs)
    write_country_borders(configs)

if __name__ == "__main__":
    exit(main(CONFIGS))
