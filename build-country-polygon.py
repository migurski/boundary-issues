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
import os
import re
import sys
import tempfile
import time
import typing
import unittest
import urllib.parse
import urllib.request

import geopandas
import networkx
import osgeo.gdal
import osgeo.ogr
import pandas
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

D0 = "-"  # owner-owner
D1 = ":"  # owners:observers
D2 = ";"  # observer;observer

CLAIMANT = tuple[str, set[str]]

class Relationship (enum.Enum):
    NO_OVERLAP = 0
    IDENTICAL = 1
    IS_INSIDE = 2
    ENCLOSES = 3
    CONTENDS = 4

@dataclasses.dataclass
class Claim:
    claimants: list[CLAIMANT]
    geometry: shapely.geometry.base.BaseGeometry | None

    def relationship(self, other:Claim) -> Relationship:
        """ Return description of DE-9IM relationship between geometries """
        pattern = self.geometry.relate(other.geometry)
        if re.match(r"^F.2...2.2$", pattern):
            return Relationship.NO_OVERLAP
        if re.match(r"^2.F...F.2$", pattern):
            return Relationship.IDENTICAL
        if re.match(r"^2.F...2.2$", pattern):
            return Relationship.IS_INSIDE
        if re.match(r"^2.2...F.2$", pattern):
            return Relationship.ENCLOSES
        if re.match(r"^2.2...2.2$", pattern):
            return Relationship.CONTENDS
        raise ValueError(pattern)

    def coalesced(self) -> Claim:
        """ Return a new Claim with the claimants sorted and grouped """
        out: dict[str, set[str]] = {}
        for iso3, perspectives in self.claimants:
            out.setdefault(iso3, set()).update(perspectives)
        out_claimants: list[CLAIMANT] = sorted(out.items())
        return Claim(out_claimants, self.geometry)

@dataclasses.dataclass
class Boundary:
    claims1: list[CLAIMANT]
    claims2: list[CLAIMANT]
    geometry: shapely.geometry.base.BaseGeometry

class TestCase (unittest.TestCase):

    tempdir: str|None = None
    config: dict|None = None

    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.mkdtemp(dir=".", prefix="tests-")
        os.makedirs(cls.tempdir, exist_ok=True)
        cls.config = load_configs(["test-config1.yaml", "test-config2.yaml"], None)
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

    @staticmethod
    def _load_borders():
        with open(os.path.join(TestCase.tempdir, BOUNDARIES_NAME), "r") as file:
            borders = []
            file.readline()
            for row in csv.reader(file):
                stable_set = set(row[0].split(D2)) if row[0] else set()
                disputed_set = set(row[1].split(D2)) if row[1] else set()
                nonexistent_set = set(row[2].split(D2)) if row[2] else set()
                geom = osgeo.ogr.CreateGeometryFromWkt(row[3])
                borders.append((stable_set, disputed_set, nonexistent_set, geom))
        return borders

    @staticmethod
    def stable_for(country):
        """ Union of all border segments where country is in the stable set """
        borders = TestCase._load_borders()
        geoms = [g for stable_set, _, _, g in borders if country in stable_set]
        return functools.reduce(lambda g1, g2: g1.Union(g2), geoms, osgeo.ogr.CreateGeometryFromWkt(EMPTY_LINE_WKT))

    @staticmethod
    def disputed_for(country):
        """ Union of all border segments where country is in the disputed set """
        borders = TestCase._load_borders()
        geoms = [g for _, disputed_set, _, g in borders if country in disputed_set]
        return functools.reduce(lambda g1, g2: g1.Union(g2), geoms, osgeo.ogr.CreateGeometryFromWkt(EMPTY_LINE_WKT))

    def test_boundaries_ind_chn_pak_npl(self):
        # A point along the border of fake Jammu/Kashmir and fake Himanchal Pradesh
        # PAK sees this border as stable (it's the border of Azad Kashmir as PAK claims it)
        # IND does not see this as stable (IND claims Azad Kashmir)
        # NPL, RUS, UKR see this as disputed
        self.assertTrue(self.stable_for("PAK").Contains(make_point(2.9, 2)))
        self.assertFalse(self.stable_for("IND").Contains(make_point(2.9, 2)))
        self.assertTrue(self.disputed_for("NPL").Contains(make_point(2.9, 2)))

        # A point along the border of fake Azad Kashmir and fake Islamabad
        # IND sees this border as stable (it's the border of J&K as IND claims it)
        # PAK does not see this as stable (PAK claims Azad Kashmir)
        # NPL, RUS, UKR see this as disputed
        self.assertTrue(self.stable_for("IND").Contains(make_point(2, 3)))
        self.assertFalse(self.stable_for("PAK").Contains(make_point(2, 3)))
        self.assertTrue(self.disputed_for("RUS").Contains(make_point(2, 3)))

        # A point along the fake Line Of Control
        # Neither IND nor PAK sees this as stable (it's the contested LoC)
        # NPL, RUS, UKR see this as disputed
        self.assertFalse(self.stable_for("IND").Contains(make_point(2.5, 2.5)))
        self.assertFalse(self.stable_for("PAK").Contains(make_point(2.5, 2.5)))
        self.assertTrue(self.disputed_for("UKR").Contains(make_point(2.5, 2.5)))

        # A point along the border of fake Jammu/Kashmir and fake Aksai Chin
        # CHN sees this as stable (it's the border of Aksai Chin as CHN claims it)
        # IND does not see this as stable (IND claims Aksai Chin)
        # RUS, UKR see this as disputed
        self.assertTrue(self.stable_for("CHN").Contains(make_point(3, 2.1)))
        self.assertFalse(self.stable_for("IND").Contains(make_point(3, 2.1)))
        self.assertTrue(self.disputed_for("RUS").Contains(make_point(3, 2.1)))

        # A point along the border of fake India and fake Aksai Chin
        # CHN sees this as stable (it's the southern border of Aksai Chin as CHN claims it)
        # IND does not see this as stable (IND claims Aksai Chin)
        # RUS, UKR see this as disputed
        self.assertTrue(self.stable_for("CHN").Contains(make_point(3.1, 2)))
        self.assertFalse(self.stable_for("IND").Contains(make_point(3.1, 2)))
        self.assertTrue(self.disputed_for("UKR").Contains(make_point(3.1, 2)))

        # A point along the border of fake Pakistan and fake China (Trans-Karakoram area)
        # All parties agree on CHN-PAK border here: stable for CHN, PAK, IND, NPL, RUS, UKR
        # No one sees it as disputed
        self.assertTrue(self.stable_for("CHN").Contains(make_point(3, 3.2)))
        self.assertTrue(self.stable_for("PAK").Contains(make_point(3, 3.2)))
        self.assertTrue(self.stable_for("IND").Contains(make_point(3, 3.2)))
        self.assertTrue(self.stable_for("NPL").Contains(make_point(3, 3.2)))
        self.assertFalse(self.disputed_for("CHN").Contains(make_point(3, 3.2)))
        self.assertFalse(self.disputed_for("PAK").Contains(make_point(3, 3.2)))
        self.assertFalse(self.disputed_for("IND").Contains(make_point(3, 3.2)))
        self.assertFalse(self.disputed_for("NPL").Contains(make_point(3, 3.2)))

        # A point along the fake Trans-Karakoram Tract border
        # CHN and PAK agree it's their border: stable for CHN, PAK
        # IND sees the IND-PAK border here (where PAK-held territory meets IND-claimed territory): stable for IND
        # RUS/UKR/NPL see this as disputed (counterintuitive: they don't recognize India up here)
        self.assertTrue(self.stable_for("CHN").Contains(make_point(3, 3.7)))
        self.assertTrue(self.stable_for("PAK").Contains(make_point(3, 3.7)))
        self.assertTrue(self.stable_for("IND").Contains(make_point(3, 3.7)))
        self.assertTrue(self.disputed_for("RUS").Contains(make_point(3, 3.7)), "Counterintuitive because RUS/UKR don't see India up here")
        self.assertTrue(self.disputed_for("UKR").Contains(make_point(3, 3.7)), "Counterintuitive because RUS/UKR don't see India up here")
        self.assertTrue(self.disputed_for("NPL").Contains(make_point(3, 3.7)), "Counterintuitive because NPL doesn't see India up here")

    def test_boundaries_ukr_rus(self):
        # A point along the border of fake Crimea and fake Russia
        # UKR sees this as stable (it's the border of Crimea as UKR claims it)
        # RUS does not see this as stable (RUS claims Crimea)
        # CHN, IND, NPL, PAK see this as disputed
        self.assertTrue(self.stable_for("UKR").Contains(make_point(-2, 2)))
        self.assertFalse(self.stable_for("RUS").Contains(make_point(-2, 2)))
        self.assertTrue(self.disputed_for("CHN").Contains(make_point(-2, 2)))

        # A point along the border of fake Crimea and fake Ukraine
        # RUS sees this as stable (it's the border of Crimea as RUS claims it)
        # UKR does not see this as stable (UKR claims Crimea)
        # CHN, IND, NPL, PAK see this as disputed
        self.assertTrue(self.stable_for("RUS").Contains(make_point(-3, 1)))
        self.assertFalse(self.stable_for("UKR").Contains(make_point(-3, 1)))
        self.assertTrue(self.disputed_for("IND").Contains(make_point(-3, 1)))

    def test_boundaries_esp_fra(self):
        # A point along the bottom edge of the condominium polygon, bordering ESP/FRA mainland
        # All observers agree the condominium border is stable — nobody disputes it
        self.assertTrue(self.stable_for("ESP").Contains(make_point(-2.5, 5)))
        self.assertTrue(self.stable_for("FRA").Contains(make_point(-2.5, 5)))
        self.assertTrue(self.stable_for("CHN").Contains(make_point(-2.5, 5)))
        self.assertFalse(self.disputed_for("ESP").Contains(make_point(-2.5, 5)))
        self.assertFalse(self.disputed_for("FRA").Contains(make_point(-2.5, 5)))
        self.assertFalse(self.disputed_for("CHN").Contains(make_point(-2.5, 5)))

    def test_claims_esp_fra(self):
        with open(os.path.join(TestCase.tempdir, CLAIMS_NAME)) as f:
            f.readline()
            claimant_strings = [row[0] for row in csv.reader(f)]
        # The condominium region must appear as a joint-owner claim
        joint_owner_rows = [c for c in claimant_strings if "ESP-FRA:" in c]
        self.assertGreater(len(joint_owner_rows), 0, "Expected ESP-FRA joint-owner token in claims")

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
                if neutral_povs and re.search(rf"[^\s]{test_iso3a}(?:{D0}\w\w\w)*{D1}(\w\w\w{D0})*({'|'.join(neutral_povs)})[\s$]", claimants)
            ]
        else:
            matching_claims = [
                (claimants, claim_geom) for claimants, claim_geom in claims
                if re.search(rf"[^\s]{test_iso3a}(?:{D0}\w\w\w)*{D1}(\w\w\w{D0})*{test_iso3b}[\s$]", claimants)
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

def load_configs(paths: list[str], iso3s: set[str] | None) -> dict[str, dict[str, typing.Any]]:
    config: dict[str, dict[str, typing.Any]] = {}
    for path in paths:
        if not os.path.exists(path):
            continue
        with open(path, "r") as file:
            for iso3, entry in yaml.safe_load(file).items():
                if iso3 in config:
                    config[iso3] = merge_country_config(config[iso3], entry)
                else:
                    config[iso3] = entry

    if iso3s is None:
        return config

    # Remove everything not in the given list of iso3s + BASE
    for iso3a in list(config):
        if iso3a not in {*iso3s, *{BASE}}:
            del config[iso3a]
        else:
            for key in ("perspectives", "interior-points", "exterior-points"):
                if key in config[iso3a]:
                    for iso3b in list(config[iso3a][key]):
                        if iso3b not in {*iso3s, *{BASE}}:
                            del config[iso3a][key][iso3b]

    return config

def make_point(x, y):
    return osgeo.ogr.CreateGeometryFromWkt(f"POINT ({x} {y})")

def clean_interection(g1: shapely.geometry.base.BaseGeometry, g2: shapely.geometry.base.BaseGeometry) -> shapely.geometry.base.BaseGeometry:
    return shapely.line_merge(g1.intersection(g2))

def clean_union(g1: shapely.geometry.base.BaseGeometry, g2: shapely.geometry.base.BaseGeometry) -> shapely.geometry.base.BaseGeometry:
    return shapely.line_merge(g1.union(g2))

def clean_polygon(g: shapely.geometry.base.BaseGeometry) -> shapely.geometry.base.BaseGeometry:
    if g.geom_type.endswith("Polygon"):
        return g
    if g.geom_type == "GeometryCollection":
        polygon_parts = [_g for _g in g.geoms if _g.geom_type.endswith("Polygon")]
        if polygon_parts:
            g = functools.reduce(lambda g1, g2: g1.union(g2), polygon_parts)
    if g.geom_type.endswith("Polygon"):
        return g
    return shapely.wkt.loads('POLYGON EMPTY')

def clean_linestring(g: shapely.geometry.base.BaseGeometry) -> shapely.geometry.base.BaseGeometry:
    if g.geom_type.endswith("LineString"):
        return shapely.line_merge(g)
    if g.geom_type == "GeometryCollection":
        linestring_parts = [_g for _g in g.geoms if _g.geom_type.endswith("LineString")]
        if linestring_parts:
            g = functools.reduce(lambda g1, g2: g1.union(g2), linestring_parts)
    if g.geom_type.endswith("LineString"):
        return shapely.line_merge(g)
    return shapely.wkt.loads('LINESTRING EMPTY')

def load_shape(el_type: str, osm_id: int|str, check_fresh_osm: bool, cache_base_url: str|None = None) -> osgeo.ogr.Geometry:
    local_path = os.path.join("data/sources", el_type, f"{osm_id}.osm.xml.gz")
    if cache_base_url and not check_fresh_osm and not os.path.exists(local_path):
        cache_url = urllib.parse.urljoin(cache_base_url, f"/cache/{el_type}/{osm_id}.osm.xml.gz")
        try:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            print("Fetching from cache", cache_url, file=sys.stderr)
            urllib.request.urlretrieve(cache_url, local_path)
        except Exception:
            pass  # fall through to OSM download
    for delay in (10, 20, None):
        newly_downloaded = False
        try:
            if check_fresh_osm or not os.path.exists(local_path):
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with gzip.open(local_path, "wb", compresslevel=9) as file:
                    url = f"https://api.openstreetmap.org/api/0.6/{el_type}/{osm_id}/full"
                    print("Downloading", url, file=sys.stderr)
                    time.sleep(5)
                    file.write(urllib.request.urlopen(url).read())
                    newly_downloaded = True
            ds = osgeo.ogr.Open(f"/vsigzip/{local_path}")
            lyr = ds.GetLayer("multipolygons")
            geometries = [feat.GetGeometryRef().Clone() for feat in lyr]
        except Exception:
            if (newly_downloaded or check_fresh_osm) and delay is not None:
                print("Must retry", url, file=sys.stderr)
                time.sleep(delay)
            else:
                print("Failed to download", url, file=sys.stderr)
                raise
        else:
            return functools.reduce(lambda g1, g2: g1.Union(g2), geometries)

def combine_shapes(shapes: list[tuple[str, str, int|str]], check_fresh_osm: bool, cache_base_url: str|None = None) -> osgeo.ogr.Geometry:
    assert shapes[0][0] == "plus"
    return functools.reduce(lambda g, s: combine_pair(g, s, check_fresh_osm, cache_base_url), shapes, osgeo.ogr.CreateGeometryFromWkt('POLYGON EMPTY'))

def combine_pair(geom1: osgeo.ogr.Geometry, shape2: tuple[str, str, int|str, str], check_fresh_osm: bool, cache_base_url: str|None = None) -> osgeo.ogr.Geometry:
    direction2, el_type2, osm_id2 = shape2
    geom2 = load_shape(el_type2, osm_id2, check_fresh_osm, cache_base_url)
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
                test_iso3b = D2.join(neutral_povs)
            relation = "interior" if is_in else "exterior"
            row = dict(iso3=test_iso3a, perspectives=test_iso3b, relation=relation)
            print("Writing validation point", row, file=sys.stderr)
            rows.writerow({**row, "geometry": f"POINT({x:.7f} {y:.7f})"})

def write_country_boundaries(dirname, configs):
    df = geopandas.read_file(os.path.join(dirname, CLAIMS_NAME))

    geometry = geopandas.GeoSeries.from_wkt(df.geometry)
    gdf = geopandas.GeoDataFrame(data=df, geometry=geometry)

    gdf_neighbors = geopandas.sjoin(gdf, gdf, predicate="touches")

    index_pairings = {
        (min(index_left, row.index_right), max(index_left, row.index_right))
        for index_left, row in gdf_neighbors.iterrows()
    }

    with open(os.path.join(dirname, BOUNDARIES_NAME), "w") as file:
        rows = csv.DictWriter(file, fieldnames=("stable", "disputed", "nonexistent", "geometry"))
        rows.writeheader()
        for i1, i2 in sorted(index_pairings):
            row1, row2 = gdf.iloc[i1], gdf.iloc[i2]
            if not row1.geometry.relate_pattern(row2.geometry, 'F*2*1*2*2'):
                # No overlap, including touching at a point
                continue
            boundary = Boundary(
                [(a, set(b.split(D2))) for a, b in re.findall(rf"\b(\w\w\w(?:{D0}\w\w\w)*){D1}(\w\w\w(?:{D2}\w\w\w)*)\b", row1.claimants)],
                [(a, set(b.split(D2))) for a, b in re.findall(rf"\b(\w\w\w(?:{D0}\w\w\w)*){D1}(\w\w\w(?:{D2}\w\w\w)*)\b", row2.claimants)],
                clean_linestring(row1.geometry.intersection(row2.geometry)),
            )
            stable_believers, disputed_believers, non_believers = set(), set(), set()
            if len(boundary.claims1) == 1 and len(boundary.claims2) == 1:
                stable_believers = boundary.claims1[0][1] & boundary.claims2[0][1]
            else:
                neighbor_combos = itertools.product(boundary.claims1, boundary.claims2)
                for (iso3a, observers_a), (iso3b, observers_b) in neighbor_combos:
                    common_observers = observers_a & observers_b
                    if iso3a == iso3b:
                        if D0 in iso3a:
                            # Joint-owner condominium: all observers agree on this border
                            stable_believers |= common_observers
                        else:
                            if iso3a in common_observers:
                                common_observers.remove(iso3a)
                            non_believers.add(iso3a)
                            if common_observers:
                                disputed_believers |= common_observers
                    else:
                        if iso3a in common_observers:
                            common_observers.remove(iso3a)
                            stable_believers.add(iso3a)
                        if iso3b in common_observers:
                            common_observers.remove(iso3b)
                            stable_believers.add(iso3b)
                        if common_observers:
                            disputed_believers |= common_observers
            row = dict(stable=D2.join(stable_believers), disputed=D2.join(disputed_believers), nonexistent=D2.join(non_believers))
            print("Writing border", row, file=sys.stderr)
            rows.writerow({**row, "geometry": dump_wkt(boundary.geometry)})

def write_country_claims(dirname, configs) -> str:
    df = geopandas.read_file(os.path.join(dirname, AREAS_NAME))

    geometry = geopandas.GeoSeries.from_wkt(df.geometry)
    gdf = geopandas.GeoDataFrame(data=df, geometry=geometry)

    # Conflicted regions
    dispute_graph = networkx.Graph()
    dispute_graph.add_nodes_from(gdf.iso3)
    # Need to separately include partial overlaps and complete containments
    gdf_disputants1 = geopandas.sjoin(gdf, gdf, predicate="overlaps")
    gdf_disputants2 = geopandas.sjoin(gdf, gdf, predicate="contains")
    gdf_disputants = pandas.concat((gdf_disputants1, gdf_disputants2))
    for _, row in gdf_disputants.iterrows():
        if row.iso3_left != row.iso3_right:
            dispute_graph.add_edge(row.iso3_left, row.iso3_right)

    all_claims = []

    for iso3s in networkx.connected_components(dispute_graph):
        print("Evaluating claims for", iso3s, 'with', len(dispute_graph.subgraph(iso3s).edges), "conflicts...", file=sys.stderr)
        gdf_sub = gdf[gdf.iso3.str.match(re.compile(f"({'|'.join(iso3s)})")) & gdf.perspectives]
        out_claims = []
        for _, new_row in gdf_sub.iterrows():
            new_claimant: CLAIMANT = (new_row.iso3, set(new_row.perspectives.split(D2)))
            new_claim = Claim([new_claimant], new_row.geometry)
            add_claims = [new_claim]
            for out_claim in out_claims:
                new_polygon = clean_polygon(new_claim.geometry)
                out_polygon = clean_polygon(out_claim.geometry)
                if new_polygon.is_empty:
                    # Stop if the new geometry has been completely eliminated
                    break
                elif out_polygon.is_empty:
                    # Move on to another one if the out geometry has been completely eliminated
                    continue
                try:
                    relationship = new_claim.relationship(out_claim)
                except ValueError:
                    with open(os.path.join(dirname, "bad-relationship.csv"), "w") as file:
                        rows = csv.DictWriter(file, ("claimants", "geometry"))
                        rows.writeheader()
                        rows.writerow({"claimants": repr(new_claim.claimants), "geometry": shapely.wkt.dumps(new_polygon)})
                        rows.writerow({"claimants": repr(out_claim.claimants), "geometry": shapely.wkt.dumps(out_polygon)})
                    raise
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
                    shared_geom = clean_polygon(out_polygon.intersection(new_polygon))
                    untouched_geom = out_polygon.difference(new_polygon)
                    out_claim.geometry = untouched_geom
                    new_claim.claimants.extend(out_claim.claimants)
                    new_claim.geometry = shared_geom
                    # All of new_claim's area has been found and accounted for
                    break
                elif relationship is Relationship.ENCLOSES:
                    # new_claim encloses out_claim
                    remaining_geom = new_polygon.difference(out_polygon)
                    out_claim.claimants.extend(new_claim.claimants)
                    new_claim.geometry = remaining_geom
                    # Some of new_claim's area remains to check against other out_claims
                    continue
                elif relationship is Relationship.CONTENDS:
                    # new_claim contends with out_claim
                    shared_geom = clean_polygon(out_polygon.intersection(new_polygon))
                    untouched_geom = out_polygon.difference(new_polygon)
                    remaining_geom = new_polygon.difference(out_polygon)
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
            coalesced = claim.coalesced()
            # Group claimants that share an identical observer set into joint-owner tokens
            groups: dict[frozenset[str], list[str]] = {}
            for iso3, perspectives in coalesced.claimants:
                key = frozenset(perspectives)
                groups.setdefault(key, []).append(iso3)
            tokens = []
            for key, iso3s in sorted(groups.items(), key=lambda kv: kv[1]):
                owner = D0.join(sorted(iso3s))
                observers = D2.join(sorted(key))
                tokens.append(f"{owner}{D1}{observers}")
            row = dict(claimants=" ".join(tokens))
            print("Writing claim polygon", row, file=sys.stderr)
            rows.writerow({**row, "geometry": shapely.wkt.dumps(claim.geometry)})

        return file.name

def write_country_areas(dirname, configs, check_fresh_osm: bool, cache_base_url: str|None = None) -> str:
    with open(os.path.join(dirname, AREAS_NAME), "w") as file:
        rows = csv.DictWriter(file, ("iso3", "perspectives", "geometry"))
        rows.writeheader()
        for (iso3a, config) in configs.items():
            geom1 = combine_shapes(config[BASE], check_fresh_osm, cache_base_url)

            # "Neutral" point of view = anyone without a defined perspective
            neutral_pov = set(configs.keys()) - set(config.get("perspectives", {}).keys())
            row1 = dict(iso3=iso3a, perspectives=D2.join(sorted(neutral_pov)))
            print("Writing base polygon", row1, file=sys.stderr)
            rows.writerow({**row1, "geometry": geom1.ExportToWkt()})

            # Generate perspectives
            for (iso3b, shapes) in config.get("perspectives", {}).items():
                geom2 = functools.reduce(lambda g, s: combine_pair(g, s, check_fresh_osm, cache_base_url), shapes, geom1)

                row2 = dict(iso3=iso3a, perspectives=iso3b)
                print("Writing perspective polygon", row2, file=sys.stderr)
                rows.writerow({**row2, "geometry": geom2.ExportToWkt()})

        return file.name

def main(dirname, configs, check_fresh_osm: bool, cache_base_url: str|None = None):
    areas_path = write_country_areas(dirname, configs, check_fresh_osm, cache_base_url)
    claims_path = write_country_claims(dirname, configs)
    print("Validating interior and exterior points...", file=sys.stderr)
    validate_areas(configs, areas_path)
    validate_claims(configs, claims_path)
    write_validation_points(dirname, configs)
    write_country_boundaries(dirname, configs)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Build country polygon data from OSM sources')
    parser.add_argument('--configs', nargs='*', help='Specific config files to process (e.g., config-UKR-RUS.yaml)')
    parser.add_argument('--iso3s', help='Comma-delimited list of ISO3 codes to filter on (e.g. "PLT,ESP,FRA,ITA")')
    parser.add_argument('--check-fresh-osm', action='store_true', help='Ignore local files and download fresh OSM data')
    parser.add_argument('--cache-base-url', help='Base URL for S3 OSM relation cache (e.g. https://mybucket.s3.us-east-1.amazonaws.com)')
    args = parser.parse_args()

    config_paths = args.configs if args.configs else glob.glob('config*.yaml')
    iso3s = set(args.iso3s.split(",")) if args.iso3s and re.match(r"^\w\w\w(,\w\w\w)+$", args.iso3s) else None
    config = load_configs(config_paths, iso3s)

    exit(main(".", config, args.check_fresh_osm, args.cache_base_url))
