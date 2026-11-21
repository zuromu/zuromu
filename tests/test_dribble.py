from __future__ import annotations

import contextlib
import io
import math
import os
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

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
        self.assertEqual(dribble.bezier(0.0), "cubic-bezier(.3333,0.0000,.6667,0.3333)")
        self.assertEqual(dribble.bezier(2.0), "cubic-bezier(.3333,0.6667,.6667,1.0000)")

    def test_cell_center_and_keyframes_formatting(self) -> None:
        origin = dribble.Day(0, 0, date(2026, 1, 4), 1)
        self.assertEqual(
            dribble.cell_center(origin),
            (dribble.LEFT + dribble.CELL / 2, dribble.TOP + dribble.CELL / 2),
        )
        later = dribble.Day(3, 4, date(2026, 1, 29), 2)
        self.assertEqual(
            dribble.cell_center(later),
            (dribble.LEFT + 3 * dribble.PITCH + dribble.CELL / 2,
             dribble.TOP + 4 * dribble.PITCH + dribble.CELL / 2),
        )
        css = dribble.keyframes("test", [(0.0, "opacity:0"), (2.5, "opacity:1"), (10.0, "opacity:0")], 10.0)
        self.assertEqual(css, "@keyframes test{0.000%{opacity:0}25.000%{opacity:1}100.000%{opacity:0}}")

    def test_dribble_to_splits_long_gaps_and_clamps_apex(self) -> None:
        route = dribble.Route()
        route.drop((0.0, 0.0), dribble.DROP_HEIGHT)
        route.dribble_to((300.0, 0.0))

        hop_segs = route.segments[1:]
        expected_hops = math.ceil(300.0 / dribble.MAX_HOP)
        self.assertEqual(len(hop_segs), expected_hops * 2)
        self.assertEqual(route.here, (300.0, 0.0))

        peaks = [seg.z1 for seg in hop_segs[::2]]
        for peak in peaks:
            self.assertEqual(peak, dribble.APEX_MAX)
        for down_seg in hop_segs[1::2]:
            self.assertEqual(down_seg.z1, 0.0)
            self.assertGreater(down_seg.impact, 0.0)

        short = dribble.Route()
        short.drop((0.0, 0.0), dribble.DROP_HEIGHT)
        short.dribble_to((20.0, 0.0))
        self.assertEqual(len(short.segments), 3)
        self.assertAlmostEqual(short.segments[1].z1, dribble.APEX_MIN + dribble.APEX_PER_PX * 20.0)

    def test_shoot_and_settle_decay_until_below_settle_min(self) -> None:
        route = dribble.Route()
        route.drop((100.0, 180.0), dribble.DROP_HEIGHT)
        rim = (200.0, 180.0)
        before_shoot = len(route.segments)
        route.shoot(rim)
        after_shoot = len(route.segments)
        route.settle()

        shot_segs = route.segments[before_shoot:after_shoot]
        self.assertEqual(len(shot_segs), 3)
        self.assertEqual(shot_segs[0].z1, dribble.SHOT_APEX)
        self.assertEqual(shot_segs[1].z1, dribble.RIM_HEIGHT)
        self.assertEqual(shot_segs[1].t1, route.rim_time)
        self.assertEqual(shot_segs[2].z1, 0.0)
        self.assertGreater(shot_segs[2].impact, 0.0)

        settle_segs = route.segments[after_shoot:]
        self.assertGreater(len(settle_segs), 0)
        self.assertEqual(len(settle_segs) % 2, 0)
        peaks = [seg.z1 for seg in settle_segs[::2]]
        for peak in peaks:
            self.assertGreaterEqual(peak, dribble.SETTLE_MIN)
        for prev, nxt in zip(peaks, peaks[1:]):
            self.assertAlmostEqual(nxt / prev, dribble.RESTITUTION ** 2)
        for seg in settle_segs:
            self.assertEqual(seg.g0, rim)
            self.assertEqual(seg.g1, rim)
        next_v = settle_segs[-1].impact * dribble.RESTITUTION
        self.assertLess(next_v * next_v / (2 * dribble.G), dribble.SETTLE_MIN)

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

    def test_plan_dribbles_to_shot_spot_only_when_behind_it(self) -> None:
        days = make_days(date(2026, 1, 4), 7, {0})
        cell = dribble.cell_center(days[0])

        far_rim = (cell[0] + dribble.SHOT_DISTANCE + 80.0, cell[1])
        far_route = dribble.plan(days, far_rim)
        shot_start = next(s.g0 for s in far_route.segments if s.z1 == dribble.SHOT_APEX)
        self.assertEqual(shot_start, (far_rim[0] - dribble.SHOT_DISTANCE, far_rim[1]))

        close_rim = (cell[0] + dribble.SHOT_DISTANCE - 20.0, cell[1] + 10.0)
        close_route = dribble.plan(days, close_rim)
        close_shot_start = next(s.g0 for s in close_route.segments if s.z1 == dribble.SHOT_APEX)
        self.assertEqual(close_shot_start, cell)

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


class DribbleCalendarTests(unittest.TestCase):
    def test_demo_calendar_starts_on_sunday_and_is_deterministic(self) -> None:
        today = date(2026, 11, 22)

        class FixedDate(date):
            @classmethod
            def today(cls) -> date:
                return today

        with mock.patch.object(dribble, "date", FixedDate):
            first = dribble.demo_calendar(seed=7)
            second = dribble.demo_calendar(seed=7)

        self.assertEqual(first, second)
        self.assertEqual(first[0].row, 0)
        self.assertEqual(first[-1].day, today)
        self.assertGreaterEqual(len(first), 365)

        for idx, entry in enumerate(first):
            self.assertEqual(entry.col, idx // 7)
            self.assertEqual(entry.row, (entry.day.weekday() + 1) % 7)
            self.assertIn(entry.level, range(5))
            if idx:
                self.assertEqual(entry.day - first[idx - 1].day, timedelta(days=1))

    def test_fetch_calendar_maps_levels_and_weekdays(self) -> None:
        payload = {
            "user": {
                "contributionsCollection": {
                    "contributionCalendar": {
                        "weeks": [
                            {
                                "contributionDays": [
                                    {"date": "2026-01-04", "weekday": 0, "contributionLevel": "NONE"},
                                    {"date": "2026-01-05", "weekday": 1, "contributionLevel": "FIRST_QUARTILE"},
                                    {"date": "2026-01-06", "weekday": 2, "contributionLevel": "SECOND_QUARTILE"},
                                ]
                            },
                            {
                                "contributionDays": [
                                    {"date": "2026-01-11", "weekday": 0, "contributionLevel": "THIRD_QUARTILE"},
                                    {"date": "2026-01-12", "weekday": 1, "contributionLevel": "FOURTH_QUARTILE"},
                                ]
                            },
                        ]
                    }
                }
            }
        }
        with mock.patch.object(dribble, "graphql", return_value=payload) as gql:
            days = dribble.fetch_calendar("tok")

        gql.assert_called_once_with(dribble.CALENDAR_QUERY, {"login": dribble.USERNAME}, "tok")
        self.assertEqual(days, [
            dribble.Day(0, 0, date(2026, 1, 4), 0),
            dribble.Day(0, 1, date(2026, 1, 5), 1),
            dribble.Day(0, 2, date(2026, 1, 6), 2),
            dribble.Day(1, 0, date(2026, 1, 11), 3),
            dribble.Day(1, 1, date(2026, 1, 12), 4),
        ])


class DribbleSvgTests(unittest.TestCase):
    def assert_keyframes_ordered(self, svg: str) -> None:
        chunks = svg.split("@keyframes ")[1:]
        self.assertGreater(len(chunks), 0)
        for chunk in chunks:
            name, rest = chunk.split("{", 1)
            depth, idx = 1, 0
            while idx < len(rest) and depth > 0:
                if rest[idx] == "{":
                    depth += 1
                elif rest[idx] == "}":
                    depth -= 1
                idx += 1
            self.assertEqual(depth, 0, name)
            body = rest[:idx - 1]
            pcts = [float(p) for p in re.findall(r"(-?\d+(?:\.\d+)?)%\{", body)]
            self.assertGreater(len(pcts), 0, name)
            self.assertGreaterEqual(pcts[0], 0.0, name)
            self.assertLessEqual(pcts[-1], 100.0, name)
            self.assertEqual(pcts, sorted(pcts), name)

    def test_build_svg_valid_xml_populated_and_empty(self) -> None:
        populated = make_days(date(2026, 1, 4), 35, {1, 9, 20, 31})
        empty = make_days(date(2026, 1, 4), 35, set())

        for days in (populated, empty):
            for theme in dribble.THEMES.values():
                svg = dribble.build_svg(days, theme)
                root = ET.fromstring(svg)
                self.assertTrue(root.tag.endswith("svg"))
                self.assert_keyframes_ordered(svg)

    def test_build_svg_animates_active_cells_and_renders_months(self) -> None:
        active = {1, 18, 40}
        days = make_days(date(2026, 1, 4), 60, active)
        theme = dribble.THEMES["dark"]
        svg = dribble.build_svg(days, theme)
        ET.fromstring(svg)

        for idx in active:
            self.assertIn(f'id="k{idx}"', svg)
            self.assertIn(f"@keyframes k{idx}{{", svg)
        for idx in (0, 2, 17, 59):
            self.assertNotIn(f'id="k{idx}"', svg)
            self.assertNotIn(f"@keyframes k{idx}{{", svg)

        self.assertIn(">Feb</text>", svg)
        self.assertIn(">Mar</text>", svg)
        self.assertIn("@media (prefers-reduced-motion:reduce)", svg)
        self.assertIn(theme["ball"], svg)
        self.assertIn(theme["rim"], svg)


class DribbleCliTests(unittest.TestCase):
    def test_main_demo_writes_both_themes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            stdout = io.StringIO()
            with mock.patch.object(sys, "argv", ["dribble.py", "--demo", "--out", str(out_dir)]), \
                 contextlib.redirect_stdout(stdout):
                dribble.main()

            for name in ("dark", "light"):
                svg_path = out_dir / f"dribble-{name}.svg"
                self.assertTrue(svg_path.exists())
                content = svg_path.read_text(encoding="utf-8")
                ET.fromstring(content)
                self.assertIn(dribble.THEMES[name]["empty"], content)
            self.assertIn("drew the dribble into", stdout.getvalue())

    def test_main_requires_token_and_fetches_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(sys, "argv", ["dribble.py", "--out", tmp]):
                with self.assertRaises(SystemExit) as ctx:
                    dribble.main()
            self.assertIn("GITHUB_TOKEN", str(ctx.exception))

            sample = make_days(date(2026, 1, 4), 14, {3, 10})
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}, clear=False), \
                 mock.patch.object(sys, "argv", ["dribble.py", "--out", tmp]), \
                 mock.patch.object(dribble, "fetch_calendar", return_value=sample) as fetch_mock, \
                 contextlib.redirect_stdout(stdout):
                dribble.main()

            fetch_mock.assert_called_once_with("tok")
            for name in ("dark", "light"):
                svg_path = Path(tmp) / f"dribble-{name}.svg"
                self.assertTrue(svg_path.exists())
                ET.fromstring(svg_path.read_text(encoding="utf-8"))
            self.assertIn("(2 days with contributions)", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
