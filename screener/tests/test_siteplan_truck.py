"""The truck model of the site planner (screener/siteplan_truck.py): the
swept path of a WB-67, the path search that turns no tighter than it can
steer, and the check of the two together (reviews/43). Pure geometry: no
network, no planner state."""
import math
import unittest

from shapely.geometry import LineString, Point, box

from screener import siteplan_truck as tk


def arc_path(R, deg, lead=120.0, tail=150.0, step=1.0):
    """A straight lead-in along +x, a left arc of radius R through deg degrees, a straight tail. Returns it and the arc's centre."""
    pts = [(-lead, 0.0), (0.0, 0.0)]
    n = max(2, int(R * math.radians(deg) / step))
    for k in range(1, n + 1):
        a = math.radians(deg) * k / n
        pts.append((R * math.sin(a), R * (1 - math.cos(a))))
    a = math.radians(deg)
    x, y = pts[-1]
    pts.append((x + tail * math.cos(a), y + tail * math.sin(a)))
    return LineString(pts), Point(0.0, R)


def radius_of(a, b, c):
    """Radius of the circle through three points (inf when in line)."""
    ab, bc, ca = math.dist(a, b), math.dist(b, c), math.dist(c, a)
    area2 = abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]))
    return float("inf") if area2 < 1e-9 else ab * bc * ca / (2 * area2)


class Vehicle(unittest.TestCase):
    def test_the_design_vehicle_is_the_wb67(self):
        v = tk.WB67
        # AASHTO 2018 Table 2-4a: 73.5 ft long, 8.5 ft wide, 4.0 ft front overhang, 4.5 ft rear overhang
        self.assertEqual((tk.LENGTH_FT, v["width"], v["front"], v["rear"]), (73.5, 8.5, 4.0, 4.5))
        self.assertEqual(tk.VEHICLE_NAME, "WB-67")
        self.assertGreaterEqual(tk.R_MIN, 41.0)                              # its centreline turning radius: the path never asks for less

    def test_a_straight_run_sweeps_the_bodys_own_width(self):
        sw, track = tk.sweep(LineString([(0, 0), (300, 0)]))
        minx, miny, maxx, maxy = sw.bounds
        self.assertAlmostEqual(maxy - miny, 8.5, places=6)
        self.assertAlmostEqual(maxx, 300 + 4.0, places=6)                    # the front overhang ahead of the steer axle
        self.assertAlmostEqual(minx, -(19.5 + 45.5 + 4.5), places=6)         # the trailer starts straight behind, in line
        self.assertAlmostEqual(sw.area, 8.5 * (300 + 73.5), delta=1.0)
        self.assertAlmostEqual(track.coords[-1][0], 300 - 65.0, places=6)    # the trailer's axles 65 ft behind the steer axle
        self.assertTrue(tk.sweep(LineString([(0, 0), (0.5, 0)]))[0].is_empty)

    def test_the_minimum_turn_matches_the_published_figures(self):
        # AASHTO (TxDOT Roadway Design Manual, Table 4-1): on a 41.0 ft
        # centreline turning radius the WB-67's minimum design turning
        # radius is 44.8 ft and its minimum inside radius 1.9 ft (a 180
        # degree turn)
        path, c = arc_path(41.0, 180)
        sw, _ = tk.sweep(path, ds=1.0)
        inside = c.distance(sw)
        self.assertAlmostEqual(inside, 1.9, delta=1.5)
        # the outer front wheel: the tractor's drive axles track inside the
        # steer axle by its wheelbase, and the wheel stands half a body out
        drive_axle = math.sqrt(41.0 ** 2 - tk.WB67["wb1"] ** 2)
        self.assertAlmostEqual(math.hypot(drive_axle + 8.5 / 2, tk.WB67["wb1"]), 44.8, delta=0.1)
        # the body's outer front corner, the front overhang further on: the swept path reaches it and no further
        corner = math.hypot(drive_axle + 8.5 / 2, tk.WB67["wb1"] + 4.0)
        self.assertTrue(sw.contains(Point(corner - 1.0, 41.0)))
        self.assertFalse(sw.intersects(Point(corner + 0.7, 41.0)))

    def test_the_trailer_cuts_inside_and_less_on_a_wider_turn(self):
        widths = []
        for R in (45.0, 60.0, 90.0):
            path, c = arc_path(R, 90)
            sw, track = tk.sweep(path, ds=1.0)
            inside = c.distance(sw)
            self.assertLess(inside, R - 8.5 / 2 - 5)                         # well inside the steer axle's own track
            widths.append(math.hypot(R + 8.5 / 2, 4.0) - inside)
        self.assertTrue(widths[0] > widths[1] > widths[2], widths)
        self.assertTrue(28 < widths[0] < 33, widths)                         # about 31 ft of pavement for a 90 degree turn at 45 ft
        self.assertTrue(20 < widths[2] < 25, widths)


class Search(unittest.TestCase):
    ground = box(0, 0, 600, 400)

    def test_straight_on(self):
        got = tk.drive((50, 10), math.pi / 2, (50, 300), math.pi / 2, self.ground.buffer(-6))
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got["line"].length, 300 - 10 + 30, delta=tk.STEP + 1)
        self.assertEqual({m[0] for m in got["moves"]}, {0})                  # no arcs

    def test_it_never_turns_tighter_than_the_vehicle_steers(self):
        free = self.ground.buffer(-6).difference(box(100, 0, 600, 250).buffer(6))
        got = tk.drive((50, 10), math.pi / 2, (300, 330), 0.0, free)
        self.assertIsNotNone(got)
        self.assertEqual({m[1] for m in got["moves"] if m[0]} - {tk.R_MIN, tk.R_EASY}, set())
        c = list(got["line"].coords)[:-1]                                    # (the last leg is the run-on, square to the goal)
        tight = min(radius_of(a, b, d) for a, b, d in zip(c, c[1:], c[2:]) if math.dist(a, b) > 1 and math.dist(b, d) > 1)
        self.assertGreater(tight, tk.R_MIN - 1.5)
        end = got["line"].coords[-1]
        self.assertAlmostEqual(end[1], 330, delta=12.5)                      # on the goal line, heading along it
        self.assertTrue(free.buffer(0.5).contains(LineString(c)))

    def test_no_path_where_the_ground_is_cut(self):
        free = self.ground.buffer(-6).difference(box(0, 150, 600, 200))      # a wall across the site
        self.assertIsNone(tk.drive((50, 10), math.pi / 2, (300, 330), 0.0, free))
        self.assertIsNone(tk.drive((-50, 10), math.pi / 2, (300, 330), 0.0, free))       # a start off the ground
        self.assertIsNone(tk.drive((50, 10), math.pi / 2, (300, 330), 0.0, box(0, 0, 0, 0)))

    def test_the_same_question_gives_the_same_path(self):
        free = self.ground.buffer(-6).difference(box(100, 0, 600, 250).buffer(6))
        a = tk.drive((50, 10), math.pi / 2, (300, 330), 0.0, free)
        b = tk.drive((50, 10), math.pi / 2, (300, 330), 0.0, free)
        self.assertEqual(list(a["line"].coords), list(b["line"].coords))
        self.assertEqual(a["expanded"], b["expanded"])

    def test_dear_ground_is_crossed_only_when_there_is_no_other_way(self):
        free = box(0, 0, 400, 300).buffer(-6)
        slab = box(150, 0, 250, 110)                                         # dear ground between start and goal, open ground above it
        got = tk.drive((50, 50), 0.0, (330, 50), 0.0, free, dear=slab, lateral=20.0)
        self.assertIsNotNone(got)
        across = tk.drive((50, 50), 0.0, (330, 50), 0.0, free, lateral=20.0)
        self.assertGreater(got["line"].length, across["line"].length)        # it pays length to stay off it
        self.assertLess(got["line"].intersection(slab).length, across["line"].intersection(slab).length)
        wall = box(150, 0, 250, 300)                                         # no way round: it is crossed
        self.assertIsNotNone(tk.drive((50, 50), 0.0, (330, 50), 0.0, free, dear=wall, lateral=20.0))


class Bends(unittest.TestCase):
    def test_bends_and_reverse_bends(self):
        arc = lambda hand, R: (hand, R, R * tk.TURN, (0.0, 0.0))
        st = lambda ft: (0, 0.0, ft, (0.0, 0.0))
        b = tk.bends([st(100)] + [arc(1, 45.0)] * 6 + [st(200)] + [arc(-1, 90.0)] * 2 + [st(50)])
        self.assertEqual([(x["deg"], x["radius_ft"], x["hand"]) for x in b["bends"]], [(90, 45.0, "left"), (30, 90.0, "right")])
        self.assertEqual((b["reverse"], b["min_radius_ft"]), (0, 45.0))      # 200 ft of straight between them
        self.assertEqual(b["bends"][0]["from_start_ft"], 100)
        # the same two bends with 40 ft between: a reverse bend
        self.assertEqual(tk.bends([arc(1, 45.0)] * 6 + [st(40)] + [arc(-1, 90.0)] * 2)["reverse"], 1)
        # the same hand again is not a reverse bend, and joins up across a short straight
        self.assertEqual(tk.bends([arc(1, 45.0)] * 3 + [st(40)] + [arc(1, 45.0)] * 3)["reverse"], 0)
        self.assertEqual(len(tk.bends([arc(1, 45.0)] * 3 + [st(10)] + [arc(1, 90.0)] * 3)["bends"]), 1)
        # one arc each way is a lane shift, not a bend
        wiggle = tk.bends([arc(1, 90.0), st(10), arc(-1, 90.0), st(100)])
        self.assertEqual((wiggle["bends"], wiggle["reverse"], wiggle["min_radius_ft"]), ([], 0, None))


class Design(unittest.TestCase):
    """A driveway on the south edge, a block the truck goes round, and a
    lane beyond it: the road's width decides."""
    ground = box(0, 0, 600, 400)

    def go(self, road):
        hard = box(road, 0, 600, 400 - road)
        start = (road / 2, 8.0)
        return tk.design(start, math.pi / 2, (300, 400 - road / 2), 0.0, self.ground, hard, mouth=Point(start[0], 0).buffer(70))

    def test_a_wide_corner_is_made(self):
        got = self.go(70.0)
        self.assertTrue(got["ok"])
        self.assertLessEqual(got["clip_ft"], tk.CLIP_TOL_FT)
        self.assertGreaterEqual(got["min_radius_ft"], tk.R_MIN)
        allowed = self.ground.difference(box(70, 0, 600, 330)).union(Point(35, 0).buffer(70))
        self.assertLess(got["swept"].difference(allowed).area, 60)

    def test_a_square_corner_between_40_ft_roads_is_not(self):
        got = self.go(40.0)
        self.assertIsNotNone(got)
        self.assertFalse(got["ok"])
        self.assertGreater(got["clip_ft"], tk.CLIP_TOL_FT)
        self.assertIsNotNone(got["clip_at"])
        self.assertGreaterEqual(got["swings"], 1)                            # it tried a wider line first

    def test_swinging_wide_makes_a_corner_the_first_line_cut(self):
        with_swing = self.go(55.0)
        self.assertTrue(with_swing["ok"])
        self.assertGreaterEqual(with_swing["swings"], 1)

    def test_a_40_ft_road_into_a_deep_court(self):
        # the planner's own case: a 40 ft end road beside the building, opening into a 130 ft court
        hard = box(40, 0, 600, 270)
        got = tk.design((20, 8), math.pi / 2, (300, 365), 0.0, self.ground, hard, mouth=Point(20, 0).buffer(70))
        self.assertTrue(got["ok"], got and got["clip_ft"])

    def test_the_body_must_stay_on_the_site(self):
        # the swept path is checked against the site's own edge too, except in the driveway's mouth
        got = tk.design((20, 8), math.pi / 2, (300, 365), 0.0, self.ground, box(40, 0, 600, 270), mouth=Point(20, 0).buffer(70))
        self.assertLess(got["swept"].difference(self.ground.union(Point(20, 0).buffer(70))).area, 60)
        self.assertEqual(tk.clip(box(-10, 0, 20, 10), box(0, 0, 100, 100))[0], 10.0)
        self.assertEqual(tk.clip(box(10, 10, 20, 20), box(0, 0, 100, 100)), (0.0, None))

    def test_keep_off_ground_holds_the_centreline_not_the_body(self):
        hard = box(40, 0, 600, 270)
        zone = box(40, 270, 600, 330)                                        # the strip in front of the dock doors
        got = tk.design((20, 8), math.pi / 2, (300, 365), 0.0, self.ground, hard, keep_off=zone, mouth=Point(20, 0).buffer(70))
        self.assertTrue(got["ok"])
        self.assertLess(got["line"].intersection(zone).length, 1.0)

    def test_none_when_there_is_no_way_at_all(self):
        self.assertIsNone(tk.design((20, 8), math.pi / 2, (300, 365), 0.0, self.ground, box(0, 100, 600, 300)))


if __name__ == "__main__":
    unittest.main()
