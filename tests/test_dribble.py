from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import dribble


def make_days(start: date, count: int, active_offsets: set[int]) -> list[dribble.Day]:
    days = []
    for offset in range(count):
        d = start + timedelta(days=offset)
        days.append(dribble.Day(offset // 7, (d.weekday() + 1) % 7, d, 2 if offset in active_offsets else 0))
    return days


class DribbleMathAndPlanTests(unittest.TestCase):
    def test_physics_helpers(self) -> None:
        self.assertEqual(dribble.rise_time(0.0), 0.0)
        t = dribble.rise_time(80.0)
        self.assertAlmostEqual(0.5 * dribble.G * t * t, 80.0)
        self.assertEqual(dribble.lerp((0.0, 10.0), (10.0, 20.0), 0.25), (2.5, 12.5))
        self.assertTrue(dribble.bezier(2.0).startswith("cubic-bezier("))

    def test_plan_visits_active_days_in_date_order_and_finishes_at_hoop(self) -> None:
        # Sunday 2026-01-04
        days = make_days(date(2026, 1, 4), 21, {2, 10, 15})
        rim = (400.0, 180.0)
        route = dribble.plan(days, rim)

        self.assertEqual(set(route.lit.keys()), {2, 10, 15})
        self.assertLess(route.lit[2], route.lit[10])
        self.assertLess(route.lit[10], route.lit[15])
        self.assertGreater(route.rim_time, route.lit[15])
        self.assertGreater(route.t, route.rim_time)
        self.assertEqual(route.here, rim)
        for seg in route.segments:
            self.assertLess(seg.t0, seg.t1)

    def test_plan_handles_empty_calendar(self) -> None:
        days = make_days(date(2026, 1, 4), 14, set())
        rim = (300.0, 180.0)
        route = dribble.plan(days, rim)

        self.assertEqual(route.lit, {})
        self.assertGreater(route.rim_time, 0.0)
        self.assertEqual(route.here, rim)

    def test_month_labels_drops_crowded_earlier_month(self) -> None:
        # Two month starts only 2 columns apart: earlier one is dropped
        days = [
            dribble.Day(0, 0, date(2026, 1, 1), 0),
            dribble.Day(2, 0, date(2026, 2, 1), 0),
            dribble.Day(6, 0, date(2026, 3, 1), 0),
        ]
        self.assertEqual(dribble.month_labels(days), [(2, "Feb"), (6, "Mar")])


class DribbleSvgTests(unittest.TestCase):
    def test_build_svg_valid_xml_populated_and_empty(self) -> None:
        populated = make_days(date(2026, 1, 4), 35, {1, 9, 20, 31})
        empty = make_days(date(2026, 1, 4), 35, set())

        for days in (populated, empty):
            for theme in dribble.THEMES.values():
                svg = dribble.build_svg(days, theme)
                root = ET.fromstring(svg)
                self.assertTrue(root.tag.endswith("svg"))


if __name__ == "__main__":
    unittest.main()
