"""Built check and land_today: a failed or missing input never reads as
raw land, the aerial review joins by parcel id, outputs hold no owner data
and are written atomically, fetching is polite (reviews/37 R1, R2, R9,
R10, R11, R14, R22, R27).

Offline: a fake arcgis.request_json serves synthetic parcels and
footprints; every real HTTP call fails the test, and the cache is a temp
dir. Nothing reads screener/output or screener-data.

    python -m unittest discover -s screener/tests -t .
"""

from __future__ import annotations

import ast
import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests
from shapely.geometry import Polygon, box, mapping
from shapely.geometry.polygon import orient

from screener import arcgis, builtcheck as bc, viewer

OWNER = "TEST OWNER ALPHA LLC"
REAL_FETCHER = bc.Fetcher
X0, Y0 = -84.50, 33.60
SIDE = 0.003                                  # about 280 x 330 m, ~23 ac
DAY_MS = 1_700_000_000_000                    # 2023-11-14


def parcel(i: int, hole: bool = False) -> Polygon:
    x = X0 + i * 0.01
    shell = box(x, Y0, x + SIDE, Y0 + SIDE)
    if not hole:
        return shell
    inner = box(x + 0.001, Y0 + 0.001, x + 0.0015, Y0 + 0.0015)
    return Polygon(shell.exterior.coords, [inner.exterior.coords])


def esri(poly: Polygon) -> dict:
    p = orient(poly, sign=-1.0)               # Esri: shells clockwise, holes counter-clockwise
    return {"rings": [list(map(list, p.exterior.coords))] + [list(map(list, r.coords)) for r in p.interiors]}


def make_run(root: Path, n: int = 3, hole_rank: int | None = None) -> Path:
    run = root / "run-x"
    run.mkdir(parents=True)
    with open(run / "ranked.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, ["rank", "parcel_id", "site_address", "jurisdiction", "acres",
                                "lead_grade", "decision", "owner", "owner_mailing"])
        w.writeheader()
        for i in range(n):
            w.writerow({"rank": i + 1, "parcel_id": f"PID  {i + 1:03d}", "site_address": f"{i} TEST RD",
                        "jurisdiction": "Fairburn", "acres": "23.0", "lead_grade": "B",
                        "decision": "", "owner": OWNER if i == 0 else f"HOLDER NUMBER {i} INC",
                        "owner_mailing": f"{900 + i} PRIVATE MAIL LN, ELSEWHERE GA 3000{i}"})
    feats = [{"type": "Feature", "properties": {"parcel_id": f"PID  {i + 1:03d}"},
              "geometry": mapping(parcel(i, hole=(hole_rank == i + 1)))} for i in range(n)]
    (run / "ranked.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}),
                                        encoding="utf-8")
    (run / "leads.md").write_text("| # | Score |\n|---|---|\n| 1 | 80.0 | x |\n", encoding="utf-8")
    return run


class FakeArcGIS:
    """Routes request_json to synthetic USA Structures and county answers."""

    def __init__(self):
        self.footprints: dict[int, tuple[Polygon, dict]] = {}
        self.records: list[tuple[Polygon | None, dict]] = []
        self.fail: dict[str, Exception] = {}       # "ids" | "features" | "records" -> error
        self.answer: dict[str, dict] = {}          # override a whole answer
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, params, *, check=None, max_age_days=None, **kw):
        kind = ("records" if url.startswith(bc.PARCELS_URL) else
                "ids" if params.get("returnIdsOnly") else "features")
        self.calls.append((kind, params))
        if kind in self.fail:
            raise self.fail[kind]
        data = self.answer[kind] if kind in self.answer else self.route(kind, params)
        problem = check(data) if check else None
        if problem:
            raise arcgis.BadAnswer(problem)
        return data

    def route(self, kind, params):
        if kind == "ids":
            env = json.loads(params["geometry"])
            b = box(env["xmin"], env["ymin"], env["xmax"], env["ymax"])
            return {"objectIds": [i for i, (g, _) in self.footprints.items() if g.intersects(b)]}
        if kind == "features":
            ids = [int(x) for x in params["objectIds"].split(",")]
            return {"features": [{"attributes": {"OBJECTID": i, **self.footprints[i][1]},
                                  "geometry": esri(self.footprints[i][0])} for i in ids],
                    "spatialReference": {"wkid": 4326}}
        # The county answers in WGS84 and always holds the parcel's own
        # record (a test may give its own one in self.records).
        q = Polygon(json.loads(params["geometry"])["rings"][0])
        given = {a.get("ParcelID") for _, a in self.records}
        own = [(parcel(i), {"ParcelID": f"PID  {i + 1:03d}"}) for i in range(10)
               if f"PID  {i + 1:03d}" not in given and parcel(i).intersects(q)]
        return {"features": [{"attributes": a, "geometry": esri(g) if g is not None else None}
                             for g, a in own + self.records],
                "spatialReference": {"wkid": 4326, "latestWkid": 4326}}


class Offline(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.patch(arcgis, "CACHE_DIR", self.tmp / "http")
        boom = mock.Mock(side_effect=AssertionError("network call in an offline test"))
        self.patch(requests.Session, "request", boom)
        self.patch(arcgis, "_session", boom)
        self.fake = FakeArcGIS()
        self.patch(arcgis, "request_json", self.fake)
        self.run_dir = make_run(self.tmp)
        self.out = self.tmp / "out"

    def patch(self, obj, name, value):
        p = mock.patch.object(obj, name, value)
        p.start()
        self.addCleanup(p.stop)

    def fetcher(self):
        return REAL_FETCHER(sleep=lambda s: None)

    def check(self, **kw) -> bc.Result:
        return bc.run_check(self.run_dir, fetcher=self.fetcher(), **kw)

    def visual(self, entries: dict, second: dict | None = None, run: bool = True) -> dict:
        body = {"visual": entries}
        if second:
            body["second_look"] = second
        if run:
            body["run"] = viewer.run_identity(self.run_dir)
        return body

    @staticmethod
    def entry(rank: int, usable="yes", state="vacant_wooded", confidence="high", **kw) -> dict:
        return {"rank": rank, "state": state, "usable_as_raw_land": usable,
                "building_share_pct": 0, "paved_share_pct": 0,
                "evidence": "pine woods, no structures", "confidence": confidence, **kw}

    def footprint(self, oid: int, poly: Polygon, sqfeet=12_000, **attrs):
        self.fake.footprints[oid] = (poly, {"OCC_CLS": "Industrial", "PRIM_OCC": "x",
                                            "SQFEET": sqfeet, "IMAGE_DATE": DAY_MS, **attrs})


class BuiltCheckTest(Offline):
    def test_clean_parcel_with_a_photo_review_is_raw_land(self):
        res = self.check(visual=self.visual({"1": self.entry(1)}))
        x, land = res.built[0], res.land[0]
        self.assertEqual(x["built_check"], bc.NONE_FOUND)
        self.assertEqual((x["footprint_state"], x["records_state"]), (bc.CHECKED, bc.CHECKED))
        self.assertEqual(land["land_today"], "raw_land")
        self.assertEqual(land["source"], bc.SOURCE_AGREES)
        self.assertTrue(land["listed_lead"])
        self.assertEqual(res.meta["sources"]["structure_fields"], bc.STRUCTURE_FIELDS)
        # what was actually asked: no owner or mailing field, no '*'
        for _, params in self.fake.calls:
            self.assertNotRegex(json.dumps(params.get("outFields", "")), r"(?i)owner|mail|\*")
        self.assertEqual({k for k, _ in self.fake.calls}, {"ids", "records"})

    def test_without_a_photo_review_nothing_is_raw_land(self):
        res = self.check()
        self.assertEqual({x["land_today"] for x in res.land}, {"unclear"})
        self.assertEqual({x["source"] for x in res.land}, {bc.SOURCE_NONE})

    def test_footprint_query_failure_is_not_checked_never_raw_land(self):
        for kind in ("ids", "features"):
            with self.subTest(kind):
                self.fake.fail = {kind: arcgis.ArcGISError("down")}
                if kind == "features":
                    self.footprint(1, box(X0 + 0.0001, Y0 + 0.0001, X0 + 0.0002, Y0 + 0.0002), sqfeet=50)
                res = self.check(visual=self.visual({"1": self.entry(1)}))
                self.assertEqual(res.built[0]["footprint_state"], bc.FAILED)
                self.assertEqual(res.built[0]["built_check"], bc.NOT_CHECKED)
                self.assertEqual(res.land[0]["land_today"], "unclear")
                self.fake.footprints.clear()

    def test_truncated_or_malformed_answers_are_not_checked(self):
        for kind, answer in (("records", {"features": [], "exceededTransferLimit": True}),
                             ("records", {}), ("ids", {"objectIds": [1], "exceededTransferLimit": True}),
                             ("ids", {})):
            with self.subTest(kind=kind, answer=answer):
                self.fake.answer = {kind: answer}
                res = self.check(visual=self.visual({"1": self.entry(1)}))
                self.assertEqual(res.built[0]["built_check"], bc.NOT_CHECKED)
                self.assertNotEqual(res.land[0]["land_today"], "raw_land")

    def test_footprints_not_in_wgs84_are_not_checked(self):
        self.footprint(1, box(X0 + 0.0001, Y0 + 0.0001, X0 + 0.0002, Y0 + 0.0002))
        self.fake.answer = {"features": {"features": [{"attributes": {}, "geometry": None}],
                                         "spatialReference": {"wkid": 102100}}}
        res = self.check(visual=self.visual({"1": self.entry(1)}))
        self.assertEqual(res.built[0]["footprint_state"], bc.FAILED)
        self.assertEqual(res.land[0]["land_today"], "unclear")

    def test_records_failure_is_not_checked(self):
        self.fake.fail = {"records": arcgis.ArcGISError("county down")}
        res = self.check(visual=self.visual({"1": self.entry(1)}))
        x = res.built[0]
        self.assertEqual((x["records_state"], x["built_check"]), (bc.FAILED, bc.NOT_CHECKED))
        self.assertIsNone(x["records_inside"])
        self.assertIn("county records query failed", x["why"])
        self.assertEqual(res.land[0]["land_today"], "unclear")

    def test_a_record_without_a_shape_is_not_checked(self):
        self.fake.records = [(None, {"ParcelID": "OTHER 1", "ImprAppr": 0})]
        res = self.check()
        self.assertEqual(res.built[0]["records_state"], bc.DEGRADED)
        self.assertEqual(res.built[0]["built_check"], bc.NOT_CHECKED)

    def test_hole_with_an_improvement_record_is_built_not_raw_land(self):
        self.run_dir = make_run(self.tmp / "h", hole_rank=1)
        hole = box(X0 + 0.001, Y0 + 0.001, X0 + 0.0015, Y0 + 0.0015)
        self.fake.records = [(parcel(0, hole=True), {"ParcelID": "PID  001", "ImprAppr": 0}),
                             (hole, {"ParcelID": "IMPR 9", "Address": "1 HOLE ST", "ClassCode": "I4",
                                     "LUCode": "400", "ImprAppr": 4_000_000, "LandAppr": 10})]
        self.footprint(7, box(X0 + 0.0011, Y0 + 0.0011, X0 + 0.0014, Y0 + 0.0014))
        res = self.check(visual=self.visual({"1": self.entry(1, usable="yes", state="vacant_wooded")}))
        x = res.built[0]
        self.assertGreater(x["hole_acres"], 0)
        self.assertGreaterEqual(x["footprint_sf_in_holes"], bc.HOLE_BUILT_SF)
        self.assertEqual(x["built_check"], bc.BUILT)
        self.assertIn("IMPR 9 $4,000,000", x["why"])
        self.assertEqual(x["records_inside"][0]["parcel_id"], "IMPR 9")
        land = res.land[0]
        self.assertEqual(land["land_today"], "unclear")       # photo says raw, footprints say built
        self.assertEqual(land["source"], bc.SOURCE_PENDING)

    def test_missing_improvement_value_is_not_zero(self):
        inside = box(X0 + 0.0005, Y0 + 0.0005, X0 + 0.0008, Y0 + 0.0008)
        self.fake.records = [(parcel(0), {"ParcelID": "PID  001"}),
                             (inside, {"ParcelID": "OTHER 2", "Address": "2 X ST"})]
        res = self.check()
        x = res.built[0]
        self.assertIsNone(x["improvement_value_roll"])
        self.assertIsNone(x["records_inside"][0]["improvements"])
        self.assertEqual(x["records_state"], bc.DEGRADED)
        self.assertEqual(x["built_check"], bc.CHECK)          # a record inside: look again
        files = bc.artifacts(res, viewer.run_identity(self.run_dir))
        row = next(csv.DictReader(io.StringIO(files["builtcheck.csv"])))
        self.assertEqual(row["improvement_value_roll"], "")

    def test_missing_structure_fields_degrade_without_crash(self):
        self.fake.footprints[3] = (box(X0 + 0.0001, Y0 + 0.0001, X0 + 0.0006, Y0 + 0.0006), {})
        res = self.check()
        x = res.built[0]
        self.assertEqual(x["footprint_state"], bc.DEGRADED)
        self.assertIsNone(x["largest_structure_sf"])
        self.assertEqual(x["occupancy"], ["?"])
        self.assertEqual(x["built_check"], bc.BUILT)          # area comes from the shape

    def test_footprint_numbers(self):
        fp = box(X0 + 0.0001, Y0 + 0.0001, X0 + 0.0004, Y0 + 0.0004)
        self.footprint(1, fp, sqfeet=330_000)
        self.footprint(2, box(X0 + SIDE - 0.000001, Y0, X0 + SIDE + 0.001, Y0 + 0.001))  # sliver on the line
        res = self.check()
        x = res.built[0]
        self.assertEqual(x["footprints"], 1)
        self.assertEqual(x["largest_structure_sf"], 330_000)   # the whole structure
        self.assertLess(x["largest_sf_in_outline"], 330_000)   # what is inside the outline
        self.assertAlmostEqual(x["largest_sf_in_outline"], x["footprint_sf_in_outline"], delta=1)
        self.assertEqual(x["imagery_dates"], ["2023-11-14", "2023-11-14"])

    def test_top_n_only(self):
        res = self.check(top=2)
        self.assertEqual([x["rank"] for x in res.built], [1, 2])
        self.assertEqual([x["rank"] for x in res.land], [1, 2])
        self.assertEqual(sum(1 for k, _ in self.fake.calls if k == "records"), 2)


class VerdictTest(unittest.TestCase):
    def x(self, outline=0, holes=0, records=(), fstate=bc.CHECKED, rstate=bc.CHECKED):
        return {"footprint_sf_in_outline": outline, "footprint_sf_in_holes": holes,
                "records_inside": list(records), "footprint_state": fstate, "records_state": rstate}

    def test_boundaries(self):
        v = lambda **kw: bc.verdict(self.x(**kw))[0]
        self.assertEqual(v(outline=bc.OUTLINE_BUILT_SF - 1), bc.CHECK)
        self.assertEqual(v(outline=bc.OUTLINE_BUILT_SF), bc.BUILT)
        self.assertEqual(v(outline=bc.OUTLINE_CHECK_SF - 1), bc.NONE_FOUND)
        self.assertEqual(v(outline=bc.OUTLINE_CHECK_SF), bc.CHECK)
        self.assertEqual(v(holes=bc.HOLE_BUILT_SF - 1), bc.NONE_FOUND)
        self.assertEqual(v(holes=bc.HOLE_BUILT_SF), bc.BUILT)
        rec = lambda usd: [{"parcel_id": "R", "improvements": usd}]
        self.assertEqual(v(records=rec(bc.IMPROVEMENTS_BUILT_USD - 1)), bc.CHECK)
        self.assertEqual(v(records=rec(bc.IMPROVEMENTS_BUILT_USD)), bc.BUILT)
        self.assertEqual(v(records=rec(None)), bc.CHECK)
        self.assertEqual(v(records=rec("n/a")), bc.CHECK)

    def test_not_checked_wins_over_no_building_but_not_over_a_finding(self):
        v = lambda **kw: bc.verdict(self.x(**kw))[0]
        self.assertEqual(v(fstate=bc.FAILED), bc.NOT_CHECKED)
        self.assertEqual(v(rstate=bc.FAILED), bc.NOT_CHECKED)
        self.assertEqual(v(rstate=bc.DEGRADED), bc.NOT_CHECKED)
        self.assertEqual(v(fstate=bc.DEGRADED), bc.NONE_FOUND)   # only SQFEET/OCC_CLS missing
        self.assertEqual(v(outline=20_000, rstate=bc.FAILED), bc.BUILT)
        self.assertEqual(v(outline=3_000, fstate=bc.FAILED), bc.CHECK)

    def test_none_found_names_the_imagery(self):
        self.assertIn("2019-2023", bc.verdict(self.x(), "2019-2023")[1][0])


class LandTodayTest(unittest.TestCase):
    def v(self, usable="yes", state="vacant_wooded", confidence="high"):
        return {"state": state, "usable": usable, "evidence": "e", "confidence": confidence}

    def test_agreement_maps_the_photo_verdict(self):
        for usable, land in bc.USABLE.items():
            with self.subTest(usable):
                c = bc.classify_land(bc.NONE_FOUND, self.v(usable=usable), None)
                self.assertEqual((c["land_today"], c["source"]), (land, bc.SOURCE_AGREES))

    def test_disagreement_needs_a_second_look(self):
        cases = [(bc.BUILT, "vacant_open", "yes"), (bc.BUILT, "partly_built", "partly"),
                 (bc.NONE_FOUND, "paved_yard_or_storage", "no"), (bc.NONE_FOUND, "houses", "partly"),
                 (bc.CHECK, "unclear", "unclear")]
        for fc, state, usable in cases:
            with self.subTest(fc=fc, state=state):
                c = bc.classify_land(fc, self.v(usable=usable, state=state), None)
                self.assertEqual((c["land_today"], c["source"]), ("unclear", bc.SOURCE_PENDING))
        c = bc.classify_land(bc.CHECK, self.v(confidence="low"), None)
        self.assertEqual(c["land_today"], "unclear")
        # built footprints and a 'yes' on a used state: still never raw land
        c = bc.classify_land(bc.BUILT, self.v(usable="yes", state="houses"), None)
        self.assertEqual(c["land_today"], "unclear")

    def test_agreeing_cases_of_the_2026_11_16_table(self):
        for fc, state, usable, land in [(bc.BUILT, "operating_building", "no", "not_raw_land"),
                                        (bc.BUILT, "houses", "partly", "partly_usable"),
                                        (bc.CHECK, "vacant_wooded", "yes", "raw_land"),
                                        (bc.CHECK, "paved_yard_or_storage", "no", "not_raw_land"),
                                        (bc.NONE_FOUND, "partly_built", "partly", "partly_usable")]:
            with self.subTest(fc=fc, state=state):
                self.assertEqual(bc.classify_land(fc, self.v(usable, state), None)["land_today"], land)

    def test_second_look_decides(self):
        second = {"land_today": "raw_land", "what_is_there": "w", "why": "footprints are old",
                  "confidence": "medium"}
        c = bc.classify_land(bc.BUILT, self.v(state="vacant_open"), second)
        self.assertEqual((c["land_today"], c["source"]), ("raw_land", bc.SOURCE_SECOND))

    def test_not_checked_keeps_negatives_drops_raw_land(self):
        c = bc.classify_land(bc.NOT_CHECKED, self.v(usable="yes"), None)
        self.assertEqual(c["land_today"], "unclear")
        c = bc.classify_land(bc.NOT_CHECKED, self.v(usable="no", state="other_use"), None)
        self.assertEqual(c["land_today"], "not_raw_land")
        second = {"land_today": "raw_land", "what_is_there": "w", "why": "", "confidence": "high"}
        self.assertEqual(bc.classify_land(bc.NOT_CHECKED, self.v(), second)["land_today"], "unclear")

    def test_incomplete_check_never_reads_better_than_a_completed_one(self):
        """reviews/37: a first look of 'partly' that a completed check
        sends to a second look read partly_usable when a fetch failed."""
        cases = [(self.v(usable="partly", state="houses", confidence="medium"), bc.NONE_FOUND),
                 (self.v(usable="partly", state="partly_built", confidence="low"), bc.BUILT),
                 (self.v(usable="partly", state="vacant_wooded"), bc.NONE_FOUND)]
        for v, fc in cases:
            done = bc.classify_land(fc, v, None)["land_today"]
            for c in (bc.classify_land(bc.NOT_CHECKED, v, None), bc.classify_land(fc, v, None, complete=False)):
                with self.subTest(state=v["state"], check=fc):
                    self.assertEqual(c["land_today"], "unclear")
                    self.assertIn("not confirmed", c["why"])
                    self.assertEqual(c["source"], bc.SOURCE_PENDING)
            self.assertIn(done, ("unclear", "partly_usable"))
        # a negative photo finding is kept; a second look's partly_usable too
        self.assertEqual(bc.classify_land(bc.NOT_CHECKED, self.v(usable="no", state="other_use"), None,
                                          complete=False)["land_today"], "not_raw_land")
        second = {"land_today": "partly_usable", "what_is_there": "w", "why": "", "confidence": "high"}
        self.assertEqual(bc.classify_land(bc.NOT_CHECKED, self.v(usable="partly"), second)["land_today"], "partly_usable")

    def test_no_review(self):
        for fc in (bc.NONE_FOUND, bc.BUILT, bc.CHECK, bc.NOT_CHECKED):
            self.assertEqual(bc.classify_land(fc, None, None)["land_today"], "unclear")


class VisualFileTest(Offline):
    def load(self, body, by_rank=False):
        rows = viewer.read_ranked(self.run_dir)
        return bc.load_visual(body, rows, viewer.run_identity(self.run_dir), by_rank)

    def test_refusals(self):
        good = self.entry(1)
        cases = {
            "unknown state": {"1": {**good, "state": "castle"}},
            "unknown usable": {"1": {**good, "usable_as_raw_land": "maybe"}},
            "unknown confidence": {"1": {**good, "confidence": "certain"}},
            "unknown key": {"1": {**good, "owner": "x"}},
            "share out of range": {"1": {**good, "paved_share_pct": 140}},
            "rank not in run": {"99": {**good, "rank": 99}},
            "key and rank differ": {"2": good},
            "other parcel": {"1": {**good, "parcel_id": "PID  002"}},
        }
        for name, entries in cases.items():
            with self.subTest(name), self.assertRaises(viewer.Refused):
                self.load(self.visual(entries))
        with self.assertRaises(viewer.Refused):
            self.load({"visual": {"1": good}, "run": {"ranked_csv_sha256": "0" * 64}})
        with self.assertRaises(viewer.Refused):
            self.load({"visual": {"1": good}, "extra": 1})
        with self.assertRaises(viewer.Refused):
            self.load(self.visual({"1": good}, second={"1": {"rank": 1, "land_today": "castle",
                                                             "confidence": "high"}}))

    def test_rank_only_files_need_the_flag(self):
        body = self.visual({"1": self.entry(1)}, run=False)
        with self.assertRaises(viewer.Refused):
            self.load(body)
        first, _ = self.load(body, by_rank=True)
        self.assertIn("PID  001", first)                       # joined to the parcel id
        with_pid = self.visual({"1": self.entry(1, parcel_id="PID  001")}, run=False)
        self.assertIn("PID  001", self.load(with_pid)[0])

    def test_evidence_is_capped(self):
        first, _ = self.load(self.visual({"1": self.entry(1, evidence="x" * 10_000)}))
        self.assertLessEqual(len(first["PID  001"]["evidence"]), bc.EVIDENCE_CAP)


class OutputTest(Offline):
    def test_owner_in_evidence_refused_and_nothing_written(self):
        body = self.visual({"1": self.entry(1, evidence="sign reads test Owner  ALPHA, l.l.c.")})
        res = self.check(visual=body)
        with self.assertRaises(viewer.OwnerDataFound) as cm:
            bc.write_outputs(res, self.run_dir, self.out)
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))
        self.assertNotIn("alpha", str(cm.exception).lower())

    def test_record_address_that_reads_as_a_mailing_is_withheld(self):
        inside = box(X0 + 0.0005, Y0 + 0.0005, X0 + 0.0008, Y0 + 0.0008)
        self.fake.records = [(parcel(0), {"ParcelID": "PID  001", "ImprAppr": 0}),
                             (inside, {"ParcelID": "HOUSE 1", "Address": "901 Private Mail Ln",
                                       "ImprAppr": 90_000}),
                             (box(X0 + 0.0010, Y0 + 0.0010, X0 + 0.0012, Y0 + 0.0012),
                              {"ParcelID": "SHED 2", "Address": "5 PUBLIC RD", "ImprAppr": 1})]
        paths = bc.write_outputs(self.check(), self.run_dir, self.out)
        built = json.loads((self.out / "builtcheck.json").read_text(encoding="utf-8"))
        recs = {r["parcel_id"]: r for r in built["parcels"][0]["records_inside"]}
        self.assertEqual(recs["HOUSE 1"]["address"], bc.ADDRESS_WITHHELD)
        self.assertEqual(recs["SHED 2"]["address"], "5 PUBLIC RD")
        for p in paths:
            self.assertNotIn("private mail", p.read_text(encoding="utf-8").lower())

    def test_cli_refuses_with_no_owner_text_and_exit_2(self):
        vis = self.tmp / "visual.json"
        vis.write_text(json.dumps(self.visual({"1": self.entry(
            1, evidence="mail goes to 900 Private Mail Ln, Elsewhere GA 30000")})), encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), mock.patch.object(bc, "Fetcher", lambda **kw: self.fetcher()):
            code = bc.main([str(self.run_dir), "--out", str(self.out), "--visual", str(vis)])
        self.assertEqual(code, 2)
        self.assertFalse(self.out.exists())
        self.assertNotIn("PRIVATE", err.getvalue().upper())

    def test_clean_outputs_hold_only_allowed_fields(self):
        res = self.check(visual=self.visual({"1": self.entry(1)}))
        paths = bc.write_outputs(res, self.run_dir, self.out)
        self.assertEqual(sorted(p.name for p in paths),
                         ["builtcheck.csv", "builtcheck.json", "land_today.csv", "land_today.json"])
        land = json.loads((self.out / "land_today.json").read_text())
        self.assertEqual(land["run"], viewer.run_identity(self.run_dir))
        self.assertEqual(set(land["parcels"][0]), set(bc.LAND_COLUMNS))
        built = json.loads((self.out / "builtcheck.json").read_text())
        self.assertEqual(set(built["parcels"][0]), set(bc.BUILT_COLUMNS + bc.BUILT_JSON_EXTRA))
        for p in paths:
            text = p.read_text().lower()
            self.assertNotIn("owner alpha", text)
            self.assertNotIn("private mail", text)
        with open(self.out / "builtcheck.csv") as fh:
            self.assertEqual(next(csv.reader(fh)), bc.BUILT_COLUMNS)
        # a re-run replaces the four files and adds none
        bc.write_outputs(self.check(), self.run_dir, self.out)
        self.assertEqual(len(list(self.out.iterdir())), 4)

    def test_csv_formula_cells_are_escaped(self):
        res = self.check(visual=self.visual({"1": self.entry(1, evidence='=HYPERLINK("http://x")')}))
        bc.write_outputs(res, self.run_dir, self.out)
        with open(self.out / "land_today.csv", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]["what_is_there"], "'=HYPERLINK(\"http://x\")")
        data = json.loads((self.out / "land_today.json").read_text())
        self.assertEqual(data["parcels"][0]["what_is_there"], '=HYPERLINK("http://x")')

    def test_atomic_write_keeps_old_files(self):
        bc.write_outputs(self.check(), self.run_dir, self.out)
        before = {p.name: p.read_bytes() for p in self.out.iterdir()}
        with mock.patch.object(viewer.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                bc.write_outputs(self.check(visual=self.visual({"1": self.entry(1)})), self.run_dir, self.out)
        self.assertEqual({p.name: p.read_bytes() for p in self.out.iterdir()}, before)

    def test_cli_exit_1_when_a_fetch_failed(self):
        self.fake.fail = {"records": arcgis.ArcGISError("down")}
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(bc, "Fetcher", lambda **kw: self.fetcher()):
            self.assertEqual(bc.main([str(self.run_dir), "--out", str(self.out)]), 1)
        self.assertTrue((self.out / "land_today.json").exists())

    def test_cache_days_must_be_a_finite_number_not_below_zero(self):
        for bad in ("nan", "inf", "-1"):
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err), \
                    mock.patch.object(bc, "Fetcher", mock.Mock(side_effect=AssertionError("fetched"))):
                code = bc.main([str(self.run_dir), "--out", str(self.out), "--cache-days", bad])
            self.assertEqual(code, 2, bad)
            self.assertIn("--cache-days", err.getvalue())

    def test_out_inside_the_repo_must_be_ignored(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = bc.main([str(self.run_dir), "--out", str(viewer.REPO_ROOT / "screener" / "bc-test")])
        self.assertEqual(code, 2)
        self.assertFalse((viewer.REPO_ROOT / "screener" / "bc-test").exists())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bc.main([str(self.run_dir), "--out", str(self.run_dir)]), 2)


class FetcherTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = mock.patch.object(arcgis, "CACHE_DIR", Path(tmp.name))
        p.start()
        self.addCleanup(p.stop)
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s

    def fetcher(self, **kw):
        return bc.Fetcher(clock=self.clock, sleep=self.sleep, **kw)

    def test_spacing_and_cached_answers(self):
        f = self.fetcher()
        answers = {"features": []}
        with mock.patch.object(arcgis, "request_json", return_value=answers) as rj:
            for i in range(5):
                f.get(bc.PARCELS_URL + "/query", {"i": i}, bc._features_problem(None))
            self.assertEqual(rj.call_count, 5)
            for call in rj.call_args_list:
                self.assertEqual(call.kwargs["max_age_days"], bc.DEFAULT_CACHE_DAYS)
        self.assertGreaterEqual(sum(self.slept), 4.0)
        self.slept.clear()
        with mock.patch.object(arcgis, "cached_json", return_value=(answers, 0.0)), \
                mock.patch.object(arcgis, "request_json") as rj:
            for i in range(5):
                f.get(bc.PARCELS_URL + "/query", {"i": i}, bc._features_problem(None))
            rj.assert_not_called()
        self.assertEqual(self.slept, [])

    def test_hosts_are_spaced_separately(self):
        f = self.fetcher()
        with mock.patch.object(arcgis, "request_json", return_value={"features": []}):
            f.get(bc.PARCELS_URL + "/query", {"a": 1}, bc._features_problem(None))
            f.get(bc.STRUCTURES_URL + "/query", {"a": 1}, bc._features_problem(None))
        self.assertEqual(self.slept, [])

    def test_stops_a_host_after_k_failures(self):
        f = self.fetcher(max_failures=3)
        with mock.patch.object(arcgis, "request_json", side_effect=arcgis.ArcGISError("x")) as rj:
            for i in range(3):
                with self.assertRaises(arcgis.ArcGISError):
                    f.get(bc.PARCELS_URL + "/query", {"i": i}, bc._features_problem(None))
            with self.assertRaises(bc.HostStopped):
                f.get(bc.PARCELS_URL + "/query", {"i": 9}, bc._features_problem(None))
            self.assertEqual(rj.call_count, 3)

    def test_stopped_host_parcels_read_not_checked(self):
        tmp = Path(tempfile.mkdtemp(dir=arcgis.CACHE_DIR))
        run = make_run(tmp)
        f = self.fetcher(max_failures=2)
        with mock.patch.object(arcgis, "request_json", side_effect=arcgis.ArcGISError("x")) as rj:
            res = bc.run_check(run, fetcher=f)
        self.assertEqual({x["built_check"] for x in res.built}, {bc.NOT_CHECKED})
        self.assertEqual(rj.call_count, 4)       # 2 per host, then nothing more


class PartialFailureTest(Offline):
    """reviews/37 fixes: a fetch that failed while the other input found
    something (a CHECK verdict) never gives raw_land, shows in land_today,
    and makes the CLI exit 1; a missing input file is refused (exit 2)."""

    def run_cli(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(bc, "Fetcher", lambda **kw: self.fetcher()):
            code = bc.main([str(self.run_dir), "--out", str(self.out), *extra])
        return code, out.getvalue(), err.getvalue()

    def visual_file(self, body) -> Path:
        path = self.tmp / "visual.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def test_records_failure_with_a_footprint_is_check_not_raw_land(self):
        self.run_dir = make_run(self.tmp / "one", n=1)
        # one ~4,500 SF footprint inside the outline: CHECK; the records query fails
        self.footprint(1, box(X0 + 0.0010, Y0 + 0.0010, X0 + 0.0012, Y0 + 0.0012), sqfeet=4_000)
        self.fake.fail = {"records": arcgis.ArcGISError("timeout")}
        vis = self.visual_file(self.visual({"1": self.entry(1)}))
        code, so, se = self.run_cli("--visual", str(vis))
        self.assertEqual(code, 1, se)
        self.assertIn("input missing", se)
        land = json.loads((self.out / "land_today.json").read_text())["parcels"][0]
        self.assertEqual(land["footprint_check"], bc.CHECK)
        self.assertNotEqual(land["land_today"], "raw_land")
        self.assertEqual(land["land_today"], "unclear")
        self.assertIn("records query failed", land["footprint_note"])

    def test_footprint_failure_with_a_record_inside_is_not_raw_land(self):
        self.run_dir = make_run(self.tmp / "one", n=1)
        inside = box(X0 + 0.0005, Y0 + 0.0005, X0 + 0.0008, Y0 + 0.0008)
        self.fake.records = [(parcel(0), {"ParcelID": "PID  001", "ImprAppr": 0}),
                             (inside, {"ParcelID": "SHED 9", "Address": "5 PUBLIC RD", "ImprAppr": 10})]
        self.fake.fail = {"ids": arcgis.ArcGISError("down")}
        res = self.check(visual=self.visual({"1": self.entry(1)}))
        self.assertEqual(res.built[0]["built_check"], bc.CHECK)
        self.assertEqual(res.land[0]["land_today"], "unclear")
        # a negative photo finding still stands
        res = self.check(visual=self.visual({"1": self.entry(1, usable="no", state="paved_yard_or_storage")}))
        self.assertEqual(res.land[0]["land_today"], "not_raw_land")

    def test_classify_land_incomplete_never_raw_land(self):
        v = {"state": "vacant_wooded", "usable": "yes", "evidence": "woods", "confidence": "high"}
        second = {"land_today": "raw_land", "what_is_there": "woods", "why": "x", "confidence": "high"}
        for check in (bc.CHECK, bc.NONE_FOUND, bc.BUILT):
            self.assertNotEqual(bc.classify_land(check, v, None, complete=False)["land_today"], "raw_land")
            self.assertNotEqual(bc.classify_land(check, v, second, complete=False)["land_today"], "raw_land")
        self.assertEqual(bc.classify_land(bc.NONE_FOUND, v, None)["land_today"], "raw_land")

    def test_missing_or_bad_input_files_exit_2_and_write_nothing(self):
        bad = self.tmp / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        for extra in (["--visual", str(self.tmp / "nope.json")], ["--visual", str(bad)],
                      ["--decisions", str(self.tmp / "nope.csv")]):
            with self.subTest(extra):
                code, _, se = self.run_cli(*extra)
                self.assertEqual(code, 2, se)
                self.assertIn("cannot read an input", se)
                self.assertFalse(self.out.exists())
        empty = self.tmp / "norun"
        empty.mkdir()
        self.run_dir = empty
        code, _, se = self.run_cli()
        self.assertEqual(code, 2, se)
        self.assertFalse(self.out.exists())

    def test_second_looks_carried_from_an_earlier_land_today(self):
        # rank 1 needs a second look (the photo is low-confidence): unclear
        # without one; an earlier land_today's second look decides it.
        vis = self.visual({"1": self.entry(1, confidence="low")})
        self.assertEqual(self.check(visual=vis).land[0]["land_today"], "unclear")
        earlier = [{"rank": 1, "parcel_id": "PID  001", "land_today": "partly_usable", "what_is_there": "half graded",
                    "why": "seen again", "confidence": "medium", "source": bc.SOURCE_SECOND},
                   {"rank": 2, "parcel_id": "PID  002", "land_today": "raw_land", "what_is_there": "woods",
                    "why": "", "confidence": "high", "source": bc.SOURCE_AGREES}]
        merged = bc.with_second_looks(vis, earlier, viewer.run_identity(self.run_dir), "earlier")
        self.assertEqual(list(merged["second_look"]), ["1"])
        land = self.check(visual=merged).land[0]
        self.assertEqual((land["land_today"], land["source"]), ("partly_usable", bc.SOURCE_SECOND))
        # through the CLI, and refused when the earlier file names another run
        path = self.tmp / "earlier.json"
        path.write_text(json.dumps(earlier), encoding="utf-8")
        code, so, se = self.run_cli("--visual", str(self.visual_file(vis)), "--second-looks-from", str(path))
        self.assertEqual(code, 0, se)
        self.assertEqual(json.loads((self.out / "land_today.json").read_text())["parcels"][0]["land_today"],
                         "partly_usable")
        other = {"run": {"name": "x", "ranked_csv_sha256": "0" * 64}, "parcels": earlier}
        with self.assertRaises(viewer.Refused):
            bc.with_second_looks(vis, other, viewer.run_identity(self.run_dir), "earlier")
        with self.assertRaises(viewer.Refused):
            bc.with_second_looks(None, earlier, viewer.run_identity(self.run_dir), "earlier")
        # a second look naming another parcel for its rank is refused by load_visual
        wrong = [dict(earlier[0], parcel_id="PID  002")]
        with self.assertRaises(viewer.Refused):
            self.check(visual=bc.with_second_looks(vis, wrong, viewer.run_identity(self.run_dir), "earlier"))


class StaticTest(unittest.TestCase):
    def test_fields_hold_no_owner_or_mailing(self):
        for fields in (bc.STRUCTURE_FIELDS, bc.PARCEL_FIELDS):
            self.assertNotRegex(fields, r"(?i)owner|mail|\*")
        for cols in (bc.BUILT_COLUMNS, bc.BUILT_JSON_EXTRA, bc.LAND_COLUMNS, bc.RECORD_KEYS):
            self.assertFalse([c for c in cols if "owner" in c.lower() or "mail" in c.lower()])

    def test_imports_and_no_global_assignment(self):
        src = Path(bc.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module.split(".")[0] for n in ast.walk(tree)
                  if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
        self.assertFalse(names & {"requests", "PIL", "numpy", "screener"}, names)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    self.assertFalse(isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                                     and t.value.id == "arcgis", ast.dump(t))
        self.assertNotIn("ThreadPoolExecutor", src)
        self.assertNotIn("startswith(", "".join(l for l in src.splitlines() if "owner" in l.lower()))

    def test_request_parameters_match_the_shared_cache(self):
        env = (-84.5, 33.6, -84.49, 33.61)
        expected = {"where": "1=1", "returnIdsOnly": "true", **arcgis._envelope_params(env)}
        self.assertEqual(bc._envelope_params(env), expected)
        p = bc.records_params(box(-84.5, 33.6, -84.49, 33.61))
        self.assertEqual(p["outFields"], bc.PARCEL_FIELDS)
        self.assertEqual(set(p), {"geometry", "geometryType", "inSR", "spatialRel", "outFields",
                                  "returnGeometry", "outSR"})



class HollowAnswersTest(Offline):
    """reviews/37 Agent 2, round 1: a hollow answer (county records in
    another spatial reference, a footprint without a shape, a county answer
    without the parcel's own record) is not checked, never 'no building
    found' or raw land, and the CLI exits 1; carried second looks and
    numeric options are checked."""

    run_cli = PartialFailureTest.run_cli
    visual_file = PartialFailureTest.visual_file

    def raw_land_visual(self):
        return self.visual_file(self.visual({"1": self.entry(1)}))

    def run_raw(self):
        code, _, se = self.run_cli("--visual", str(self.raw_land_visual()))
        self.assert_not_raw(code, se)

    def assert_not_raw(self, code, se):
        self.assertEqual(code, 1, se)
        built = json.loads((self.out / "builtcheck.json").read_text())["parcels"][0]
        land = json.loads((self.out / "land_today.json").read_text())["parcels"][0]
        self.assertEqual(built["built_check"], bc.NOT_CHECKED)
        self.assertNotEqual(land["land_today"], "raw_land")
        self.assertNotEqual(land["source"], bc.SOURCE_AGREES)

    def test_records_in_state_plane_are_not_checked(self):
        self.run_dir = make_run(self.tmp / "one", n=1)
        inside = box(X0 + 0.0005, Y0 + 0.0005, X0 + 0.0008, Y0 + 0.0008)
        self.fake.answer = {"records": {"spatialReference": {"wkid": 2240}, "features": [
            {"attributes": {"ParcelID": "PID  001"}, "geometry": esri(parcel(0))},
            {"attributes": {"ParcelID": "OTHER", "ImprAppr": 900_000}, "geometry": esri(inside)}]}}
        self.run_raw()

    def test_footprint_without_a_shape_is_not_checked(self):
        self.run_dir = make_run(self.tmp / "one", n=1)
        self.footprint(1, box(X0 + 0.0005, Y0 + 0.0005, X0 + 0.0025, Y0 + 0.0025), sqfeet=100_000)
        real = self.fake.route

        def route(kind, params):
            d = real(kind, params)
            if kind == "features":
                for f in d["features"]:
                    f["geometry"] = None
            return d
        self.fake.route = route
        self.run_raw()
        built = json.loads((self.out / "builtcheck.json").read_text())["parcels"][0]
        self.assertIn("without a shape", built["why"])

    def test_county_answer_without_the_parcels_own_record_is_not_checked(self):
        self.run_dir = make_run(self.tmp / "one", n=1)
        self.fake.answer = {"records": {"features": [], "spatialReference": {"wkid": 4326}}}
        self.run_raw()
        built = json.loads((self.out / "builtcheck.json").read_text())["parcels"][0]
        self.assertIn("own county record", built["why"])

    def test_a_held_second_look_is_not_carried_as_a_verdict(self):
        v = {"state": "vacant_wooded", "usable": "yes", "evidence": "woods", "confidence": "high"}
        second = {"land_today": "raw_land", "what_is_there": "woods", "why": "x", "confidence": "high"}
        held = bc.classify_land(bc.NONE_FOUND, v, second, complete=False)
        self.assertEqual((held["land_today"], held["source"]), ("unclear", bc.SOURCE_SECOND_HELD))
        kept = bc.classify_land(bc.NONE_FOUND, dict(v, usable="no"), dict(second, land_today="not_raw_land"), complete=False)
        self.assertEqual(kept["source"], bc.SOURCE_SECOND)      # a negative verdict stands as the reviewer's
        rows = [{"rank": 1, "parcel_id": "PID  001", **{k: held[k] for k in ("land_today", "what_is_there", "why", "confidence", "source")}},
                {"rank": 2, "parcel_id": "PID  002", "land_today": "partly_usable", "what_is_there": "x", "why": "",
                 "confidence": "high", "source": bc.SOURCE_SECOND}]
        self.assertEqual(list(bc.second_looks_from(rows)), ["2"])

    def test_second_looks_file_without_second_looks_is_refused(self):
        vis = self.visual_file(self.visual({"1": self.entry(1)}))
        path = self.tmp / "builtcheck.json"
        path.write_text(json.dumps([{"rank": 1, "parcel_id": "PID  001", "built_check": bc.NONE_FOUND}]), encoding="utf-8")
        code, _, se = self.run_cli("--visual", str(vis), "--second-looks-from", str(path))
        self.assertEqual(code, 2, se)
        self.assertIn("no 'second look' rows", se)
        self.assertFalse(self.out.exists())

    def test_top_must_be_positive(self):
        for top in ("0", "-5"):
            code, _, se = self.run_cli("--top", top)
            self.assertEqual(code, 2, se)
            self.assertFalse(self.out.exists())

    def test_records_query_checks_wgs84(self):
        check = bc._features_problem(None, wgs84=True)
        self.assertIsNone(check({"features": [{}], "spatialReference": {"wkid": 4326}}))
        self.assertIn("WGS84", check({"features": [{}], "spatialReference": {"wkid": 2240}}))
        self.assertIn("WGS84", check({"features": [{}]}))
        self.assertIsNone(check({"features": [], "spatialReference": {"wkid": 2240}}))


if __name__ == "__main__":
    unittest.main()
