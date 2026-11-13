"""The 2026-11-13 screen changes from the lead document checks. Offline.

- screens.building_rejects: commercial buildings on the county roll mean an
  operating site (live only; the frozen evaluation never applies it);
- owners: Norfolk Southern's real-estate company is a railroad owner, and
  quarry / waste operator names are told apart (report only);
- nextdoor.py and the lead grade: the owner's built parcel next door of any
  size, a listed contamination site close by or on a bordering parcel.
"""

from __future__ import annotations

import csv
import io
import unittest
import zipfile
from pathlib import Path

from shapely.geometry import Point

from screener import fulton, grade, nextdoor, owners, run, screens
from screener.tests.test_grade import S, clear_parcel
from screener.tests.test_integrity import (
    A, FakeLayer, Offline, RunHarness, county_parcel, polygon_feature, square,
)

SCREENER = Path(run.__file__).resolve().parent
SCR = {"built_min_building_sf": 2500, "built_min_improvement_value": 50000}


class BuildingScreenTests(unittest.TestCase):
    def test_thresholds(self):
        p = {"commercial_buildings": 3, "commercial_building_sf": 4407.0,
             "improvement_value": 230700.0}
        self.assertEqual(screens.building_rejects(p, SCR), [
            "already built on (the county lists 3 commercial buildings, 4,407 sq ft, and "
            "improvements $230,700)"])
        self.assertEqual(screens.building_rejects({**p, "commercial_building_sf": 2499.0}, SCR), [])
        self.assertEqual(screens.building_rejects({**p, "improvement_value": 49999.0}, SCR), [])
        self.assertEqual(screens.building_rejects(p, {**SCR, "built_min_building_sf": 0}), [])
        self.assertEqual(screens.building_rejects({"improvement_value": 9e6}, SCR), [])
        one = {**p, "commercial_buildings": 1}
        self.assertIn("1 commercial building,", screens.building_rejects(one, SCR)[0])

    def test_defaults_when_settings_lack_the_keys(self):
        p = {"commercial_buildings": 1, "commercial_building_sf": 3000.0,
             "improvement_value": 60000.0}
        self.assertTrue(screens.building_rejects(p, {}))

    def test_the_evaluation_never_applies_it(self):
        for path in (SCREENER / "autoresearch").glob("*.py"):
            with self.subTest(file=path.name):
                text = path.read_text()
                self.assertNotIn("building_rejects", text)
                self.assertNotIn("load_structures", text)
                self.assertNotIn("commercial_building", text)


class StructureFileTests(Offline):
    def write_zip(self, rows):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Tax_StructureCommercial.txt",
                       "ObjectID\tParcelID\tCard\tAreaSF\n" + "".join(rows))
        path = self.tmp / "fulton_cama.zip"
        path.write_bytes(buf.getvalue())
        return path

    def test_counts_and_sums_per_parcel(self):
        self.write_zip(["1\tP1 \t1\t1000\n", "2\tP1\t2\t2500.5\n", "3\tP2\t1\tjunk\n",
                        "4\tP9\t1\t9999\n"])
        got = fulton.load_structures(self.tmp, {"P1", "P2", "P3"})
        self.assertEqual(got["P1"], {"commercial_buildings": 2, "commercial_building_sf": 3500.5})
        self.assertEqual(got["P2"], {"commercial_buildings": 1, "commercial_building_sf": 0.0})
        self.assertNotIn("P3", got)
        self.assertNotIn("P9", got)

    def test_unreadable_file_is_a_clean_stop(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Tax_Sales.txt", "x")
        (self.tmp / "fulton_cama.zip").write_bytes(buf.getvalue())
        with self.assertRaises(fulton.arcgis.ArcGISError):
            fulton.load_structures(self.tmp, {"P1"})


class OwnerRuleTests(unittest.TestCase):
    def test_railroad_real_estate_company(self):
        self.assertEqual(owners.owner_type("SOUTHERN REGION INDUSTRIAL REALTY INC  ET AL"),
                         "utility")
        self.assertEqual(owners.owner_type("SOUTHERN INDUSTRIAL REALTY LLC"), "entity")

    def test_operating_kind(self):
        q, w = "a quarry or aggregates company", "a waste or sanitation company"
        cases = {
            "MARTIN MARIETTA MATERIALS REAL ESTATE INVESTMENTS INC": q,
            "VULCAN MATERIALS CO": q, "STONE QUARRY CO": q, "SOUTHERN AGGREGATES LLC": q,
            "CLM SANITATION LLC": w, "GEORGIA WASTE SYSTEM INC": w,
            "SAFEGUARD LANDFILL MANAGEMENT, LLC": w,
            "QUARRY HILLS HOA": None, "ROCK QUARRY ROAD HOLDINGS": None,
            "COSTCO WHOLESALE CORPORATION": None, "SMITH JOHN": None, None: None,
        }
        for name, want in cases.items():
            with self.subTest(name=name):
                self.assertEqual(owners.operating_kind(name), want)


def neighbour(oid, pid, corner, owner, imp=0.0, acres=50.0, lucode="391"):
    return polygon_feature(square(*corner), ParcelID=pid, Owner=owner, ImprAppr=imp,
                           LandAcres=acres, LUCode=lucode)


class CheckOneTests(Offline):
    URL = "https://example.invalid/MapServer/11"

    def run_check(self, feats, sites=()):
        layer = FakeLayer(feats)
        self.serve({f"{self.URL}/query": layer})
        p = county_parcel("L1", A, owner="TEST OPERATOR INC")
        return nextdoor.check_one(p, self.URL, 10.0, 250000.0, nextdoor.Sites(list(sites))), layer

    def test_finds_the_owners_plant_through_its_other_parcel(self):
        side = square(*A).bounds[2] - A[0]
        strip = (A[0] + side, A[1])
        plant = (A[0] + 2 * side, A[1])
        far = (A[0] + 5 * side, A[1])
        feats = {1: neighbour(1, "L1", A, "TEST OPERATOR INC"),
                 2: neighbour(2, "S1", strip, "TEST OPERATOR INC", acres=6.0),
                 3: neighbour(3, "PLANT", plant, "TEST OPERATOR INCORPORATED", imp=19e6, acres=94.3),
                 4: neighbour(4, "FAR", far, "TEST OPERATOR INC", imp=5e6),
                 5: neighbour(5, "OTHER", (A[0], A[1] - 0.0026), "SOMEONE ELSE LLC", imp=5e6)}
        res, _ = self.run_check(feats)
        self.assertEqual([b.parcel_id for b in res.built], ["PLANT"])
        self.assertEqual(res.built[0].acres, 94.3)

    def test_the_owners_house_is_not_an_operating_site(self):
        side = square(*A).bounds[2] - A[0]
        feats = {1: neighbour(1, "HOUSE", (A[0] + side, A[1]), "TEST OPERATOR INC", imp=900000.0,
                              acres=3.0, lucode="101"),
                 2: neighbour(2, "SHOP", (A[0] - side, A[1]), "TEST OPERATOR INC", imp=900000.0,
                              acres=3.0, lucode="401")}
        res, _ = self.run_check(feats)
        self.assertEqual([b.parcel_id for b in res.built], ["SHOP"])

    def test_site_on_a_bordering_parcel_not_on_the_lead(self):
        side = square(*A).bounds[2] - A[0]
        nb = (A[0] + side, A[1])
        feats = {1: neighbour(1, "N1", nb, "SOMEONE ELSE LLC")}
        on_neighbour = Point(nb[0] + side / 2, nb[1] + 0.001)
        on_lead = Point(A[0] + side / 2, A[1] + 0.001)
        far = Point(A[0] + 20 * side, A[1])
        res, _ = self.run_check(feats, [(on_neighbour, "TEST WOOD TREATING"),
                                        (on_lead, "ON THE LEAD"), (far, "FAR AWAY")])
        self.assertEqual(res.sites, ["TEST WOOD TREATING"])
        self.assertEqual(res.built, [])

    def test_flag_lines(self):
        p = {nextdoor.OWNER_KEY: [nextdoor.Built("PLANT", 94.3, 19162800.0)],
             nextdoor.SITES_KEY: ["TEST WOOD TREATING"]}
        lines = nextdoor.flag_lines(p)
        self.assertIn("PLANT (94.3 ac, $19,162,800 improvements)", lines[0])
        self.assertIn("Possibly buffer or expansion land", lines[0])
        self.assertEqual(lines[1], "a bordering parcel holds a listed contamination site: "
                                   "TEST WOOD TREATING")
        self.assertEqual(nextdoor.flag_lines({nextdoor.OWNER_KEY: None}), [])


class GradeRuleTests(unittest.TestCase):
    def grade(self, s=S, **over):
        return grade.grade_parcel(clear_parcel(**over), s)

    def test_near_site_counts_and_far_does_not(self):
        lead = self.grade(contamination=[{"name": "TEST HSI", "kind": "GA EPD HSI", "feet": 169}])
        self.assertEqual((lead.grade, lead.found),
                         ("B", ["a listed contamination site 169 ft away (TEST HSI)"]))
        lead = self.grade(contamination=[{"name": "X", "kind": "EPA", "feet": 400}])
        self.assertEqual(lead.grade, "A")
        lead = self.grade(contamination=[{"name": "X", "kind": "EPA", "feet": 0},
                                         {"name": "Y", "kind": "EPA", "feet": 20}])
        self.assertEqual(lead.found, ["a listed contamination site is on the parcel"])
        off = grade.Settings(contamination_near_ft=0)
        lead = self.grade(off, contamination=[{"name": "X", "kind": "EPA", "feet": 169}])
        self.assertEqual(lead.grade, "A")

    def test_bordering_parcel_site(self):
        lead = self.grade(**{nextdoor.SITES_KEY: ["TEST WOOD TREATING"]})
        self.assertEqual(lead.found, ["a bordering parcel holds a listed contamination site "
                                      "(TEST WOOD TREATING)"])
        lead = self.grade(**{nextdoor.SITES_KEY: ["T"],
                             "contamination": [{"name": "Z", "kind": "EPA", "feet": 100}]})
        self.assertEqual(len(lead.found), 1)          # one contamination item, not two

    def test_operating_owner(self):
        lead = self.grade(owner="CLM SANITATION LLC")
        self.assertEqual(lead.found, ["the owner's name reads like a waste or sanitation "
                                      "company: possibly buffer or expansion land of its own "
                                      "operation"])
        self.assertEqual(self.grade(grade.Settings(operating_owner=False),
                                    owner="CLM SANITATION LLC").grade, "A")

    def test_any_size_not_checked_is_never_clear(self):
        lead = self.grade(**{nextdoor.OWNER_KEY: None, nextdoor.SITES_KEY: None,
                             nextdoor.REASON_KEY: "the county parcel layer failed (BadAnswer)"})
        self.assertEqual(lead.not_checked, ["neighbouring parcels of any size not checked (the "
                                            "county parcel layer failed (BadAnswer))"])
        off = grade.Settings(next_door_any_size=False)
        self.assertEqual(self.grade(off, **{nextdoor.OWNER_KEY: None}).grade, "A")

    def test_settings(self):
        s = grade.settings({"grade": {"next_door_any_size": False, "next_door_max_parcels": 10,
                                      "contamination_near_ft": 0, "operating_owner": False}})
        self.assertEqual((s.next_door_any_size, s.next_door_max_parcels,
                          s.contamination_near_ft, s.operating_owner), (False, 10, 0.0, False))
        for bad in ({"next_door_max_parcels": -1}, {"next_door_max_parcels": 2.5},
                    {"next_door_any_size": "yes"}, {"contamination_near_ft": -5}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                grade.settings({"grade": bad})

    def test_keys_carry_the_grade_stem(self):
        for key in (nextdoor.OWNER_KEY, nextdoor.SITES_KEY, nextdoor.REASON_KEY):
            self.assertTrue(any(w in key for w in grade.KEY_STEMS), key)

    def test_rank_and_scoring_never_name_the_new_facts(self):
        for name in ("rank.py", "scoring.py"):
            text = (SCREENER / name).read_text()
            for word in ("commercial_building", "nextdoor", "operating_kind"):
                with self.subTest(file=name, word=word):
                    self.assertNotIn(word, text)


class RunTests(RunHarness):
    def rows(self):
        with open(self.out / "ranked.csv", newline="") as fh:
            return {r["parcel_id"]: r for r in csv.DictReader(fh)}

    def test_built_site_is_screened_out_after_the_deed_file(self):
        self.parcels[0]["improvement_value"] = 230700.0       # under half the land value
        self.parcels[0]["land_value"] = 2235800.0
        self.load_structures.return_value = {
            "14F0001 0001": {"commercial_buildings": 3, "commercial_building_sf": 4407.0}}
        code, _ = self.main()
        self.assertEqual(code, 0)
        self.assertNotIn("14F0001 0001", self.rows())
        with open(self.out / "rejected.csv", newline="") as fh:
            rej = {r["parcel_id"]: r for r in csv.DictReader(fh)}
        self.assertTrue(rej["14F0001 0001"]["reasons"].startswith(
            "already built on (the county lists 3 commercial buildings, 4,407 sq ft"))
        asked = self.load_structures.call_args[0][1]
        self.assertEqual(asked, {"14F0001 0001", "14F0002 0002"})

    def test_owners_plant_of_any_size_next_door(self):
        self.neighbours.feats.update({
            1: neighbour(1, "PLANT", (A[0], A[1] + (square(*A).bounds[3] - A[1])),
                         "TEST OWNER ALPHA", imp=19e6, acres=94.3)})
        code, _ = self.main()
        self.assertEqual(code, 0)
        a, b = self.rows()["14F0001 0001"], self.rows()["14F0002 0002"]
        self.assertIn("Same owner, any size", a["flags"])
        self.assertIn("the same owner holds 1 built-on parcel adjacent", a["lead_found"])
        self.assertNotIn("any size", b["flags"])

    def test_neighbour_layer_failure_is_not_checked(self):
        self.neighbours.ids_answer = {"error": {"code": 500, "message": "down"}}
        code, _ = self.main()
        self.assertEqual(code, 0)
        for r in self.rows().values():
            self.assertIn("neighbouring parcels of any size not checked", r["lead_not_checked"])
        self.assertIn("NEIGHBOUR CHECK FAILED for 2 of 2 parcels", self.notes())


if __name__ == "__main__":
    unittest.main()
