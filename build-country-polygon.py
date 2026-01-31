#!/usr/bin/env python3
from __future__ import annotations

import csv
import functools
import glob
import gzip
import os
import sys
import tempfile
import unittest
import urllib.request

import geopandas
import osgeo.gdal
import osgeo.ogr
import shapely.wkt
import yaml

osgeo.gdal.UseExceptions()

BOUNDARIES_NAME = "country-boundaries.csv"
AREAS_NAME = "country-areas.csv"
EMPTY_LINE_WKT = "LINESTRING EMPTY"

class TestCase (unittest.TestCase):

    tempdir: str|None = None

    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.mkdtemp(dir=".", prefix="tests-")
        os.makedirs(cls.tempdir, exist_ok=True)
        with open('test-config.yaml', 'r') as file:
            config = yaml.safe_load(file)
        main(cls.tempdir, config)

    @classmethod
    def tearDownClass(cls):
        return  # Don't remove these so we can inspect them in QGIS
        os.remove(os.path.join(cls.tempdir, BOUNDARIES_NAME))
        os.remove(os.path.join(cls.tempdir, AREAS_NAME))
        os.rmdir(cls.tempdir)
        cls.tempdir = None

    def test_boundaries(self):
        with open(os.path.join(TestCase.tempdir, BOUNDARIES_NAME), "r") as file:
            agreed, disputed = {}, {}
            file.readline()
            for row in csv.reader(file):
                key = tuple(row[:-2])
                agreed[key] = osgeo.ogr.CreateGeometryFromWkt(row[-2])
                disputed[key] = osgeo.ogr.CreateGeometryFromWkt(row[-1])

        # A point along the border of fake Jammu/Kashmir and fake Himanchal Pradesh
        self.assertTrue(agreed[("IND", "PAK", "PAK")].Contains(make_point(2.9, 2)))
        self.assertFalse(agreed[("IND", "PAK", "IND")].Contains(make_point(2.9, 2)))
        self.assertTrue(disputed[("IND", "PAK", "RUS,UKR")].Contains(make_point(2.9, 2)))

        # A point along the border of fake Azad Kashmir and fake Islamabad
        self.assertTrue(agreed[("IND", "PAK", "IND")].Contains(make_point(2, 3)))
        self.assertFalse(agreed[("IND", "PAK", "PAK")].Contains(make_point(2, 3)))
        self.assertTrue(disputed[("IND", "PAK", "RUS,UKR")].Contains(make_point(2, 3)))

        # A point along the fake Line Of Control
        self.assertFalse(agreed[("IND", "PAK", "IND")].Contains(make_point(2.5, 2.5)))
        self.assertFalse(agreed[("IND", "PAK", "PAK")].Contains(make_point(2.5, 2.5)))
        self.assertTrue(disputed[("IND", "PAK", "RUS,UKR")].Contains(make_point(2.5, 2.5)))

        # A point along the border of fake Crimea and fake Russia
        self.assertTrue(agreed[("RUS", "UKR", "UKR")].Contains(make_point(-2, 2)))
        self.assertFalse(agreed[("RUS", "UKR", "RUS")].Contains(make_point(-2, 2)))
        self.assertTrue(disputed[("RUS", "UKR", "CHN,IND,PAK")].Contains(make_point(-2, 2)))

        # A point along the border of fake Crimea and fake Ukraine
        self.assertTrue(agreed[("RUS", "UKR", "RUS")].Contains(make_point(-3, 1)))
        self.assertFalse(agreed[("RUS", "UKR", "UKR")].Contains(make_point(-3, 1)))
        self.assertTrue(disputed[("RUS", "UKR", "CHN,IND,PAK")].Contains(make_point(-3, 1)))

        # A point along the border of fake Jammu/Kashmir and fake Aksai Chin
        self.assertTrue(agreed[("CHN", "IND", "CHN")].Contains(make_point(3, 2.1)))
        self.assertFalse(agreed[("CHN", "IND", "IND")].Contains(make_point(3, 2.1)))
        self.assertTrue(disputed[("CHN", "IND", "RUS,UKR")].Contains(make_point(3, 2.1)))

        # A point along the border of fake India and fake Aksai Chin
        self.assertTrue(agreed[("CHN", "IND", "CHN")].Contains(make_point(3.1, 2)))
        self.assertFalse(agreed[("CHN", "IND", "IND")].Contains(make_point(3.1, 2)))
        self.assertTrue(disputed[("CHN", "IND", "RUS,UKR")].Contains(make_point(3.1, 2)))

        # A point along the border of fake Pakistan and fake China
        self.assertTrue(agreed[("CHN", "PAK", "CHN")].Contains(make_point(3, 3.2)))
        self.assertTrue(agreed[("CHN", "PAK", "PAK")].Contains(make_point(3, 3.2)))
        self.assertTrue(agreed[("CHN", "PAK", "IND")].Contains(make_point(3, 3.2)))
        self.assertTrue(agreed[("CHN", "PAK", "RUS,UKR")].Contains(make_point(3, 3.2)))
        self.assertFalse(disputed[("CHN", "PAK", "CHN")].Contains(make_point(3, 3.2)))
        self.assertFalse(disputed[("CHN", "PAK", "PAK")].Contains(make_point(3, 3.2)))
        self.assertFalse(disputed[("CHN", "PAK", "IND")].Contains(make_point(3, 3.2)))
        self.assertFalse(disputed[("CHN", "PAK", "RUS,UKR")].Contains(make_point(3, 3.2)))

        # A point along the border of fake Pakistan and fake Trans-Karakoram Tract
        self.assertTrue(agreed[("CHN", "PAK", "CHN")].Contains(make_point(3, 3.7)))
        self.assertTrue(agreed[("CHN", "PAK", "PAK")].Contains(make_point(3, 3.7)))
        self.assertTrue(agreed[("IND", "PAK", "IND")].Contains(make_point(3, 3.7)))
        self.assertFalse(agreed[("CHN", "PAK", "IND")].Contains(make_point(3, 3.7)))
        self.assertFalse(agreed[("IND", "PAK", "CHN")].Contains(make_point(3, 3.7)))
        self.assertFalse(agreed[("IND", "PAK", "PAK")].Contains(make_point(3, 3.7)))
        self.assertFalse(disputed[("CHN", "IND", "RUS,UKR")].Contains(make_point(3, 3.7)))
        self.assertFalse(disputed[("CHN", "PAK", "RUS,UKR")].Contains(make_point(3, 3.7)))
        self.assertTrue(disputed[("IND", "PAK", "RUS,UKR")].Contains(make_point(3, 3.7)), "Counterintuitive because RUS/UKR don't see India up here")

    def test_areas(self):
        with open(os.path.join(TestCase.tempdir, AREAS_NAME), "r") as file:
            file.readline()
            areas = {
                tuple(row[:-1]): osgeo.ogr.CreateGeometryFromWkt(row[-1])
                for row in csv.reader(file)
            }

        # A point inside fake Azad Kashmir
        self.assertTrue(areas[("IND", "IND")].Contains(make_point(2.3, 2.7)))
        self.assertFalse(areas[("IND", "PAK")].Contains(make_point(2.3, 2.7)))
        self.assertTrue(areas[("PAK", "CHN,RUS,UKR")].Contains(make_point(2.3, 2.7)))
        self.assertFalse(areas[("IND", "RUS,UKR")].Contains(make_point(2.3, 2.7)))

        # A point inside fake Jammu/Kashmir
        self.assertTrue(areas[("IND", "IND")].Contains(make_point(2.7, 2.3)))
        self.assertFalse(areas[("IND", "PAK")].Contains(make_point(2.7, 2.3)))
        self.assertTrue(areas[("IND", "RUS,UKR")].Contains(make_point(2.7, 2.3)))
        self.assertFalse(areas[("PAK", "CHN,RUS,UKR")].Contains(make_point(2.7, 2.3)))

        # A point inside fake Crimea
        self.assertTrue(areas[("UKR", "CHN,IND,PAK,UKR")].Contains(make_point(-2.5, 1.5)))
        self.assertFalse(areas[("UKR", "RUS")].Contains(make_point(-2.5, 1.5)))
        self.assertTrue(areas[("RUS", "RUS")].Contains(make_point(-2.5, 1.5)))

        # A point inside fake Aksai Chin
        self.assertTrue(areas[("IND", "IND")].Contains(make_point(3.5, 2.5)))
        self.assertFalse(areas[("IND", "CHN")].Contains(make_point(3.5, 2.5)))
        self.assertTrue(areas[("CHN", "CHN,PAK,RUS,UKR")].Contains(make_point(3.5, 2.5)))
        self.assertFalse(areas[("IND", "CHN")].Contains(make_point(3.5, 2.5)))

        # A point inside fake Trans-Karakoram Tract
        self.assertTrue(areas[("IND", "IND")].Contains(make_point(3.7, 3.7)))
        self.assertFalse(areas[("IND", "CHN")].Contains(make_point(3.7, 3.7)))
        self.assertTrue(areas[("CHN", "CHN,PAK,RUS,UKR")].Contains(make_point(3.7, 3.7)))
        self.assertFalse(areas[("IND", "CHN")].Contains(make_point(3.7, 3.7)))

def make_point(x, y):
    return osgeo.ogr.CreateGeometryFromWkt(f"POINT ({x} {y})")

def load_shape(el_type: str, osm_id: int|str) -> osgeo.ogr.Geometry:
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
    geom2 = load_shape(el_type2, osm_id2)
    if direction2 == "plus" and geom1 is None:
        geom3 = geom2.Clone()
    elif direction2 == "plus" and geom1 is not None:
        geom3 = geom1.Union(geom2)
    elif direction2 == "minus" and geom1 is not None:
        geom3 = geom1.Difference(geom2)
    else:
        raise ValueError((geom1, direction2))
    return geom3

def dump_wkt(shape: shapely.geometry.base.BaseGeometry) -> str:
    return shapely.wkt.dumps(shape, rounding_precision=7)

def write_country_boundaries(dirname, configs):
    df = geopandas.read_file(os.path.join(dirname, AREAS_NAME))

    geometry = geopandas.GeoSeries.from_wkt(df.geometry)
    gdf = geopandas.GeoDataFrame(data=df, geometry=geometry)

    # Note each country's own view of itself
    self_views: dict[str, shapely.geometry.base.BaseGeometry] = {
        r.iso3: r.geometry for i, r in gdf.iterrows() if r.iso3 in r.perspective
    }

    # Calculate all neighbor pairings in all possible views (expensive!)
    print("Calculating neighbor pairings...", file=sys.stderr)
    gdf_neighbors = geopandas.sjoin(gdf, gdf, predicate="touches")
    iso3_pairings = {
        (min(row.iso3_left, row.iso3_right), max(row.iso3_left, row.iso3_right))
        for i, row in gdf_neighbors[["iso3_left", "iso3_right"]].iterrows()
    }

    with open(os.path.join(dirname, BOUNDARIES_NAME), "w") as file:
        rows = csv.DictWriter(file, fieldnames=("iso3a", "iso3b", "perspectives", "agreed_geometry", "disputed_geometry"))
        rows.writeheader()
        for iso3a, iso3b in iso3_pairings:
            # Populate single perspectives to gdf polygon row ID pairs
            row_pairings: dict[str, tuple[int, int]] = {}

            for iso3c in configs.keys():
                gdf1 = gdf[(gdf.iso3 == iso3a) & gdf.perspective.str.contains(iso3c)]
                gdf2 = gdf[(gdf.iso3 == iso3b) & gdf.perspective.str.contains(iso3c)]
                assert len(gdf1) == 1
                assert len(gdf2) == 1
                geom1, geom2 = gdf1.iloc[0].geometry, gdf2.iloc[0].geometry
                index1, index2 = int(gdf1.index.values[0]), int(gdf2.index.values[0])
                row_pairings[iso3c] = (index1, index2)

            # Populate gdf polygon row ID pairs to matching sets of perspectives
            other_pairings: dict[tuple[int, int], set[str]] = {}

            party1: tuple[int, int] = row_pairings.pop(iso3a)
            party2: tuple[int, int] = row_pairings.pop(iso3b)
            for other_party, pairing in row_pairings.items():
                if pairing in other_pairings:
                    other_pairings[pairing].add(other_party)
                else:
                    other_pairings[pairing] = {other_party}
            other_parties: dict[tuple[str], tuple[int, int]] = {
                tuple(sorted(parties)): pairing for pairing, parties in other_pairings.items()
            }

            # print(iso3a, party1, iso3b, party2, other_parties, file=sys.stderr)

            # Caculate boundaries for each claimant + those of outside observers
            line1 = gdf.iloc[party1[0]].geometry.intersection(gdf.iloc[party1[1]].geometry)
            line2 = gdf.iloc[party2[0]].geometry.intersection(gdf.iloc[party2[1]].geometry)
            other_lines: dict[tuple[str], shapely.geometry.base.BaseGeometry] = {
                k: gdf.iloc[i1].geometry.intersection(gdf.iloc[i2].geometry)
                for k, (i1, i2) in other_parties.items()
            }
            # print(iso3a, str(line1)[:50], iso3b, str(line2)[:50], {k: str(v)[:50] for k, v in other_lines.items()}, file=sys.stderr)

            # Write unambiguous geometries
            row1 = dict(iso3a=iso3a, iso3b=iso3b, perspectives=iso3a)
            print("Writing first-party border", row1, file=sys.stderr)
            rows.writerow({**row1, "agreed_geometry": dump_wkt(line1), "disputed_geometry": EMPTY_LINE_WKT})

            row2 = dict(iso3a=iso3a, iso3b=iso3b, perspectives=iso3b)
            print("Writing first-party border", row2, file=sys.stderr)
            rows.writerow({**row2, "agreed_geometry": dump_wkt(line2), "disputed_geometry": EMPTY_LINE_WKT})

            # Write alternative disputed geometries
            for other_iso3s, linestring in other_lines.items():
                linestrings = [line1, line2, linestring]
                agreed_linestring = functools.reduce(lambda g1, g2: g1.intersection(g2), linestrings)
                disputed_linestring = functools.reduce(lambda g1, g2: g1.union(g2), linestrings).difference(agreed_linestring)

                # Identify 3rd parties with a potential interest in this border
                interested_iso3s: set[str] = {
                    o for o in other_iso3s
                    if disputed_linestring.intersects(self_views[o]) or agreed_linestring.intersects(self_views[o])
                }
                disinterested_iso3s: set[str] = set(other_iso3s) - interested_iso3s

                # Subtract this border from within interested parties' interiors
                for other_iso3 in interested_iso3s:
                    other_polygon, other_boundary = self_views[other_iso3], self_views[other_iso3].boundary

                    agreed_linestring = agreed_linestring.difference(other_polygon).difference(other_boundary)
                    disputed_linestring = disputed_linestring.difference(other_polygon).difference(other_boundary)
                    agreed_wkt = dump_wkt(agreed_linestring) if agreed_linestring.length else EMPTY_LINE_WKT
                    disputed_wkt = dump_wkt(disputed_linestring) if disputed_linestring.length else EMPTY_LINE_WKT

                    row3 = dict(iso3a=iso3a, iso3b=iso3b, perspectives=other_iso3)
                    print("Writing interested party border", row3, file=sys.stderr)
                    rows.writerow({**row3, "agreed_geometry": agreed_wkt, "disputed_geometry": disputed_wkt})

                # Show this border for all disinterested parties
                if disinterested_iso3s:
                    agreed_wkt = dump_wkt(agreed_linestring) if agreed_linestring.length else EMPTY_LINE_WKT
                    disputed_wkt = dump_wkt(disputed_linestring) if disputed_linestring.length else EMPTY_LINE_WKT

                    row4 = dict(iso3a=iso3a, iso3b=iso3b, perspectives=",".join(sorted(disinterested_iso3s)))
                    print("Writing disinterested parties border", row4, file=sys.stderr)
                    rows.writerow({**row4, "agreed_geometry": agreed_wkt, "disputed_geometry": disputed_wkt})

def write_country_areas(dirname, configs):
    with open(os.path.join(dirname, AREAS_NAME), "w") as file:
        rows = csv.DictWriter(file, ("iso3", "perspective", "geometry"))
        rows.writeheader()
        for (iso3a, config) in configs.items():
            geom1 = combine_shapes(config["base"])

            # "Neutral" point of view = anyone without a defined perspective
            neutral_pov = set(configs.keys()) - set(config.get("perspectives", {}).keys())
            row1 = dict(iso3=iso3a, perspective=",".join(sorted(neutral_pov)))
            print("Writing base polygon", row1, file=sys.stderr)
            rows.writerow({**row1, "geometry": geom1.ExportToWkt()})

            # Generate perspectives
            for (iso3b, shapes) in config.get("perspectives", {}).items():
                geom2 = functools.reduce(combine_pair, shapes, geom1)

                row2 = dict(iso3=iso3a, perspective=iso3b)
                print("Writing perspective polygon", row2, file=sys.stderr)
                rows.writerow({**row2, "geometry": geom2.ExportToWkt()})

def main(dirname, configs):
    write_country_areas(dirname, configs)
    write_country_boundaries(dirname, configs)

if __name__ == "__main__":
    config = {}
    for path in glob.glob('config*.yaml'):
        with open(path, 'r') as file:
            config.update(yaml.safe_load(file))
    exit(main(".", config))
