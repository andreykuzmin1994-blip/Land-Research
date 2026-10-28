"""Offline tests for the screener's pure logic (no network).

    python -m unittest discover -s screener/tests -t .
"""

import unittest

from shapely.geometry import Polygon

from screener import checks, rank, screens, zoning
from screener.fulton import _parse_date, jurisdiction
from screener.geo import LocalProjection, acres, esri_to_shape, haversine_miles
from screener.owners import is_po_box, mailing_state, owner_type
from screener.run import reason_key

SETTINGS = {
    "max_improvement_value": 250000, "max_flood_pct": 40, "max_floodway_pct": 10,
    "max_wetland_pct": 20, "max_protected_pct": 10, "max_steep_pct": 40,
    "min_usable_acres": 4, "exclude_tax_exempt": True, "exclude_government": True,
    "exclude_developer_owned": True, "exclude_utility_owned": True, "exclude_listed": True,
}


def parcel(**kw):
    base = {
        "parcel_id": "09F000000000001", "jurisdiction": "South Fulton / unincorporated",
        "acres": 20.0, "usable_acres": 18.0, "land_value": 400000, "improvement_value": 0,
        "class_code": "R5", "owner": "SMITH JOHN", "owner_type": "individual",
        "flood_pct": 0.0, "floodway_pct": 0.0, "wetland_pct": 0.0, "water_pct": 0.0,
        "protected_pct": 0.0, "easement_pct": 0.0, "exit_miles": 1.5,
        "entitlement": "possible", "industrial_within_1mi": 6, "rail_ft": None,
        "steep_pct": 10.0,
    }
    base.update(kw)
    return base


class OwnerTests(unittest.TestCase):
    def test_types(self):
        cases = {
            "CITY OF ATLANTA": "government",
            "FULTON COUNTY BOARD OF EDUCATION": "government",
            "DEVELOPMENT AUTHORITY OF FULTON COUNTY": "development_authority",
            "PROLOGIS LP": "developer",
            "LINK INDUSTRIAL PROPERTIES LLC": "developer",
            "NORFOLK SOUTHERN RAILWAY CO": "utility",
            "SMITH JOHN ESTATE OF": "estate",
            "JONES FAMILY REVOCABLE TRUST": "trust",
            "ACME REAL ESTATE LLC": "entity",
            "AIRPORT DISTRIBUTION CENTER LLC": "entity",
            "FIRST BAPTIST CHURCH OF FAIRBURN": "religious",
            "VARELA KENDRICK": "individual",
            "": "unknown",
        }
        for name, expected in cases.items():
            self.assertEqual(owner_type(name), expected, name)

    def test_mailing(self):
        self.assertEqual(mailing_state("DENVER CO 80237"), "CO")
        self.assertEqual(mailing_state("FAIRBURN GA 30213-1234"), "GA")
        self.assertIsNone(mailing_state("TORONTO ON M5V"))
        self.assertTrue(is_po_box("P O BOX 961089"))
        self.assertFalse(is_po_box("5050 S SYRACUSE ST STE 1200"))


class ZoningTests(unittest.TestCase):
    def test_codes(self):
        for code in ("M-1", "M-1A", "M-2", "I-1-C", "I1C", "MI", "I-2"):
            self.assertEqual(zoning.zoning_category(code), zoning.INDUSTRIAL, code)
        for code in ("AG-1", "A-L", "C-2", "C2C", "GC"):
            self.assertEqual(zoning.zoning_category(code), zoning.REZONABLE, code)
        for code in ("R-3", "RMD-1", "R-2 OVERLAY", "PD-H"):
            self.assertEqual(zoning.zoning_category(code), zoning.RESIDENTIAL, code)
        self.assertEqual(zoning.zoning_category(None), zoning.UNKNOWN)
        self.assertEqual(zoning.zoning_category("I-MIX-C"), zoning.FLEX)

    def test_flu(self):
        self.assertEqual(zoning.flu_category("Industrial Mix-Use Zone"), zoning.INDUSTRIAL)
        self.assertEqual(zoning.flu_category("Office/Industrial"), zoning.INDUSTRIAL)
        self.assertEqual(zoning.flu_category("Suburban Neighborhood"), zoning.RESIDENTIAL)
        self.assertEqual(zoning.flu_category("Conserve/ Preserve"), "conservation")
        self.assertEqual(zoning.flu_category("Rural/ Agricultural Neighborhood"), "rural")

    def test_entitlement(self):
        self.assertEqual(zoning.entitlement("industrial", "unknown", 0)[0], "by_right")
        self.assertEqual(zoning.entitlement("rezonable", "industrial", 0)[0], "likely")
        self.assertEqual(zoning.entitlement("residential", "residential", 2)[0], "hard")
        self.assertEqual(zoning.entitlement("residential", "residential", 12)[0], "hard")
        self.assertEqual(zoning.entitlement("residential", "unknown", 12)[0], "possible")
        self.assertEqual(zoning.entitlement("rezonable", "conservation", 30)[0], "hard")
        self.assertEqual(zoning.entitlement("unknown", "unknown", 0)[0], "unknown")


class GeoTests(unittest.TestCase):
    def test_rings_with_hole(self):
        shell = [[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]          # clockwise
        hole = [[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75], [0.25, 0.25]]
        shape = esri_to_shape({"rings": [shell, hole]})
        self.assertAlmostEqual(shape.area, 0.75)

    def test_acres_and_distance(self):
        # ~1 km square near Atlanta is ~247 acres.
        lon, lat = -84.5, 33.6
        dlon = 1000 / (111_320 * 0.8329)
        dlat = 1000 / 110_574
        sq = Polygon([(lon, lat), (lon + dlon, lat), (lon + dlon, lat + dlat), (lon, lat + dlat)])
        self.assertAlmostEqual(acres(LocalProjection.around(sq)(sq)), 247.1, delta=3)
        self.assertAlmostEqual(haversine_miles(-84.5, 33.6, -84.5, 33.7), 6.9, delta=0.1)


class HistogramTests(unittest.TestCase):
    def test_quantile_and_share(self):
        hist = {"min": 0.0, "max": 100.0, "counts": [25, 25, 25, 25]}
        self.assertAlmostEqual(checks._hist_quantile(hist, 0.5), 50.0)
        self.assertAlmostEqual(checks._hist_share_above(hist, 50.0), 50.0)
        self.assertAlmostEqual(checks._hist_share_above(hist, 62.5), 37.5)
        self.assertIsNone(checks._hist_share_above({"min": 0, "max": 1, "counts": []}, 0.5))


class ScreenTests(unittest.TestCase):
    def test_attribute_rejects(self):
        self.assertEqual(screens.attribute_rejects(parcel(), SETTINGS, {}), [])
        built = parcel(improvement_value=900000, land_value=200000)
        self.assertTrue(screens.attribute_rejects(built, SETTINGS, {}))
        # A barn on a big tract is not "built on".
        barn = parcel(improvement_value=300000, land_value=900000)
        self.assertEqual(screens.attribute_rejects(barn, SETTINGS, {}), [])
        dev = parcel(owner_type="developer")
        self.assertIn("already owned by an industrial developer / REIT",
                      screens.attribute_rejects(dev, SETTINGS, {}))
        passed = {"09F000000000001": {"decision": "pass", "date": "2026-10-01"}}
        self.assertTrue(screens.attribute_rejects(parcel(), SETTINGS, passed))

    def test_site_rejects(self):
        self.assertEqual(screens.site_rejects(parcel(), SETTINGS), [])
        self.assertTrue(screens.site_rejects(parcel(flood_pct=55.0), SETTINGS))
        self.assertTrue(screens.site_rejects(parcel(steep_pct=60.0), SETTINGS))
        self.assertTrue(screens.site_rejects(parcel(usable_acres=3.0), SETTINGS))
        # Slope not run yet: no slope-based reject.
        self.assertEqual(screens.site_rejects(parcel(steep_pct=None), SETTINGS), [])


class RankTests(unittest.TestCase):
    def test_closer_exit_scores_higher(self):
        near = rank.score(parcel(exit_miles=0.8))["score"]
        far = rank.score(parcel(exit_miles=5.0))["score"]
        self.assertGreater(near, far)

    def test_missing_features_do_not_crash(self):
        bare = {"parcel_id": "x", "acres": 10.0, "usable_acres": None}
        out = rank.score(bare)
        self.assertTrue(0 <= out["score"] <= 100)

    def test_seller_signals(self):
        long_held = rank.motivation_score(parcel(years_held=30, owner_state="FL"))[0]
        recent = rank.motivation_score(parcel(years_held=1, owner_type="entity"))[0]
        self.assertGreater(long_held, recent)


class MiscTests(unittest.TestCase):
    def test_reason_key(self):
        self.assertEqual(reason_key("45% in the 100-year floodplain"), "in the 100-year floodplain")
        self.assertEqual(reason_key("already built on (improvements $900,000)"), "already built on")
        self.assertEqual(reason_key("only 3.1 usable acres after constraints"),
                         "too few usable acres after constraints")

    def test_dates_and_jurisdictions(self):
        self.assertIsNone(_parse_date("0002-01-01 00:00:00"))
        self.assertIsNone(_parse_date("5024-01-01"))
        self.assertEqual(str(_parse_date("2017-08-11 00:00:00.0000000")), "2017-08-11")
        self.assertEqual(jurisdiction("55C"), "South Fulton / unincorporated")
        self.assertEqual(jurisdiction("05AZ"), "Atlanta")


if __name__ == "__main__":
    unittest.main()


class EvaluateTests(unittest.TestCase):
    """The AutoResearch scorer, on a synthetic pool (no data files needed)."""

    def setUp(self):
        from screener.autoresearch import evaluate as ev
        self.ev = ev
        self.records = []
        for i in range(200):
            rec = parcel(exit_miles=0.5 + (i % 50) / 10.0)
            rec.update({"key": f"k{i:03d}", "split": "dev" if i % 4 else "test", "signal": i})
            self.records.append(rec)
        # Positives: the ten highest "signal" values in dev.
        dev = [r for r in self.records if r["split"] == "dev"]
        self.positives = {r["key"] for r in sorted(dev, key=lambda r: -r["signal"])[:10]}

    def test_perfect_and_inverse_scorers(self):
        perfect = self.ev.evaluate(self.records, self.positives, "dev",
                                   scorer=lambda f: {"score": f["signal"]}, settings=SETTINGS)
        inverse = self.ev.evaluate(self.records, self.positives, "dev",
                                   scorer=lambda f: {"score": -f["signal"]}, settings=SETTINGS)
        self.assertEqual(perfect["k"], 3)  # 2% of 150 dev parcels
        self.assertEqual(perfect["hits"], 3)
        self.assertEqual(inverse["hits"], 0)
        self.assertEqual(perfect["positives"], 10)

    def test_scorer_never_sees_key_or_split(self):
        seen = []

        def spy(features):
            seen.append(set(features))
            return {"score": 0.0}

        self.ev.evaluate(self.records, self.positives, "dev", scorer=spy, settings=SETTINGS)
        self.assertTrue(seen)
        self.assertTrue(all("key" not in s and "split" not in s for s in seen))

    def test_screened_out_positives_cannot_hit(self):
        for r in self.records:
            if r["key"] in self.positives:
                r["flood_pct"] = 90.0
        res = self.ev.evaluate(self.records, self.positives, "dev",
                               scorer=lambda f: {"score": f["signal"]}, settings=SETTINGS)
        self.assertEqual(res["hits"], 0)
        self.assertEqual(res["positives_screened_out"], 10)
