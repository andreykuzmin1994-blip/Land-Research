"""site_fit.py: prototypes file, freeze, SiteFit formulas, fitter, rules,
projection, determinism, export, CLI (reviews/22_site_fit). Offline (SR-6).

Golden numbers are hand-derived from the operator's SiteFit tool
(geomBuilding / parkRows / detVolume; line numbers in
reviews/22_site_fit/02_risk_review.md, SF-07). The tool's file itself is not
committed.
"""

from __future__ import annotations

import ast
import io
import json
import math
import os
import random
import re
import subprocess
import tempfile
import time
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import tests  # noqa: F401 — suite guards (mirror kill switch, committed-file stubs)
import site_fit
import zoning
from hazards_helpers import disabled as hazards_disabled
from zoning_helpers import REPO_ROOT, patch_rules, stored, verified

PROTOS_PATH = REPO_ROOT / "building_prototypes.json"
SQFT_TO_M2 = 0.3048 * 0.3048


def repo_protos() -> dict:
    return json.loads(PROTOS_PATH.read_text(encoding="utf-8"))


def lattice(lo, hi, step=40.0):
    return [float(v) for v in range(int(lo), int(hi) + 1, int(step))]


SPECS = [(445.0, lattice(400, 640)), (890.0, lattice(600, 1000))]


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def fitted(rings, specs=SPECS):
    return {d: (r[0] if r else None) for d, r in site_fit.fit_lengths(rings, specs).items()}


def geojson(*parts):
    """GeoJSON (Polygon or MultiPolygon) from parts given as lists of rings."""
    def close(r):
        return [list(p) for p in r] + [list(r[0])]
    if len(parts) == 1:
        return json.dumps({"type": "Polygon", "coordinates": [close(r) for r in parts[0]]})
    return json.dumps({"type": "MultiPolygon",
                       "coordinates": [[close(r) for r in part] for part in parts]})


class FakeConn:
    """Answers site_fit's two reads; optional psycopg-like transaction state.

    ``inner``: parcel_id -> (gross_sqft, inner_geojson or None for empty,
    inner_area) or absent (= no geometry row). With ``tx=True`` the fake
    behaves like a psycopg connection inside an open transaction: a failed
    statement outside a savepoint aborts it until rollback."""

    class _Info:
        def __init__(self, conn):
            self.conn = conn

        @property
        def transaction_status(self):
            return 3 if self.conn.aborted else 2

    def __init__(self, zoning_rows=None, inner=None, fail=(), tx=False):
        self.zoning_rows = zoning_rows or {}
        self.inner = inner or {}
        self.fail = set(fail)
        self.executed = []
        self.aborted = False
        self.savepoints = 0
        self.rollbacks = 0
        if tx:
            self.info = FakeConn._Info(self)

    def cursor(self):
        conn = self

        class Cur:
            rows = []

            def execute(self, sql, params=()):
                if conn.aborted:
                    raise RuntimeError("current transaction is aborted")
                conn.executed.append((sql, params))
                if sql in conn.fail:
                    if hasattr(conn, "info") and conn.savepoints == 0:
                        conn.aborted = True
                    raise RuntimeError("injected")
                if sql == site_fit._SQL_ZONING_FOR_FIT:
                    self.rows = [(p,) + conn.zoning_rows[p] for p in params[0]
                                 if p in conn.zoning_rows]
                elif sql == site_fit._SQL_BUFFERED_FOR_FIT:
                    self.rows = []
                    for p in params[-1]:
                        if p not in conn.inner:
                            continue
                        gross, gj, area = conn.inner[p]
                        self.rows.append((p, None if gross is None else gross * SQFT_TO_M2,
                                          gj is None, area, gj))
                else:
                    self.rows = [(1,)]

            def fetchall(self):
                return list(self.rows)

            def fetchone(self):
                return self.rows[0] if self.rows else None

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None
        return Cur()

    def transaction(self):
        conn = self

        class Tx:
            def __enter__(self):
                conn.savepoints += 1
                return self

            def __exit__(self, et, ev, tb):
                conn.savepoints -= 1
                return False    # a savepoint rollback leaves the outer transaction usable
        return Tx()

    def rollback(self):
        self.rollbacks += 1
        self.aborted = False


def inner_rect(w, h, x0=2_000_000.0, y0=1_300_000.0):
    return geojson([rect(x0, y0, x0 + w, y0 + h)])


# ---------------------------------------------------------------------------
class PrototypesFileTests(unittest.TestCase):
    """SF-04 / SF-08: the human-owned file carries SiteFit's DEF numbers."""

    def test_repo_file_is_valid_and_carries_the_sitefit_defaults(self):
        d = repo_protos()
        self.assertEqual(site_fit.validate_prototypes(d), [])
        self.assertEqual(d["source"], "operator SiteFit tool defaults")
        self.assertEqual(d["as_of"], "2026-11-05")
        self.assertEqual(d["step_ft"], 40)
        self.assertEqual(d["default_setback_ft"], 25)
        keys = ("type", "L", "W", "clear", "court", "trailerOn", "trailer", "stall", "doorSp",
                "endClr")
        want = {
            "cross": ("cross", 1000, 520, 40, 130, True, 55, 12, 12, 60),
            "front": ("front", 640, 260, 36, 130, True, 55, 12, 12, 60),
            "rear": ("rear", 640, 260, 36, 130, True, 55, 12, 12, 60),
        }
        for k, vals in want.items():
            self.assertEqual(tuple(d["prototypes"][k][f] for f in keys), vals, k)
        self.assertEqual(d["prototypes"]["cross"]["min_length_ft"], 600)
        self.assertEqual(d["prototypes"]["front"]["min_length_ft"], 400)
        self.assertEqual(d["parking"]["car"], {"W": 500, "D": 124, "sw": 9, "sd": 18, "aisle": 24})
        self.assertEqual(d["parking"]["trailer"],
                         {"W": 600, "D": 190, "sw": 12, "sd": 55, "aisle": 80})
        self.assertEqual(d["detention"], {"L": 400, "W": 200, "depth": 6, "slope": 4})
        self.assertEqual(d["road"]["truck"]["width"], 30)
        self.assertEqual(d["road"]["car"]["width"], 24)

    def _bad(self, mutate):
        d = repo_protos()
        mutate(d)
        return site_fit.validate_prototypes(d)

    def test_schema_rejections(self):
        cases = {
            "off lattice": lambda d: d["prototypes"]["front"].update(min_length_ft=385),
            "endClr below pod": lambda d: d["prototypes"]["cross"].update(endClr=40),
            "zero width": lambda d: d["prototypes"]["rear"].update(W=0),
            "negative court": lambda d: d["prototypes"]["rear"].update(court=-1),
            "min above L": lambda d: d["prototypes"]["front"].update(min_length_ft=680),
            "wrong source": lambda d: d.update(source="someone else"),
            "bad date": lambda d: d.update(as_of="soon"),
            "missing prototype": lambda d: d["prototypes"].pop("rear"),
            "type mismatch": lambda d: d["prototypes"]["rear"].update(type="front"),
            "bool trailer": lambda d: d["prototypes"]["rear"].update(trailerOn="yes"),
            "step": lambda d: d.update(step_ft=0),
            "bool number": lambda d: d["prototypes"]["front"].update(L=True),
        }
        for label, m in cases.items():
            self.assertTrue(self._bad(m), label)

    def test_frozen_and_never_reread(self):
        a = site_fit.load_prototypes()
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("re-read")):
            b = site_fit.load_prototypes()
        self.assertIs(a, b)
        self.assertIsInstance(a, types.MappingProxyType)
        with self.assertRaises(TypeError):
            a["prototypes"]["cross"]["W"] = 400  # type: ignore[index]


class FreezeTests(unittest.TestCase):
    """SF-04: byte change, disappearing and appearing all raise."""

    def test_changed_missing_and_appearing(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "building_prototypes.json"
            p.write_text(json.dumps(repo_protos()), encoding="utf-8")
            st = site_fit._load_state(p)
            with mock.patch.object(site_fit, "_STATE", st):
                site_fit.verify_prototypes_unchanged(p)
                d = repo_protos()
                d["prototypes"]["cross"]["W"] = 400
                p.write_text(json.dumps(d), encoding="utf-8")
                with self.assertRaises(site_fit.PrototypesChanged):
                    site_fit.verify_prototypes_unchanged(p)
                p.unlink()
                with self.assertRaises(site_fit.PrototypesChanged):
                    site_fit.verify_prototypes_unchanged(p)
            st = site_fit._load_state(p)          # missing at load
            self.assertIsNone(st.data)
            with mock.patch.object(site_fit, "_STATE", st):
                site_fit.verify_prototypes_unchanged(p)
                p.write_text("{}", encoding="utf-8")
                with self.assertRaises(site_fit.PrototypesChanged):
                    site_fit.verify_prototypes_unchanged(p)

    def test_malformed_never_raises_on_load_and_projections_are_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "building_prototypes.json"
            p.write_text("{not json", encoding="utf-8")
            st = site_fit._load_state(p)
            self.assertIsNone(st.data)
            self.assertIn("not valid", st.problem or "")
            with mock.patch.object(site_fit, "_STATE", st):
                conn = FakeConn()
                p = site_fit.project_parcel(conn, "x")
            self.assertEqual(p.status, site_fit.UNAVAILABLE)
            self.assertEqual(conn.executed, [])
            self.assertFalse(site_fit.meets_bts_footprint(p))
            self.assertIn("unavailable", site_fit.data_gap_text(p))


class CommittedTests(unittest.TestCase):
    def _git(self, repo, *args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})

    def test_clean_dirty_untracked_and_missing(self):
        real = tests.REAL_VERIFY_PROTOTYPES_COMMITTED
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            self._git(repo, "init", "-q")
            f = repo / "building_prototypes.json"
            real(f)
            f.write_bytes(b'{"a": 1}\n')
            with self.assertRaises(site_fit.PrototypesChanged):
                real(f)
            self._git(repo, "add", "building_prototypes.json")
            self._git(repo, "commit", "-q", "-m", "p")
            real(f)
            f.write_bytes(b'{"a": 2}\n')
            with self.assertRaises(site_fit.PrototypesChanged):
                real(f)
            f.unlink()
            with self.assertRaises(site_fit.PrototypesChanged):
                real(f)

    def test_the_repo_file_is_committed(self):
        tests.REAL_VERIFY_PROTOTYPES_COMMITTED()

    def test_the_suite_stub_is_in_place(self):
        self.assertIsNot(site_fit.verify_prototypes_committed,
                         tests.REAL_VERIFY_PROTOTYPES_COMMITTED)


# ---------------------------------------------------------------------------
class SiteFitFormulaTests(unittest.TestCase):
    """SF-07: geomBuilding / parkRows / detVolume, ported literally."""

    P = site_fit.load_prototypes()["prototypes"]

    def test_default_prototypes(self):
        c, f, r = self.P["cross"], self.P["front"], self.P["rear"]
        self.assertEqual((site_fit.dock_doors(c, 1000), site_fit.trailer_stalls(c, 1000),
                          site_fit.envelope_depth(c)), (148, 166, 890.0))
        for p in (f, r):
            self.assertEqual((site_fit.dock_doors(p, 640), site_fit.trailer_stalls(p, 640),
                              site_fit.envelope_depth(p)), (44, 53, 445.0))
        self.assertEqual((site_fit.dock_doors(f, 400), site_fit.trailer_stalls(f, 400)), (24, 33))
        self.assertEqual((site_fit.dock_doors(c, 600), site_fit.trailer_stalls(c, 600)), (82, 100))

    def test_negative_run_gives_no_docks_and_stalls_ignore_end_clearance(self):
        f = dict(self.P["front"])
        # e = min(endClr, L/2) clamps the run at >= 0, so SiteFit's "span < 0 ->
        # 0 doors" branch is unreachable: a run of exactly 0 has one door.
        self.assertEqual(site_fit.dock_doors(f, 100), 1)        # e = 50, span 0
        self.assertEqual(site_fit.dock_doors(f, 131), 1)        # span 11 < 12 -> 1
        self.assertEqual(site_fit.dock_doors(f, 132), 2)        # span 12 -> 2
        f["endClr"] = 80
        self.assertEqual(site_fit.dock_doors(f, 120), 0 + 1)    # e = min(80, 60) -> span 0
        self.assertEqual(site_fit.dock_doors({**f, "endClr": 70}, 130), 1)
        self.assertEqual(site_fit.trailer_stalls(f, 100), 8)    # floor(100/12), no clearance
        self.assertEqual(site_fit.dock_doors({**f, "doorSp": 4}, 640),
                         math.floor((640 - 160) / 8) + 1)      # spacing floor of 8 ft
        self.assertEqual(site_fit.trailer_stalls({**f, "trailerOn": False}, 640), 0)
        self.assertEqual(site_fit.envelope_depth({**f, "trailerOn": False}), 390.0)

    def test_parking_and_detention(self):
        self.assertEqual(site_fit.park_rows(124, 18, 24), 4)
        self.assertEqual(site_fit.lot_stalls(500, 124, 9, 18, 24), 220)
        self.assertEqual(site_fit.park_rows(190, 55, 80), 2)
        self.assertEqual(site_fit.lot_stalls(600, 190, 12, 55, 80), 100)
        v = site_fit.detention_volume_cf(400, 200, 6, 4)
        a1, a2 = 400 * 200, (400 - 48) * (200 - 48)
        self.assertAlmostEqual(v, 6 / 3 * (a1 + a2 + math.sqrt(a1 * a2)))
        self.assertIsNone(site_fit.detention_volume_cf(40, 40, 6, 4))


# ---------------------------------------------------------------------------
def _brute_force_ok(rings, depth, length, pl, tol=1e-6):
    """Independent containment check by densified sampling (SF-05)."""
    rad = math.radians(pl.angle_deg)
    c, s = math.cos(rad), math.sin(rad)

    def to_rot(x, y):
        return x * c + y * s, -x * s + y * c

    x0, x1 = pl.xc - length / 2, pl.xc + length / 2
    y0, y1 = pl.y0, pl.y0 + depth

    def strictly_in(x, y):
        return x0 + tol < x < x1 - tol and y0 + tol < y < y1 - tol

    def winding(x, y):
        wn = 0
        for ring in rings:
            n = len(ring)
            for i in range(n):
                ax, ay = to_rot(*ring[i])
                bx, by = to_rot(*ring[(i + 1) % n])
                cross = (bx - ax) * (y - ay) - (x - ax) * (by - ay)
                if ay <= y < by and cross > 0:
                    wn += 1
                elif by <= y < ay and cross < 0:
                    wn -= 1
        return wn != 0

    for ring in rings:
        n = len(ring)
        for i in range(n):
            ax, ay = to_rot(*ring[i])
            bx, by = to_rot(*ring[(i + 1) % n])
            k = max(1, int(math.hypot(bx - ax, by - ay)))
            for j in range(k + 1):
                t = j / k
                if strictly_in(ax + (bx - ax) * t, ay + (by - ay) * t):
                    return False
    for (ax, ay), (bx, by) in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                               ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        k = max(1, int(math.hypot(bx - ax, by - ay) / 5))
        for j in range(k + 1):
            t = j / k
            px, py = ax + (bx - ax) * t, ay + (by - ay) * t
            # nudge inward so a sample on a flush edge is not ambiguous
            px = min(max(px, x0 + 1e-4), x1 - 1e-4)
            py = min(max(py, y0 + 1e-4), y1 - 1e-4)
            if not winding(px, py):
                return False
    return True


class FitterTests(unittest.TestCase):
    """SF-05 / SF-08: exact expected answers on fixtures."""

    def test_square_and_rectangle(self):
        self.assertEqual(fitted([rect(0, 0, 1000, 1000)]), {445.0: 640.0, 890.0: 960.0})
        self.assertEqual(fitted([rect(0, 0, 1000.1, 1000.1)]), {445.0: 640.0, 890.0: 1000.0})
        self.assertEqual(fitted([rect(0, 0, 700, 450)]), {445.0: 640.0, 890.0: None})
        self.assertEqual(fitted([rect(0, 0, 450, 700)]), {445.0: 640.0, 890.0: None})  # rotated 90

    def test_step_boundary_and_min_length(self):
        # front-load: 600 gives 156,000 SF (passes 150K), 560 gives 145,600
        self.assertEqual(fitted([rect(0, 0, 600.05, 450)])[445.0], 600.0)
        self.assertEqual(fitted([rect(0, 0, 600.0, 450)])[445.0], 560.0)   # tolerance: just below
        self.assertEqual(fitted([rect(0, 0, 399.9, 450)])[445.0], None)    # below the minimum
        self.assertEqual(fitted([rect(0, 0, 5000, 450)])[445.0], 640.0)    # capped at L

    def test_v_sided_rhombus_interior_optimum(self):
        # Review finding 4: the best slab is centred on y = 0, where no vertex
        # lies on the slab's edges. By hand: 1200 * (1 - 150/480) = 825 free
        # at 0 degrees, so lattice 800 (vertex events alone gave 720).
        ring = [(0, 0), (600, -480), (1200, 0), (600, 480)]
        got = site_fit.fit_lengths([ring], [(300.0, lattice(200, 1000))])[300.0]
        self.assertEqual(got[0], 800.0)
        self.assertGreaterEqual(got[1].length_free, 800.0)
        self.assertLessEqual(got[1].length_free, 825.0)
        self.assertTrue(_brute_force_ok([ring], 300.0, 800.0, got[1]))

    def test_candidate_positions_are_deterministic_and_inside(self):
        ys = site_fit._candidate_ys([0.0, 100.0, 480.0], 300.0, 0.01, 0.02, 179.98)
        self.assertEqual(ys, sorted(set(ys)))
        self.assertTrue(all(0.02 <= y <= 179.98 for y in ys))
        self.assertIn(0.02, ys)
        self.assertIn(179.98, ys)
        self.assertGreaterEqual(len(ys), site_fit._Y_SAMPLES)

    def test_rotated_rectangle(self):
        a = math.radians(33.0)
        r = [(x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a))
             for x, y in rect(0, 0, 700, 460)]
        self.assertEqual(fitted([r])[445.0], 640.0)

    def test_u_shape_and_notch(self):
        u = [(0, 0), (1000, 0), (1000, 1000), (700, 1000), (700, 300), (300, 300), (300, 1000),
             (0, 1000)]
        self.assertEqual(fitted([u]), {445.0: None, 890.0: None})
        # a 1000 x 500 lot with a 20 ft wide, 100 ft deep notch in one long side:
        # above it only 400 ft remain (< 445), so the envelope must sit beside it
        notch = [(0, 0), (490, 0), (490, 100), (510, 100), (510, 0), (1000, 0), (1000, 500),
                 (0, 500)]
        self.assertEqual(fitted([notch])[445.0], 480.0)           # 490 - margin -> 480 lattice
        self.assertEqual(fitted([rect(0, 0, 1000, 500)])[445.0], 640.0)

    def test_two_disjoint_squares_never_bridge_the_gap(self):
        a, b = rect(0, 0, 400, 460), rect(420, 0, 820, 460)
        self.assertEqual(fitted([a, b])[445.0], None)
        self.assertEqual(fitted([rect(0, 0, 820, 460)])[445.0], 640.0)

    def test_hole_is_respected(self):
        outer, hole = rect(0, 0, 700, 1000), rect(300, 450, 320, 470)
        self.assertEqual(fitted([outer])[890.0], 680.0)
        self.assertEqual(fitted([outer, hole])[890.0], None)     # any 890 slab spans the hole
        got = site_fit.fit_lengths([outer, hole], SPECS)[445.0]
        self.assertEqual(got[0], 640.0)            # fits beside the hole, not across it
        self.assertTrue(_brute_force_ok([outer, hole], 445.0, 640.0, got[1]))

    def test_triangle_sliver_and_tiny(self):
        tri = [(0, 0), (1400, 0), (0, 1000)]
        got = site_fit.fit_lengths([tri], SPECS)
        self.assertEqual(got[445.0][0], 640.0)
        self.assertTrue(_brute_force_ok([tri], 445.0, 640.0, got[445.0][1]))
        self.assertEqual(fitted([rect(0, 0, 3000, 40)]), {445.0: None, 890.0: None})
        self.assertEqual(fitted([[(0, 0), (1, 0), (0, 1)]]), {445.0: None, 890.0: None})
        self.assertEqual(fitted([]), {445.0: None, 890.0: None})

    def test_irregular_300_vertex_boundary(self):
        rings = [_wobbly(324, 800, seed=7)]
        got = site_fit.fit_lengths(rings, SPECS)
        for d, r in got.items():
            if r is not None:
                self.assertTrue(_brute_force_ok(rings, d, r[0], r[1]), d)
        self.assertIsNotNone(got[445.0])

    def test_random_polygons_brute_force_property(self):
        rng = random.Random(20261105)
        checked = 0
        for _ in range(40):
            n = rng.randint(5, 40)
            ring = _wobbly(n, rng.uniform(300, 900), seed=rng.randint(0, 10 ** 6), amp=0.35)
            for d, r in site_fit.fit_lengths([ring], SPECS).items():
                if r is not None:
                    checked += 1
                    self.assertTrue(_brute_force_ok([ring], d, r[0], r[1]), (n, d))
        self.assertGreater(checked, 10)


def _wobbly(n, radius, seed=0, amp=0.15):
    rng = random.Random(seed)
    k1, k2 = rng.randint(2, 6), rng.randint(7, 19)
    p1, p2 = rng.uniform(0, 6.3), rng.uniform(0, 6.3)
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        r = radius * (1 + amp * math.sin(k1 * t + p1) + amp / 3 * math.sin(k2 * t + p2))
        pts.append((round(r * math.cos(t) * 1.4, 2), round(r * math.sin(t), 2)))
    return pts


# ---------------------------------------------------------------------------
class RulesTests(unittest.TestCase):
    """SF-09 / SF-10 / SF-11: setback, coverage, height and buffer rules."""

    DATA = site_fit.load_prototypes()

    def rules(self, j, code, pid="p1"):
        st = stored(j, f"{j}_zoning", code) if j else None
        return site_fit._rules_for(st, pid, self.DATA)

    def test_every_district_in_the_rules(self):
        want = {
            ("south_fulton", "M-1"): 40, ("south_fulton", "M-2"): 40,
            ("south_fulton", "AG-1"): 60, ("fairburn", "M-1"): 35, ("fairburn", "M-2"): 40,
            ("fairburn", "PD"): 25, ("atlanta", "I-1"): 40, ("atlanta", "I-2"): 40,
            ("union_city", "M-1"): 50, ("fid", "M-1"): 40, ("fid", "M-2"): 40,
            ("fid", "M-1A"): 40,   # FID resolves to South Fulton's table (M-1A -> M-1)
        }
        rules = zoning.load_rules()["jurisdictions"]
        listed = {(j, c) for j, jj in rules.items() for c in jj["districts"]}
        self.assertEqual(listed, set(want))
        for (j, code), sb in want.items():
            self.assertEqual(self.rules(j, code).setback_ft, sb, (j, code))

    def test_height_note_on_every_rules_path(self):
        # Review finding 13: the height statement is there even without a rule.
        note = "height not checked (clear 40/36 ft; no district maximum in the rules)"
        self.assertIn(note, self.rules(None, None).notes)
        self.assertIn(note, self.rules("atlanta", "R-4").notes)      # unlisted district
        self.assertIn(note, self.rules("atlanta", "I-1").notes)      # max_height_ft null
        self.assertIn("height not checked (clear 40/36 ft; district max 48)",
                      self.rules("fairburn", "PD").notes)

    def test_rules_unavailable_is_named(self):
        # Review finding 12: a rules load failure is not "no district rule".
        with patch_rules(problem="zoning_rules.json schema: verified_by missing"):
            r = self.rules("south_fulton", "M-1")
        self.assertEqual(r.setback_ft, 25.0)
        self.assertEqual(r.label, "zoning rules unavailable")
        self.assertTrue(r.setback_source.startswith("default (zoning rules unavailable: "))
        self.assertFalse(r.from_rules)

    def test_labels_and_nulls(self):
        r = self.rules("south_fulton", "M-1")
        self.assertEqual(r.setback_source, "max(front/side/rear), south_fulton M-1")
        self.assertEqual(r.label, "draft rules")
        self.assertEqual(self.rules("atlanta", "I-1").setback_ft, 40)     # rear null skipped
        self.assertTrue(self.rules("fairburn", "PD").setback_source.startswith("default ("))
        none = self.rules(None, None)
        self.assertEqual((none.setback_ft, none.setback_source, none.label),
                         (25.0, "default (no district rule)", "no district rule"))
        r = self.rules("atlanta", "R-4")
        self.assertEqual(r.setback_ft, 25.0)
        self.assertIn("not listed in the rules", r.setback_source)

    def test_residential_buffers_are_never_folded_in(self):
        self.assertEqual(self.rules("south_fulton", "M-1").setback_ft, 40)   # not 100 / 110
        self.assertEqual(self.rules("union_city", "M-1").setback_ft, 50)     # not 75 + 20
        self.assertEqual(self.rules("fairburn", "M-1").setback_ft, 35)       # ROW buffer ignored
        src = (REPO_ROOT / "site_fit.py").read_text(encoding="utf-8")
        self.assertNotIn("buffer_along", src)   # no key is special-cased

    def test_every_listed_buffer_is_named_not_applied(self):
        # Review finding 2: Fairburn M-1's 50 ft buffer toward ANY other
        # district (Sec. 80-372) is larger than its 35 ft setback and is not
        # applied; the projection names it (and every other buffer key)
        # instead of calling the omission "residential" only.
        fb = self.rules("fairburn", "M-1")
        self.assertEqual(fb.setback_ft, 35)
        self.assertIn("buffers listed for fairburn M-1, not applied: "
                      "buffer_abutting_other_districts 50 ft, "
                      "buffer_along_all_public_ROW 25 ft (within the 35 ft setback), "
                      "side_rear_yard_abutting_residential 50 ft", fb.notes)
        sf = self.rules("south_fulton", "M-1")
        self.assertIn("buffers listed for south_fulton M-1, not applied: "
                      "plus_improvement_setback 10 ft (within the 40 ft setback), rear 100 ft, "
                      "side 50 ft", sf.notes)
        uc = self.rules("union_city", "M-1")
        self.assertTrue(any(n.startswith("buffers listed for union_city M-1") and
                            "buffer 75 ft" in n for n in uc.notes))
        self.assertFalse([n for n in self.rules("fairburn", "PD").notes if "buffer" in n])
        self.assertNotIn("neighbour zoning not in DB", site_fit.RES_BUFFERS)
        self.assertIn("only candidate parcels are stored", site_fit.RES_BUFFERS)

    def test_coverage_far_and_height(self):
        fb = self.rules("fairburn", "M-1")
        self.assertEqual(fb.cap_pct, 60.0)
        self.assertIn("height not checked (clear 40/36 ft; district max 48)", fb.notes)
        self.assertIsNone(self.rules("south_fulton", "M-1").cap_pct)
        at = self.rules("atlanta", "I-1")                                  # FAR 2.0: no cap
        self.assertIsNone(at.cap_pct)
        self.assertEqual(at.cap_text, "no lot coverage cap in the rules")
        self.assertTrue(any("does not limit a one-story footprint" in n for n in at.notes))
        self.assertEqual(self.rules("union_city", "M-1").cap_pct, 80.0)

    def test_same_code_different_jurisdiction(self):
        self.assertEqual(self.rules("union_city", "M-1").setback_ft, 50)
        self.assertEqual(self.rules("fairburn", "M-1").setback_ft, 35)
        self.assertEqual(self.rules("south_fulton", "M-1").setback_ft, 40)

    def test_verified_follows_the_rules_file(self):
        with patch_rules(verified("south_fulton")):
            r = self.rules("south_fulton", "M-1")
            self.assertEqual((r.verified, r.label), (True, "verified rules"))
            self.assertFalse(self.rules("fid", "M-1A").verified)   # FID itself still draft

    def test_non_numeric_value_is_reported_not_guessed(self):
        def mut(d):
            d["jurisdictions"]["union_city"]["districts"]["M-1"]["dimensions"][
                "front_setback_ft"]["value"] = "see table"
        with patch_rules(verified(mutate=mut)):
            r = self.rules("union_city", "M-1")
        self.assertEqual(r.setback_ft, 20)
        self.assertIn("front_setback_ft of union_city M-1 is not a number: not applied", r.notes)


# ---------------------------------------------------------------------------
SF_M1 = stored("south_fulton", "south_fulton_zoning", "M-1")
FB_M1 = stored("fairburn", "fairburn_zoning", "M-1")
INVESTIGATE = ("investigate further: no district setbacks in zoning_rules.json; "
               "25 ft default used")


# hazards is not the subject here: the gate is ON in the shipped repo since the
# operator-authorized prepare-mutation of 2026-11-09 (reviews/33_hazards_enabled);
# these fakes/records predate the hazard read, so they run with it OFF.
@hazards_disabled()
class ProjectionTests(unittest.TestCase):

    def setUp(self):
        site_fit._FIT_CACHE.clear()

    def test_ok_result_numbers_constraints_and_sql_parameters(self):
        conn = FakeConn({"a": SF_M1}, {"a": (1_000_000, inner_rect(1200, 900), 1_080_000.0)})
        p = site_fit.project_parcel(conn, "a")
        self.assertEqual(p.status, "ok")
        self.assertEqual(p.setback_ft, 40)
        sql, params = conn.executed[1]
        self.assertEqual(sql, site_fit._SQL_BUFFERED_FOR_FIT)
        self.assertEqual(params, site_fit._buffered_params(40.0, ["a"]))
        self.assertEqual((params[1], params[5], params[-1]), (40.0, 40.0, ["a"]))
        got = {f.key: (f.length_ft, f.building_sf, f.docks, f.trailer_stalls, f.coverage_pct,
                       f.binding) for f in p.fits}
        self.assertEqual(got["cross"], (1000.0, 520_000, 148, 166, 52.0, "full size"))
        self.assertEqual(got["front"], (640.0, 166_400, 44, 53, 16.6, "full size"))
        self.assertEqual(site_fit.max_building_sf(p), 520_000)
        for needle in (site_fit.FLOOD_WETLANDS, site_fit.RES_BUFFERS, site_fit.CAR_PARKING,
                       site_fit.FIT_RULE):
            self.assertIn(needle, p.constraints)
        self.assertEqual(p.constraints[0],
                         "setback 40 ft all sides = max(front/side/rear), south_fulton M-1, "
                         "draft rules")

    def test_coverage_cap_binds_and_steps_down(self):
        # Fairburn M-1: 60% of gross. Gross 250,000 SF -> 150,000 / 260 = 576 -> 560.
        conn = FakeConn({"a": FB_M1}, {"a": (250_000, inner_rect(800, 600), 480_000.0)})
        p = site_fit.project_parcel(conn, "a")
        front = p.fits[1]
        self.assertEqual((front.length_ft, front.building_sf, front.binding),
                         (560.0, 145_600, "coverage cap"))
        self.assertEqual(front.coverage_pct, round(100 * 145_600 / 250_000, 1))
        self.assertIn("max lot coverage 60% of gross parcel area, fairburn M-1", p.constraints)
        self.assertIn("height not checked (clear 40/36 ft; district max 48)", p.constraints)
        site_fit._FIT_CACHE.clear()
        conn = FakeConn({"a": SF_M1}, {"a": (250_000, inner_rect(800, 600), 480_000.0)})
        self.assertEqual(site_fit.project_parcel(conn, "a").fits[1].length_ft, 640.0)  # no cap

    def test_cap_below_minimum_reports_does_not_fit(self):
        conn = FakeConn({"a": FB_M1}, {"a": (150_000, inner_rect(800, 600), 400_000.0)})
        front = site_fit.project_parcel(conn, "a").fits[1]
        self.assertFalse(front.fits)
        self.assertEqual(front.binding, "does not fit: coverage cap below the minimum length")

    def test_statuses(self):
        conn = FakeConn({}, {"e": (300_000, None, 0.0), "z": (0, inner_rect(10, 10), 0.0)})
        r = site_fit.project_parcels(conn, ["n", "e", "z"])
        self.assertEqual(r["n"].status, "no_geometry")
        self.assertEqual(r["e"].status, "empty_after_setbacks")
        self.assertEqual(r["z"].status, "no_geometry")
        with mock.patch.object(site_fit, "_STATE", site_fit._State(None, None, "missing")):
            r = dict(r, u=site_fit.project_parcel(conn, "u"))
        self.assertEqual(r["u"].status, "unavailable")
        for p in r.values():
            for needle in site_fit.NOT_MODELLED:    # finding 7: detention included
                self.assertIn(needle, p.constraints)
            self.assertFalse(site_fit.meets_bts_footprint(p))
        self.assertIn(site_fit.DETENTION, site_fit.NOT_MODELLED)
        self.assertIsNone(site_fit.data_gap_text(r["e"]))       # nothing fits: correct N/A
        self.assertIn("no_geometry", site_fit.data_gap_text(r["n"]))

    def test_error_in_a_savepoint_leaves_the_connection_usable(self):
        conn = FakeConn({"a": SF_M1}, {}, fail={site_fit._SQL_BUFFERED_FOR_FIT}, tx=True)
        p = site_fit.project_parcel(conn, "a")
        self.assertEqual(p.status, "error")
        self.assertIn("RuntimeError", p.reason)
        self.assertFalse(conn.aborted)
        with conn.cursor() as cur:
            cur.execute("SELECT 1")                       # the next query still works
        self.assertIn("error", site_fit.data_gap_text(p))

    def test_without_a_savepoint_the_fake_would_abort(self):
        # the control for the test above: a failure outside a savepoint
        conn = FakeConn(fail={"SELECT boom"}, tx=True)
        with self.assertRaises(RuntimeError):
            with conn.cursor() as cur:
                cur.execute("SELECT boom")
        with self.assertRaises(RuntimeError):
            with conn.cursor() as cur:
                cur.execute("SELECT 1")

    def test_zoning_read_failure(self):
        conn = FakeConn(fail={site_fit._SQL_ZONING_FOR_FIT})
        self.assertEqual(site_fit.project_parcel(conn, "a").status, "error")

    def test_deterministic_across_part_order_and_vertex_rotation(self):
        a = rect(2_000_000, 1_300_000, 2_000_700, 1_300_460)
        b = [(2_001_000 + x, 1_300_000 + y) for x, y in _wobbly(60, 500, seed=3)]
        b = [(x + 600, y + 600) for x, y in b]
        g1 = geojson([a], [b])
        g2 = geojson([b[17:] + b[:17]], [a[2:] + a[:2]])
        out = []
        for g in (g1, g2, g1):
            site_fit._FIT_CACHE.clear()
            conn = FakeConn({"a": SF_M1}, {"a": (900_000, g, 500_000.0)})
            out.append(site_fit.project_parcel(conn, "a"))
        self.assertEqual(out[0], out[2])
        self.assertEqual([f.length_ft for f in out[0].fits], [f.length_ft for f in out[1].fits])
        self.assertEqual([f.building_sf for f in out[0].fits], [f.building_sf for f in out[1].fits])
        self.assertTrue(out[0].multipart)
        self.assertIn("multipart buildable area: each envelope fitted inside one part",
                      out[0].constraints)
        r = repr(out[0])
        self.assertNotRegex(r, r"\d{4}-\d{2}-\d{2}|[0-9a-f]{8}-[0-9a-f]{4}-")
        self.assertNotIn("coordinates", r)

    def test_cache_vs_no_cache_bit_identity(self):
        g = geojson([_wobbly(120, 700, seed=11)])
        conn = FakeConn({"a": SF_M1}, {"a": (1_400_000, g, 1_000_000.0)})
        site_fit._FIT_CACHE.clear()
        cold = site_fit.project_parcel(conn, "a")
        warm = site_fit.project_parcel(conn, "a")
        self.assertEqual(cold, warm)
        self.assertEqual(repr(cold), repr(warm))

    def test_result_is_frozen_and_bts_helper(self):
        conn = FakeConn({"a": SF_M1}, {"a": (1_000_000, inner_rect(700, 460), 300_000.0)})
        p = site_fit.project_parcel(conn, "a")
        with self.assertRaises(Exception):
            p.status = "ok"  # type: ignore[misc]
        with self.assertRaises(Exception):
            p.fits[0].building_sf = 10 ** 6  # type: ignore[misc]
        self.assertEqual(site_fit.max_building_sf(p), 166_400)
        self.assertFalse(site_fit.meets_bts_footprint(p))          # draft rules
        with patch_rules(verified("south_fulton")):
            site_fit._FIT_CACHE.clear()
            pv = site_fit.project_parcel(conn, "a")
        self.assertTrue(site_fit.meets_bts_footprint(pv))
        self.assertFalse(site_fit.meets_bts_footprint({"max_building_sf": 10 ** 6}))
        self.assertFalse(site_fit.meets_bts_footprint(None))

    def test_bts_threshold_boundary(self):
        def proj(sf, issue=True, from_rules=True, default_listed=False, verified_=True):
            f = site_fit.PrototypeFit("front", "front-load", sf is not None, False, 600.0, 260.0,
                                      445.0, sf, 41, 50, 10.0, "geometry", 0.0)
            p = site_fit.Projection("a", "ok", "projected", "verified rules", verified_, 25.0, "",
                                    10 ** 6, 10 ** 6, None, (f,), (),
                                    setback_from_rules=from_rules,
                                    setback_default_listed=default_listed)
            return site_fit._issue(p) if issue else p
        self.assertTrue(site_fit.meets_bts_footprint(proj(150_000)))
        self.assertFalse(site_fit.meets_bts_footprint(proj(149_999)))
        self.assertFalse(site_fit.meets_bts_footprint(proj(None)))
        self.assertEqual(site_fit.BTS_MIN_FOOTPRINT_SF, 150_000)
        # Finding 6 (kept for an unlisted district or an unreadable value):
        # a plain default setback is not a BTS input.
        self.assertFalse(site_fit.meets_bts_footprint(proj(520_000, from_rules=False)))
        # Operator decision D3 (reviews/29_operator_decisions, deliberately
        # inverted): a listed district without any setback counts with the
        # default, and is flagged whatever the size.
        d = proj(520_000, from_rules=False, default_listed=True)
        self.assertTrue(site_fit.meets_bts_footprint(d))
        self.assertEqual(site_fit.bts_review_text(d), INVESTIGATE)
        small = proj(149_999, from_rules=False, default_listed=True)
        self.assertFalse(site_fit.meets_bts_footprint(small))
        self.assertEqual(site_fit.bts_review_text(small), INVESTIGATE)
        # rules_verified stays required; the review text too.
        dv = proj(520_000, from_rules=False, default_listed=True, verified_=False)
        self.assertFalse(site_fit.meets_bts_footprint(dv))
        self.assertIsNone(site_fit.bts_review_text(dv))
        self.assertIsNone(site_fit.bts_review_text(proj(520_000)))     # district setbacks
        self.assertIsNone(site_fit.bts_review_text(proj(520_000, from_rules=False)))

    def test_a_projection_built_outside_the_module_never_passes(self):
        # Review finding 3: direct construction (the research.py forgery
        # repro), a copy, dataclasses.replace and a subclass all fail.
        import copy
        import dataclasses
        f = site_fit.PrototypeFit("cross", "cross-dock", True, True, 1000.0, 520.0, 890.0,
                                  520_000, 148, 166, 10.0, "full size", 0.0)
        forged = site_fit.Projection("x", "ok", "projected", "verified rules", True, 25.0, "",
                                     1, 1, None, (f,), (), setback_from_rules=True)
        self.assertFalse(site_fit.meets_bts_footprint(forged))
        real = site_fit._issue(dataclasses.replace(forged))
        self.assertTrue(site_fit.meets_bts_footprint(real))
        self.assertFalse(site_fit.meets_bts_footprint(copy.copy(real)))
        self.assertFalse(site_fit.meets_bts_footprint(dataclasses.replace(real)))
        self.assertFalse(site_fit.meets_bts_footprint(type(real)(*[
            getattr(real, k) for k in site_fit.Projection.__dataclass_fields__])))

    def test_a_default_setback_projection_built_outside_the_module_never_passes(self):
        # reviews/29_operator_decisions D3-06: the D3 path keeps the
        # forgery guard (copy, dataclasses.replace, direct construction).
        import copy
        import dataclasses
        f = site_fit.PrototypeFit("cross", "cross-dock", True, True, 1000.0, 520.0, 890.0,
                                  520_000, 148, 166, 10.0, "full size", 0.0)
        forged = site_fit.Projection("x", "ok", "projected", "verified jurisdiction, default "
                                     "setback", True, 25.0, "", 1, 1, None, (f,), (),
                                     setback_from_rules=False, setback_default_listed=True)
        self.assertFalse(site_fit.meets_bts_footprint(forged))
        self.assertIsNone(site_fit.bts_review_text(forged))
        real = site_fit._issue(dataclasses.replace(forged))
        self.assertTrue(site_fit.meets_bts_footprint(real))
        for fake in (copy.copy(real), dataclasses.replace(real), type(real)(*[
                getattr(real, k) for k in site_fit.Projection.__dataclass_fields__])):
            self.assertFalse(site_fit.meets_bts_footprint(fake))
            self.assertIsNone(site_fit.bts_review_text(fake))
        # A draft projection made to look listed-default is still False.
        draft = site_fit._issue(dataclasses.replace(forged, rules_verified=False))
        self.assertFalse(site_fit.meets_bts_footprint(draft))
        self.assertIsNone(site_fit.bts_review_text(draft))

    def test_verified_listed_district_without_setbacks_counts_with_the_review_text(self):
        # Review finding 6, reversed by operator decision D3 (2026-11-09,
        # reviews/29_operator_decisions): Fairburn PD (not_permitted ->
        # REZONING, passes the S9 gate) has no setbacks; its 25 ft default is
        # labelled, now counts for the BTS test and carries the investigate
        # text. The label value is unchanged (D3-04).
        pd = stored("fairburn", "fairburn_zoning", "PD")
        conn = FakeConn({"a": pd}, {"a": (2_000_000, inner_rect(1300, 1100), 1_400_000.0)})
        with patch_rules(verified("fairburn")):
            site_fit._FIT_CACHE.clear()
            p = site_fit.project_parcel(conn, "a")
        self.assertEqual((p.status, p.setback_ft, p.rules_verified, p.setback_from_rules,
                          p.setback_default_listed), ("ok", 25.0, True, False, True))
        self.assertEqual(p.rules_label, "verified jurisdiction, default setback")
        self.assertEqual(site_fit.VERIFIED_DEFAULT_LABEL, "verified jurisdiction, default setback")
        self.assertEqual(site_fit.max_building_sf(p), 520_000)
        self.assertTrue(site_fit.meets_bts_footprint(p))
        self.assertEqual(site_fit.bts_review_text(p), INVESTIGATE)
        self.assertEqual(p.constraints.count(INVESTIGATE), 1)
        # The same parcel with draft rules: unchanged (no flag, no BTS).
        site_fit._FIT_CACHE.clear()
        d = site_fit.project_parcel(conn, "a")
        self.assertEqual((d.rules_label, d.setback_default_listed), ("draft rules", False))
        self.assertFalse(site_fit.meets_bts_footprint(d))
        self.assertIsNone(site_fit.bts_review_text(d))
        self.assertNotIn(INVESTIGATE, d.constraints)
        self.assertFalse([c for c in d.constraints if "investigate" in c])
        with patch_rules(verified("fairburn")):
            site_fit._FIT_CACHE.clear()
            conn = FakeConn({"a": FB_M1}, {"a": (2_000_000, inner_rect(1300, 1100), 1.0)})
            m1 = site_fit.project_parcel(conn, "a")
        self.assertEqual((m1.rules_label, m1.setback_from_rules), ("verified rules", True))
        self.assertTrue(site_fit.meets_bts_footprint(m1))
        self.assertIsNone(site_fit.bts_review_text(m1))
        self.assertFalse(m1.setback_default_listed)

    def _fairburn(self, district, mutate=None):
        code = stored("fairburn", "fairburn_zoning", district)
        conn = FakeConn({"a": code}, {"a": (2_000_000, inner_rect(1300, 1100), 1_400_000.0)})
        with patch_rules(verified("fairburn", mutate=mutate)):
            site_fit._FIT_CACHE.clear()
            return site_fit.project_parcel(conn, "a")

    def test_d3_unlisted_district_stays_bts_na(self):
        # D3-01: an unlisted district (dimensions None) in a verified
        # jurisdiction keeps finding 6: default setback, BTS N/A, no review text.
        p = self._fairburn("R-1")
        self.assertEqual((p.status, p.rules_verified, p.setback_from_rules,
                          p.setback_default_listed), ("ok", True, False, False))
        self.assertIn("not listed in the rules", p.setback_source)
        self.assertEqual(site_fit.max_building_sf(p), 520_000)
        self.assertFalse(site_fit.meets_bts_footprint(p))
        self.assertIsNone(site_fit.bts_review_text(p))
        self.assertNotIn(INVESTIGATE, p.constraints)

    def test_d3_unreadable_setback_value_stays_bts_na(self):
        # D3-02: a present-but-unusable value is a data error, not an absent
        # rule: BTS N/A plus the existing note. All three null: counts.
        def see_table(data):
            data["jurisdictions"]["fairburn"]["districts"]["PD"]["dimensions"][
                "front_setback_ft"] = {"value": "see table", "cite": "x"}
        p = self._fairburn("PD", see_table)
        self.assertEqual((p.setback_from_rules, p.setback_default_listed), (False, False))
        self.assertIn("front_setback_ft of fairburn PD is not a number: not applied",
                      p.constraints)
        self.assertFalse(site_fit.meets_bts_footprint(p))
        self.assertIsNone(site_fit.bts_review_text(p))

        def negative(data):
            data["jurisdictions"]["fairburn"]["districts"]["PD"]["dimensions"][
                "side_setback_ft"] = -5
        n = self._fairburn("PD", negative)
        self.assertFalse(site_fit.meets_bts_footprint(n))
        self.assertIsNone(site_fit.bts_review_text(n))

        def all_null(data):
            dims = data["jurisdictions"]["fairburn"]["districts"]["PD"]["dimensions"]
            for k in ("front_setback_ft", "side_setback_ft", "rear_setback_ft"):
                dims[k] = {"value": None, "cite": "x"}
        a = self._fairburn("PD", all_null)
        self.assertTrue(site_fit.meets_bts_footprint(a))
        self.assertEqual(site_fit.bts_review_text(a), INVESTIGATE)

    def test_too_complex_ring_is_an_error_not_an_unbounded_search(self):
        # Review finding 5: a 1,000-vertex strip 440 ft wide (nothing fits,
        # so every angle would be searched; about 13 s uncapped) is refused
        # in well under a second.
        n = 1000
        top = [(2_000_000 + 3 * i, 1_300_440 + 5 * math.sin(i)) for i in range(n // 2)]
        bot = [(2_000_000 + 3 * i, 1_300_000 + 5 * math.sin(i)) for i in reversed(range(n // 2))]
        conn = FakeConn({"a": SF_M1}, {"a": (900_000, geojson([top + bot]), 600_000.0)})
        t = time.perf_counter()
        p = site_fit.project_parcel(conn, "a")
        self.assertLess(time.perf_counter() - t, 1.0)
        self.assertEqual(p.status, "error")
        self.assertIn("too complex (1000 vertices > 600)", p.reason)
        self.assertFalse(site_fit.meets_bts_footprint(p))
        self.assertIn("too complex", site_fit.data_gap_text(p))

    def test_simplification_in_postgis_is_conservative_and_stated(self):
        sql = site_fit._SQL_BUFFERED_FOR_FIT
        self.assertIn("ST_SimplifyPreserveTopology(ST_Buffer(t, -1.0 * (%s::float8 + 2.0 * "
                      "%s::float8)), %s::float8)", sql)
        self.assertIn("ST_Area(inner_g)", sql)       # buildable area is the unsimplified one
        self.assertEqual(sql.count("%s"), len(site_fit._buffered_params(40.0, ["a"])))
        self.assertEqual(site_fit._buffered_params(40.0, ["a"]),
                         (400, 40.0, 1.0, 1.0, 400, 40.0, ["a"]))
        row = ("a", 1_000_000 / site_fit.SQFT_PER_M2, False, 300_000.0,
               inner_rect(700, 460), True)
        p = site_fit._project_row("a", site_fit._rules_for(SF_M1, "a", site_fit.load_prototypes()),
                                  row, site_fit.load_prototypes())
        self.assertEqual(p.status, "ok")
        self.assertIn(site_fit.SIMPLIFIED, p.constraints)

    def test_worst_case_runtime_bound(self):
        # SF-12: a 324-vertex irregular buildable area (the landtest maximum
        # vertex count) where neither depth reaches full size, so every angle
        # is searched. About 0.2 s here; the bound leaves slack for CI.
        ring = [(2_000_000 + x, 1_300_000 + y) for x, y in _wobbly(324, 330, seed=5, amp=0.3)]
        conn = FakeConn({"a": SF_M1}, {"a": (500_000, geojson([ring]), 300_000.0)})
        t = time.perf_counter()
        site_fit.project_parcel(conn, "a")
        self.assertLess(time.perf_counter() - t, 2.0)


# ---------------------------------------------------------------------------
class ExportTests(unittest.TestCase):
    """SF-17: the SiteFit study round trip, with SiteFit's own plane math."""

    def test_round_trip_inside_the_setback_line(self):
        o = (33.6, -84.5)          # SiteFit local plane origin
        side, sb = 1100.0, 40.0
        corners_local = rect(0, 0, side, side)
        boundary_ll = [site_fit.to_ll(p, o) for p in corners_local] + \
            [site_fit.to_ll(corners_local[0], o)]          # closed on purpose
        inner = geojson([rect(sb, sb, side - sb, side - sb)])
        conn = FakeConn({"a": SF_M1}, {"a": (side * side, inner, (side - 2 * sb) ** 2)})
        site_fit._FIT_CACHE.clear()
        p = site_fit.project_parcel(conn, "a")
        for f in p.fits:
            if not f.fits:
                continue
            # stand-in for ST_Transform: here the 'feet plane' IS SiteFit's plane
            corners = site_fit.building_corners(p, f, margin=2.0)
            corners_ll = [site_fit.to_ll(c, o) for c in corners]
            doc = site_fit.study_document(p, f, boundary_ll, corners_ll)
            n = len(doc["boundary"])
            self.assertEqual(n, 4)                              # closing vertex dropped
            self.assertEqual(doc["setbacks"], [sb] * n)
            self.assertEqual(doc["edgeTags"], [""] * n)
            self.assertEqual((doc["v"], doc["sample"], doc["defaultSetback"], doc["view"]),
                             (1, False, 25, None))
            self.assertEqual(doc["name"], "a")
            self.assertTrue(doc["id"].startswith("lr-"))
            el = doc["elements"][0]
            self.assertEqual(el["kind"], "building")
            self.assertEqual(el["p"]["type"], f.key)
            self.assertEqual(el["p"]["L"], f.length_ft)
            self.assertIn(site_fit.FLOOD_WETLANDS, doc["notes"])
            # SiteFit: elLL = toLL(rot(pt, rot), center); front = -y (sides [-1])
            ct = f.depth_ft - f.width_ft
            L, W = el["p"]["L"], el["p"]["W"]
            sides = {"cross": (-1, 1), "front": (-1,), "rear": (1,)}[f.key]
            parts = [rect(-L / 2, -W / 2, L / 2, W / 2)]
            per_side = ct / len(sides)                          # court + trailer
            for s_ in sides:
                y_a, y_b = s_ * W / 2, s_ * (W / 2 + per_side)
                parts.append(rect(-L / 2, min(y_a, y_b), L / 2, max(y_a, y_b)))
            a = math.radians(el["rot"])
            for ring in parts:
                for x, y in ring:
                    rx, ry = x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)
                    ll = site_fit.to_ll((rx, ry), el["center"])
                    lx, ly = site_fit.to_local(ll, o)
                    self.assertGreaterEqual(min(lx, ly), sb - 0.5, (f.key, lx, ly))
                    self.assertLessEqual(max(lx, ly), side - sb + 0.5, (f.key, lx, ly))


# ---------------------------------------------------------------------------
class BoundaryTests(unittest.TestCase):
    """SF-19 / SF-06 / SF-10 / SF-16: what site_fit.py may import and contain."""

    SRC = (REPO_ROOT / "site_fit.py").read_text(encoding="utf-8")
    TREE = ast.parse(SRC)

    def test_imports_stdlib_and_zoning_only(self):
        mods = set()
        for node in ast.walk(self.TREE):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods.add((node.module or "").split(".")[0])
        # reviews/26_flood_wetlands (FW-26): "hazards" added deliberately,
        # imported once under the alias _hz (the host ban below still holds).
        # reviews/27_site_fit_buffers (BUF-22): "adjacency" added deliberately,
        # imported once under the alias _adj; never adjacency_import.
        allowed = {"__future__", "hashlib", "json", "math", "re", "subprocess", "types",
                   "weakref", "bisect", "dataclasses", "datetime", "pathlib", "typing", "zoning",
                   "hazards", "adjacency"}
        self.assertEqual(mods - allowed, set())
        self.assertIn("import hazards as _hz\n", self.SRC)
        self.assertIn("import adjacency as _adj\n", self.SRC)
        self.assertNotIn("adjacency_import", self.SRC)

    def test_requirements_unchanged(self):
        self.assertEqual((REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").split(),
                         ["psycopg[binary]>=3.1,<4", "python-dotenv>=1.0,<2", "requests>=2.31,<3"])

    def test_no_mercator_no_flood_hosts_no_adjacency_sql(self):
        consts = [n.value for n in ast.walk(self.TREE)
                  if isinstance(n, ast.Constant) and isinstance(n.value, (str, int))]
        for bad in (3857, 900913, "3857", "900913"):
            self.assertNotIn(bad, consts)
        self.assertNotRegex(self.SRC, r"3857|900913")
        self.assertNotRegex(self.SRC.lower(), r"fema\.gov|fws\.gov|hazards\.|wetlands\.")
        sqls = [v for k, v in vars(site_fit).items() if k.startswith("_SQL_")]
        self.assertTrue(sqls)
        # reviews/27_site_fit_buffers (review finding 6): the one allowed
        # proximity test compares an applied segment's end with the OTHER
        # applied segments of the same parcel (where two abutting parts meet),
        # never another parcel.
        same_parcel = ("FROM ad_lines o WHERE o.parcel_id = l.parcel_id "
                       "AND (o.adjacency_id <> l.adjacency_id OR o.part <> l.part) "
                       "AND ST_DWithin(o.line, dp.geom, 0.01)")
        for sql in sqls:
            for fn in ("ST_Touches", "ST_DWithin", "ST_Intersects", "JOIN parcels p2"):
                self.assertNotIn(fn, sql.replace(same_parcel, ""))
            self.assertNotIn("{", sql)
        self.assertIn("ST_Transform(g, 2240)", site_fit._SQL_BUFFERED_FOR_FIT)
        self.assertIn("-1.0 * %s::float8", site_fit._SQL_BUFFERED_FOR_FIT)
        self.assertEqual(site_fit.SRID_FEET, 2240)

    def test_exclusion_hook_is_private_and_empty(self):
        self.assertEqual(site_fit._flood_wetland_exclusions("x"), [])
        import inspect
        for name in ("project_parcel", "project_parcels"):
            params = list(inspect.signature(getattr(site_fit, name)).parameters)
            self.assertEqual(params[0], "conn", name)
            self.assertEqual(len(params), 2, name)


# ---------------------------------------------------------------------------
class CliTests(unittest.TestCase):
    """SF-18: no PII, safe file names, gitignored output, no DSN -> exit 2."""

    def test_no_dsn(self):
        import cli
        with mock.patch("prepare.dsn_available", return_value=False):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["site-fit", "--parcel", "a"])
        self.assertEqual(rc, 2)

    @hazards_disabled()  # the FakeConn predates the hazard read
    def test_output_never_carries_owner_or_address_and_files_are_safe(self):
        import cli
        import prepare
        sentinel = ("OWNER_SENTINEL", "MAIL_SENTINEL", "ADDRESS_SENTINEL")
        conn = FakeConn({"../x/y": SF_M1}, {"../x/y": (1_000_000, inner_rect(1200, 900), 9.0)})
        conn.read_only = False

        class Ctx:
            def __enter__(self):
                return conn

            def __exit__(self, *a):
                return False

        def export_geometry(c, p, f, margin=2.0):
            return ([[33.6, -84.5], [33.6, -84.49], [33.61, -84.49]],
                    [[33.601, -84.499], [33.601, -84.495], [33.605, -84.495], [33.605, -84.499]])
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(prepare, "dsn_available", return_value=True), \
                mock.patch.object(prepare, "get_connection", return_value=Ctx()), \
                mock.patch.object(site_fit, "export_geometry", export_geometry), \
                mock.patch.object(cli, "SITE_FIT_STUDIES_DIR", Path(td)):
            site_fit._FIT_CACHE.clear()
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["site-fit", "--parcel", "../x/y", "--export"])
            out = buf.getvalue()
            files = list(Path(td).iterdir())
            self.assertEqual(rc, 0)
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].parent, Path(td))
            self.assertRegex(files[0].name, r"^x_y-[0-9a-f]{8}\.sitefit\.json$")
            text = files[0].read_text(encoding="utf-8")
        for s in sentinel:
            self.assertNotIn(s, out + text)
        self.assertIn(site_fit.FLOOD_WETLANDS, out)
        self.assertIn(site_fit.FLOOD_WETLANDS, text)
        self.assertIn(site_fit.DETENTION, text)
        self.assertIn("(open it in SiteFit: Studies > Open file…)", out)   # finding 14
        self.assertTrue(conn.read_only)
        # the CLI's reads are site_fit's: parcel id, geometry, zoning only
        for sql, _ in conn.executed:
            self.assertNotRegex(sql, r"owner|address")

    def test_slugs(self):
        import cli
        a, b = cli._study_filename("../etc/passwd"), cli._study_filename("fulton-14 0144  LL0215")
        self.assertNotIn("/", a)
        self.assertFalse(a.startswith("."))
        self.assertNotEqual(cli._study_filename("a/b"), cli._study_filename("a b"))
        self.assertTrue(b.endswith(".sitefit.json"))

    def test_output_folder_and_tool_file_are_gitignored(self):
        for path in ("site_fit_studies/x.sitefit.json", "SiteFit.html", "sitefit/SiteFit.html"):
            r = subprocess.run(["git", "-C", str(REPO_ROOT), "check-ignore", "-q", path],
                               capture_output=True)
            self.assertEqual(r.returncode, 0, path)
        tracked = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files"], capture_output=True,
                                 text=True).stdout
        self.assertNotRegex(tracked.lower(), r"sitefit\.html")

    def test_makefile_target(self):
        mk = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("site-fit:", mk)
        self.assertIn("python cli.py --no-color site-fit $(ARGS)", mk)


if __name__ == "__main__":
    unittest.main()
