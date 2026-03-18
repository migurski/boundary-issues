#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import dataclasses
import enum
import functools
import glob
import gzip
import itertools
import json
import os
import re
import sys
import tempfile
import time
import typing
import unittest
import urllib.request

import geopandas
import networkx
import osgeo.gdal
import osgeo.ogr
import shapely
import shapely.wkt
import yaml

osgeo.gdal.UseExceptions()
csv.field_size_limit(sys.maxsize)

VALIDATION_POINTS_NAME = "validation-points.csv"
BOUNDARIES_NAME = "country-boundaries.csv"
CLAIMS_NAME = "country-claims.csv"
AREAS_NAME = "country-areas.csv"
EMPTY_LINE_WKT = "LINESTRING EMPTY"
BASE = "base"
DELIM = ";"

class Relationship (enum.Enum):
    NO_OVERLAP = 0
    IDENTICAL = 1
    IS_INSIDE = 2
    ENCLOSES = 3
    CONTENDS = 4

@dataclasses.dataclass
class Claim:
    claimants: list[str]  # Must match r"^\w\w\w:\w\w\w(;\w\w\w)*$"
    geometry: shapely.geometry.base.BaseGeometry | None

    def relationship(self, other:Claim) -> Relationship:
        """ Return description of DE-9IM relationship between geometries """
        pattern = self.geometry.relate(other.geometry)
        if re.match(r"^F.2...2.2$", pattern):
            return Relationship.NO_OVERLAP
        if re.match(r"^2.[F01]...[F01].2$", pattern):
            return Relationship.IDENTICAL
        if re.match(r"^2.[F01]...2.2$", pattern):
            return Relationship.IS_INSIDE
        if re.match(r"^2.2...[F01].2$", pattern):
            return Relationship.ENCLOSES
        if re.match(r"^2.2...2.2$", pattern):
            return Relationship.CONTENDS
        raise ValueError(pattern)

    def coalesced(self) -> Claim:
        """ Return a new Claim with the claimants sorted and grouped """
        assert all(re.match(r"^\w\w\w:\w\w\w(;\w\w\w)*$", c) for c in self.claimants)
        out_claimants = []
        for iso3, sub_claimants in itertools.groupby(sorted(self.claimants), key=lambda c: c[:3]):
            out_perspectives = []
            for sub_claimant in sub_claimants:
                out_perspectives.extend(sub_claimant[4:].split(";"))
            out_claimants.append(f"{iso3}:{';'.join(sorted(out_perspectives))}")
        assert all(re.match(r"^\w\w\w:\w\w\w(;\w\w\w)*$", c) for c in out_claimants)
        return Claim(out_claimants, self.geometry)

class TestCase (unittest.TestCase):

    tempdir: str|None = None
    config: dict|None = None

    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.mkdtemp(dir=".", prefix="tests-")
        os.makedirs(cls.tempdir, exist_ok=True)
        cls.config = load_configs(["test-config1.yaml", "test-config2.yaml"])
        write_country_areas(cls.tempdir, cls.config, check_fresh_osm=False)
        write_country_claims(cls.tempdir, cls.config)
        write_validation_points(cls.tempdir, cls.config)
        write_country_boundaries(cls.tempdir, cls.config)

    @classmethod
    def tearDownClass(cls):
        return  # Don't remove these so we can inspect them in QGIS
        os.remove(os.path.join(cls.tempdir, BOUNDARIES_NAME))
        os.remove(os.path.join(cls.tempdir, AREAS_NAME))
        os.rmdir(cls.tempdir)
        cls.tempdir = None

    def test_merge_country_config(self) -> None:
        base = {
            "base": [["+", "relation", "fake-IND"]],
            "perspectives": {"CHN": [["-", "relation", "fake-Aksai-Chin"]]},
            "interior-points": {"base": [[1.0, 2.0]]},
        }
        addition = {
            "perspectives": {"PAK": [["-", "relation", "fake-IND-Kashmir"]]},
            "exterior-points": {"PAK": [[2.3, 2.7]]},
        }
        merged = merge_country_config(base, addition)
        # base geometry preserved
        self.assertEqual(merged["base"], base["base"])
        # perspectives from both blocks present
        self.assertIn("CHN", merged["perspectives"])
        self.assertIn("PAK", merged["perspectives"])
        # interior-points from base preserved
        self.assertEqual(merged["interior-points"], base["interior-points"])
        # exterior-points from addition added
        self.assertEqual(merged["exterior-points"], addition["exterior-points"])

        # duplicate "base" sub-key in interior-points must raise an error
        base_with_conflict = {
            "interior-points": {"base": [[1.0, 2.0]]},
        }
        addition_with_conflict = {
            "interior-points": {"base": [[9.9, 9.9]]},
        }
        with self.assertRaises(ValueError):
            merge_country_config(base_with_conflict, addition_with_conflict)

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
        self.assertTrue(disputed[("IND", "PAK", "NPL;RUS;UKR")].Contains(make_point(2.9, 2)))

        # A point along the border of fake Azad Kashmir and fake Islamabad
        self.assertTrue(agreed[("IND", "PAK", "IND")].Contains(make_point(2, 3)))
        self.assertFalse(agreed[("IND", "PAK", "PAK")].Contains(make_point(2, 3)))
        self.assertTrue(disputed[("IND", "PAK", "NPL;RUS;UKR")].Contains(make_point(2, 3)))

        # A point along the fake Line Of Control
        self.assertFalse(agreed[("IND", "PAK", "IND")].Contains(make_point(2.5, 2.5)))
        self.assertFalse(agreed[("IND", "PAK", "PAK")].Contains(make_point(2.5, 2.5)))
        self.assertTrue(disputed[("IND", "PAK", "NPL;RUS;UKR")].Contains(make_point(2.5, 2.5)))

        # A point along the border of fake Crimea and fake Russia
        self.assertTrue(agreed[("RUS", "UKR", "UKR")].Contains(make_point(-2, 2)))
        self.assertFalse(agreed[("RUS", "UKR", "RUS")].Contains(make_point(-2, 2)))
        self.assertTrue(disputed[("RUS", "UKR", "CHN;IND;NPL;PAK")].Contains(make_point(-2, 2)))

        # A point along the border of fake Crimea and fake Ukraine
        self.assertTrue(agreed[("RUS", "UKR", "RUS")].Contains(make_point(-3, 1)))
        self.assertFalse(agreed[("RUS", "UKR", "UKR")].Contains(make_point(-3, 1)))
        self.assertTrue(disputed[("RUS", "UKR", "CHN;IND;NPL;PAK")].Contains(make_point(-3, 1)))

        # A point along the border of fake Jammu/Kashmir and fake Aksai Chin
        self.assertTrue(agreed[("CHN", "IND", "CHN")].Contains(make_point(3, 2.1)))
        self.assertFalse(agreed[("CHN", "IND", "IND")].Contains(make_point(3, 2.1)))
        self.assertTrue(disputed[("CHN", "IND", "RUS;UKR")].Contains(make_point(3, 2.1)))

        # A point along the border of fake India and fake Aksai Chin
        self.assertTrue(agreed[("CHN", "IND", "CHN")].Contains(make_point(3.1, 2)))
        self.assertFalse(agreed[("CHN", "IND", "IND")].Contains(make_point(3.1, 2)))
        self.assertTrue(disputed[("CHN", "IND", "RUS;UKR")].Contains(make_point(3.1, 2)))

        # A point along the border of fake Pakistan and fake China
        self.assertTrue(agreed[("CHN", "PAK", "CHN")].Contains(make_point(3, 3.2)))
        self.assertTrue(agreed[("CHN", "PAK", "PAK")].Contains(make_point(3, 3.2)))
        self.assertTrue(agreed[("CHN", "PAK", "IND")].Contains(make_point(3, 3.2)))
        self.assertTrue(agreed[("CHN", "PAK", "NPL;RUS;UKR")].Contains(make_point(3, 3.2)))
        self.assertFalse(disputed[("CHN", "PAK", "CHN")].Contains(make_point(3, 3.2)))
        self.assertFalse(disputed[("CHN", "PAK", "PAK")].Contains(make_point(3, 3.2)))
        self.assertFalse(disputed[("CHN", "PAK", "IND")].Contains(make_point(3, 3.2)))
        self.assertFalse(disputed[("CHN", "PAK", "NPL;RUS;UKR")].Contains(make_point(3, 3.2)))

        # A point along the border of fake Pakistan and fake Trans-Karakoram Tract
        self.assertTrue(agreed[("CHN", "PAK", "CHN")].Contains(make_point(3, 3.7)))
        self.assertTrue(agreed[("CHN", "PAK", "PAK")].Contains(make_point(3, 3.7)))
        self.assertTrue(agreed[("IND", "PAK", "IND")].Contains(make_point(3, 3.7)))
        self.assertFalse(agreed[("CHN", "PAK", "IND")].Contains(make_point(3, 3.7)))
        self.assertFalse(agreed[("IND", "PAK", "CHN")].Contains(make_point(3, 3.7)))
        self.assertFalse(agreed[("IND", "PAK", "PAK")].Contains(make_point(3, 3.7)))
        self.assertFalse(disputed[("CHN", "IND", "RUS;UKR")].Contains(make_point(3, 3.7)))
        self.assertFalse(disputed[("CHN", "PAK", "NPL;RUS;UKR")].Contains(make_point(3, 3.7)))
        self.assertTrue(disputed[("IND", "PAK", "NPL;RUS;UKR")].Contains(make_point(3, 3.7)), "Counterintuitive because RUS/UKR don't see India up here")

    def test_claims(self):
        validate_claims(TestCase.config, os.path.join(TestCase.tempdir, CLAIMS_NAME))

    def test_areas(self):
        validate_areas(TestCase.config, os.path.join(TestCase.tempdir, AREAS_NAME))

def validate_claims(configs, claims_path):
    with open(claims_path, "r") as file:
        file.readline()
        claims = [
            (row[0], osgeo.ogr.CreateGeometryFromWkt(row[1]))
            for row in csv.reader(file)
        ]

    all_povs = set(configs.keys())
    cases = [
        (is_in, test_iso3a, test_iso3b, x, y)
        for test_iso3a, config in configs.items()
        for is_in, grouping in [(True, "interior-points"), (False, "exterior-points")]
        for test_iso3b, points in config.get(grouping, {}).items()
        for x, y in points
    ]

    for is_in, test_iso3a, test_iso3b, x, y in cases:
        if test_iso3b == BASE:
            # "Neutral" point of view = anyone without a defined perspective
            local_povs = set(configs[test_iso3a].get("perspectives", {}).keys())
            neutral_povs = all_povs - local_povs
            matching_claims = [
                (claimants, claim_geom) for claimants, claim_geom in claims
                if re.search(f"{test_iso3a}:(\w\w\w;)*({'|'.join(neutral_povs)})", claimants)
            ]
        else:
            matching_claims = [
                (claimants, claim_geom) for claimants, claim_geom in claims
                if re.search(f"{test_iso3a}:(\w\w\w;)*{test_iso3b}", claimants)
            ]

        if not matching_claims:
            continue

        if is_in:
            assert any(make_point(x, y).Within(claim_geom) for _, claim_geom in matching_claims), \
                f"({x}, {y}) should be inside {test_iso3a} for some of {matching_claims} from the {test_iso3b} perspective"
        else:
            assert all(not make_point(x, y).Within(claim_geom) for _, claim_geom in matching_claims), \
                f"({x}, {y}) should be outside {test_iso3a} for all of {matching_claims} from the {test_iso3b} perspective"

def validate_areas(configs, areas_path):
    with open(areas_path, "r") as file:
        file.readline()
        areas = {
            tuple(row[:-1]): osgeo.ogr.CreateGeometryFromWkt(row[-1])
            for row in csv.reader(file)
        }

    all_povs = set(configs.keys())
    cases = [
        (is_in, test_iso3a, test_iso3b, x, y, area_iso3a, area_iso3bs, area_geom)
        for test_iso3a, config in configs.items()
        for is_in, grouping in [(True, "interior-points"), (False, "exterior-points")]
        for test_iso3b, points in config.get(grouping, {}).items()
        for x, y in points
        for (area_iso3a, area_iso3bs), area_geom in areas.items()
    ]

    for is_in, test_iso3a, test_iso3b, x, y, area_iso3a, area_iso3bs, area_geom in cases:
        if test_iso3a != area_iso3a:
            # Skip test/area mismatches
            continue
        if test_iso3b == BASE:
            # "Neutral" point of view = anyone without a defined perspective
            local_povs = set(configs[test_iso3a].get("perspectives", {}).keys())
            neutral_povs = all_povs - local_povs
            if not any(neutral_pov in area_iso3bs for neutral_pov in neutral_povs):
                # Skip test/perspective mismatches for disinterested parties
                continue
        elif test_iso3b not in area_iso3bs:
            # Skip test/perspective mismatches
            continue

        if is_in:
            assert make_point(x, y).Within(area_geom), \
                f"({x}, {y}) should be inside {test_iso3a} ({area_geom.GetEnvelope()}) from the {test_iso3b} perspective"
        else:
            assert not make_point(x, y).Within(area_geom), \
                f"({x}, {y}) should be outside {test_iso3a} ({area_geom.GetEnvelope()}) from the {test_iso3b} perspective"

def merge_country_config(base: dict[str, typing.Any], addition: dict[str, typing.Any]) -> dict[str, typing.Any]:
    merged = dict(base)
    for key in ("perspectives", "interior-points", "exterior-points"):
        if key in addition:
            duplicates = set(merged.get(key, {}).keys()) & set(addition[key].keys())
            if duplicates:
                raise ValueError(f"Duplicate keys in {key}: {duplicates}")
            merged[key] = {**merged.get(key, {}), **addition[key]}
    if "base" in addition and "base" not in merged:
        merged["base"] = addition["base"]
    return merged

def load_configs(paths: list[str]) -> dict[str, dict[str, typing.Any]]:
    config: dict[str, dict[str, typing.Any]] = {}
    for path in paths:
        with open(path, "r") as file:
            for iso3, entry in yaml.safe_load(file).items():
                if iso3 in config:
                    config[iso3] = merge_country_config(config[iso3], entry)
                else:
                    config[iso3] = entry
    return config

def make_point(x, y):
    return osgeo.ogr.CreateGeometryFromWkt(f"POINT ({x} {y})")

def clean_interection(g1: shapely.geometry.base.BaseGeometry, g2: shapely.geometry.base.BaseGeometry) -> shapely.geometry.base.BaseGeometry:
    return shapely.line_merge(g1.intersection(g2))

def clean_union(g1: shapely.geometry.base.BaseGeometry, g2: shapely.geometry.base.BaseGeometry) -> shapely.geometry.base.BaseGeometry:
    return shapely.line_merge(g1.union(g2))

def load_shape(el_type: str, osm_id: int|str, check_fresh_osm: bool) -> osgeo.ogr.Geometry:
    local_path = os.path.join("data/sources", el_type, f"{osm_id}.osm.xml.gz")
    for delay in (15, 60, None):
        newly_downloaded = False
        try:
            if check_fresh_osm or not os.path.exists(local_path):
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with gzip.open(local_path, "wb", compresslevel=9) as file:
                    url = f"https://api.openstreetmap.org/api/0.6/{el_type}/{osm_id}/full"
                    print("Downloading", url, file=sys.stderr)
                    file.write(urllib.request.urlopen(url).read())
                    newly_downloaded = True
            ds = osgeo.ogr.Open(f"/vsigzip/{local_path}")
            lyr = ds.GetLayer("multipolygons")
            geometries = [feat.GetGeometryRef().Clone() for feat in lyr]
        except Exception:
            if newly_downloaded and delay is not None:
                print("Must retry", url, file=sys.stderr)
                time.sleep(delay)
            else:
                print("Failed to download", url, file=sys.stderr)
                raise
        else:
            return functools.reduce(lambda g1, g2: g1.Union(g2), geometries)

def combine_shapes(shapes: list[tuple[str, str, int|str]], check_fresh_osm: bool) -> osgeo.ogr.Geometry:
    assert shapes[0][0] == "plus"
    return functools.reduce(lambda g, s: combine_pair(g, s, check_fresh_osm), shapes, osgeo.ogr.CreateGeometryFromWkt('POLYGON EMPTY'))

def combine_pair(geom1: osgeo.ogr.Geometry, shape2: tuple[str, str, int|str, str], check_fresh_osm: bool) -> osgeo.ogr.Geometry:
    direction2, el_type2, osm_id2 = shape2
    geom2 = load_shape(el_type2, osm_id2, check_fresh_osm)
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

def write_validation_points(dirname, configs):
    all_povs = set(configs.keys())
    cases = [
        (is_in, test_iso3a, test_iso3b, x, y)
        for test_iso3a, config in configs.items()
        for is_in, grouping in [(True, "interior-points"), (False, "exterior-points")]
        for test_iso3b, points in config.get(grouping, {}).items()
        for x, y in points
    ]

    with open(os.path.join(dirname, VALIDATION_POINTS_NAME), "w") as file:
        rows = csv.DictWriter(file, fieldnames=("iso3", "perspectives", "relation", "geometry"))
        rows.writeheader()
        for is_in, test_iso3a, test_iso3b, x, y in cases:
            if test_iso3b == BASE:
                # "Neutral" point of view = anyone without a defined perspective
                local_povs = set(configs[test_iso3a].get("perspectives", {}).keys())
                neutral_povs = all_povs - local_povs
                test_iso3b = ";".join(neutral_povs)
            relation = "interior" if is_in else "exterior"
            row = dict(iso3=test_iso3a, perspectives=test_iso3b, relation=relation)
            print("Writing validation point", row, file=sys.stderr)
            rows.writerow({**row, "geometry": f"POINT({x:.7f} {y:.7f})"})

def write_country_boundaries(dirname, configs):
    df = geopandas.read_file(os.path.join(dirname, AREAS_NAME))

    geometry = geopandas.GeoSeries.from_wkt(df.geometry)
    gdf = geopandas.GeoDataFrame(data=df, geometry=geometry)

    # Note each country's own view of itself
    self_views: dict[str, shapely.geometry.base.BaseGeometry] = {
        r.iso3: r.geometry for i, r in gdf.iterrows() if r.iso3 in r.perspectives
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
                gdf1 = gdf[(gdf.iso3 == iso3a) & gdf.perspectives.str.contains(iso3c)]
                gdf2 = gdf[(gdf.iso3 == iso3b) & gdf.perspectives.str.contains(iso3c)]
                assert len(gdf1) == 1
                assert len(gdf2) == 1
                _geom1, _geom2 = gdf1.iloc[0].geometry, gdf2.iloc[0].geometry
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
            line1 = clean_interection(gdf.iloc[party1[0]].geometry, gdf.iloc[party1[1]].geometry)
            line2 = clean_interection(gdf.iloc[party2[0]].geometry, gdf.iloc[party2[1]].geometry)
            other_lines: dict[tuple[str], shapely.geometry.base.BaseGeometry] = {
                k: clean_interection(gdf.iloc[i1].geometry, gdf.iloc[i2].geometry)
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
                agreed_linestring = functools.reduce(clean_interection, linestrings)
                disputed_linestring = functools.reduce(clean_union, linestrings).difference(agreed_linestring)

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

                    row4 = dict(iso3a=iso3a, iso3b=iso3b, perspectives=DELIM.join(sorted(disinterested_iso3s)))
                    print("Writing disinterested parties border", row4, file=sys.stderr)
                    rows.writerow({**row4, "agreed_geometry": agreed_wkt, "disputed_geometry": disputed_wkt})

def write_country_claims(dirname, configs) -> str:
    df = geopandas.read_file(os.path.join(dirname, AREAS_NAME))

    geometry = geopandas.GeoSeries.from_wkt(df.geometry)
    gdf = geopandas.GeoDataFrame(data=df, geometry=geometry)

    # Conflicted regions
    dispute_graph = networkx.Graph()
    dispute_graph.add_nodes_from(gdf.iso3)
    gdf_disputants = geopandas.sjoin(gdf, gdf, predicate="overlaps")
    for _, row in gdf_disputants.iterrows():
        dispute_graph.add_edge(row.iso3_left, row.iso3_right)

    all_claims = []

    for iso3s in networkx.connected_components(dispute_graph):
        print("Evaluating claims for", iso3s, 'with', len(dispute_graph.subgraph(iso3s).edges), "conflicts...", file=sys.stderr)
        gdf_sub = gdf[gdf.iso3.str.match(re.compile(f"({'|'.join(iso3s)})"))]
        out_claims = []
        for _, new_row in gdf_sub.iterrows():
            new_claimant = f"{new_row.iso3}:{new_row.perspectives}"
            new_claim = Claim([new_claimant], new_row.geometry)
            add_claims = [new_claim]
            for out_claim in out_claims:
                relationship = new_claim.relationship(out_claim)
                if relationship is Relationship.NO_OVERLAP:
                    # new_claim does not overlap out_claim
                    continue
                elif relationship is Relationship.IDENTICAL:
                    # new_claim is identical to out_claim
                    out_claim.claimants.extend(new_claim.claimants)
                    add_claims.remove(new_claim)
                    # All of new_claim's area has been found and accounted for
                    break
                elif relationship is Relationship.IS_INSIDE:
                    # new_claim is inside out_claim
                    shared_geom = out_claim.geometry.intersection(new_claim.geometry)
                    untouched_geom = out_claim.geometry.difference(new_claim.geometry)
                    out_claim.geometry = untouched_geom
                    new_claim.claimants.extend(out_claim.claimants)
                    new_claim.geometry = shared_geom
                    # All of new_claim's area has been found and accounted for
                    break
                elif relationship is Relationship.ENCLOSES:
                    # new_claim encloses out_claim
                    remaining_geom = new_claim.geometry.difference(out_claim.geometry)
                    out_claim.claimants.extend(new_claim.claimants)
                    new_claim.geometry = remaining_geom
                    # Some of new_claim's area remains to check against other out_claims
                    continue
                elif relationship is Relationship.CONTENDS:
                    # new_claim contends with out_claim
                    shared_geom = out_claim.geometry.intersection(new_claim.geometry)
                    untouched_geom = out_claim.geometry.difference(new_claim.geometry)
                    remaining_geom = new_claim.geometry.difference(out_claim.geometry)
                    add_claims.append(Claim(out_claim.claimants + new_claim.claimants, shared_geom))
                    out_claim.geometry = untouched_geom
                    new_claim.geometry = remaining_geom
                    # Some of new_claim's area remains to check against other out_claims
                    continue
            for add_claim in add_claims:
                out_claims.append(add_claim)

        all_claims.extend(out_claims)

    with open(os.path.join(dirname, CLAIMS_NAME), 'w') as file:
        rows = csv.DictWriter(file, ("claimants", "geometry"))
        rows.writeheader()
        for claim in all_claims:
            row = dict(claimants=" ".join(claim.coalesced().claimants))
            print("Writing claim polygon", row, file=sys.stderr)
            rows.writerow({**row, "geometry": shapely.wkt.dumps(claim.geometry)})

        return file.name

def write_country_areas(dirname, configs, check_fresh_osm: bool) -> str:
    with open(os.path.join(dirname, AREAS_NAME), "w") as file:
        rows = csv.DictWriter(file, ("iso3", "perspectives", "geometry"))
        rows.writeheader()
        for (iso3a, config) in configs.items():
            geom1 = combine_shapes(config[BASE], check_fresh_osm)

            # "Neutral" point of view = anyone without a defined perspective
            neutral_pov = set(configs.keys()) - set(config.get("perspectives", {}).keys())
            row1 = dict(iso3=iso3a, perspectives=DELIM.join(sorted(neutral_pov)))
            print("Writing base polygon", row1, file=sys.stderr)
            rows.writerow({**row1, "geometry": geom1.ExportToWkt()})

            # Generate perspectives
            for (iso3b, shapes) in config.get("perspectives", {}).items():
                geom2 = functools.reduce(lambda g, s: combine_pair(g, s, check_fresh_osm), shapes, geom1)

                row2 = dict(iso3=iso3a, perspectives=iso3b)
                print("Writing perspective polygon", row2, file=sys.stderr)
                rows.writerow({**row2, "geometry": geom2.ExportToWkt()})

        return file.name

def main(dirname, configs, check_fresh_osm: bool):
    areas_path = write_country_areas(dirname, configs, check_fresh_osm)
    claims_path = write_country_claims(dirname, configs)
    print("Validating interior and exterior points...", file=sys.stderr)
    validate_areas(configs, areas_path)
    validate_claims(configs, claims_path)
    write_validation_points(dirname, configs)
    write_country_boundaries(dirname, configs)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Build country polygon data from OSM sources')
    parser.add_argument('--configs', nargs='*', help='Specific config files to process (e.g., config-UKR-RUS.yaml)')
    parser.add_argument('--check-fresh-osm', action='store_true', help='Ignore local files and download fresh OSM data')
    args = parser.parse_args()

    config_paths = args.configs if args.configs else glob.glob('config*.yaml')
    config = load_configs(config_paths)

    exit(main(".", config, args.check_fresh_osm))
