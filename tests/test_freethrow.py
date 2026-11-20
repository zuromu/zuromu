from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import freethrow


class SimulateTests(unittest.TestCase):
    def test_same_seed_is_deterministic(self) -> None:
        a = freethrow.simulate(7, 42)
        b = freethrow.simulate(7, 42)
        self.assertEqual(a.result, b.result)
        self.assertEqual(a.speed, b.speed)
        self.assertEqual(a.angle, b.angle)
        self.assertEqual(a.path, b.path)

    def test_power_extremes_and_sweet_spot(self) -> None:
        short = freethrow.simulate(1, 1)
        self.assertEqual(short.result, "short")
        self.assertIsNone(short.made_at)

        long_shot = freethrow.simulate(10, 1)
        self.assertEqual(long_shot.result, "long")
        self.assertIsNone(long_shot.made_at)

        sweet = freethrow.simulate(7, 1)
        self.assertEqual(sweet.result, "swish")
        self.assertIsNotNone(sweet.made_at)

    def test_path_starts_at_release_and_advances_in_time(self) -> None:
        shot = freethrow.simulate(7, 3)
        self.assertEqual(shot.path[0], (0.0, *freethrow.RELEASE))
        times = [t for t, _, _ in shot.path]
        self.assertEqual(times, sorted(times))
        self.assertAlmostEqual(shot.end, times[-1])


class SvgAndScoreboardTests(unittest.TestCase):
    def test_build_svg_valid_xml_idle_and_shot(self) -> None:
        shot = freethrow.simulate(7, 1)
        for theme in freethrow.THEMES.values():
            idle_svg = freethrow.build_svg(theme, None, "no shots yet, be the first")
            shot_svg = freethrow.build_svg(theme, shot, "zuromu took a 7")
            ET.fromstring(idle_svg)
            ET.fromstring(shot_svg)
            self.assertIn("no shots yet, be the first", idle_svg)
            self.assertIn("zuromu took a 7", shot_svg)

    def test_scoreboard_empty_singular_and_recent_five(self) -> None:
        empty = freethrow.scoreboard({"shots": []})
        self.assertIn("dist/shot-0-dark.svg", empty)
        self.assertIn("No shots yet, be the first.", empty)

        one = freethrow.scoreboard({
            "shots": [{"issue": 1, "shooter": "alice", "power": 7, "result": "swish", "at": "2026-10-25T00:00:00Z"}]
        })
        self.assertIn("1 made out of 1 shot so far, 1 of them a swish.", one)
        self.assertIn("| [alice](https://github.com/alice) | 7 | swish |", one)

        shots = [
            {"issue": i, "shooter": f"user{i}", "power": 7, "result": "swish" if i <= 2 else "front",
             "at": f"2026-10-25T00:0{i}:00Z"}
            for i in range(1, 7)
        ]
        many = freethrow.scoreboard({"shots": shots})
        self.assertIn("2 made out of 6 shots so far, 2 of them swishes.", many)
        self.assertIn("user6", many)
        self.assertIn("user2", many)
        self.assertNotIn("user1", many)
        self.assertLess(many.index("user6"), many.index("user2"))


class ShootFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data_file = root / "data" / "free-throws.json"
        self.dist_dir = root / "dist"
        self.readme = root / "README.md"
        self.reply_file = root / "reply.md"
        self.readme.write_text(
            "before\n<!-- freethrow:start -->\nold\n<!-- freethrow:end -->\nafter\n",
            encoding="utf-8",
        )
        self.patches = [
            mock.patch.object(freethrow, "DATA", self.data_file),
            mock.patch.object(freethrow, "DIST", self.dist_dir),
            mock.patch.object(freethrow, "README", self.readme),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def run_shoot(self, title: str, shooter: str, issue: int) -> str | None:
        if self.reply_file.exists():
            self.reply_file.unlink()
        env = {
            "TITLE": title,
            "SHOOTER": shooter,
            "ISSUE": str(issue),
            "REPLY_FILE": str(self.reply_file),
        }
        with mock.patch.dict(os.environ, env, clear=False), \
             contextlib.redirect_stdout(io.StringIO()):
            freethrow.shoot()
        if self.reply_file.exists():
            return self.reply_file.read_text(encoding="utf-8")
        return None

    def test_ignores_non_shot_issue_title(self) -> None:
        reply = self.run_shoot("shooting star idea", "alice", 1)
        self.assertIsNone(reply)
        self.assertFalse(self.data_file.exists())

    def test_rejects_bad_power_and_bad_shooter(self) -> None:
        for title, shooter in [("shoot 0", "alice"), ("shoot 11", "alice"), ("shoot nope", "alice"), ("shoot 7", "bad_name")]:
            reply = self.run_shoot(title, shooter, 1)
            self.assertIsNotNone(reply)
            self.assertIn("Couldn't read that shot", reply)
        self.assertFalse(self.data_file.exists())

    def test_valid_shot_cooldown_rerun_and_cleanup(self) -> None:
        reply = self.run_shoot("shoot 7", "alice", 1)
        self.assertIsNotNone(reply)
        self.assertIn("1 made out of 1", reply)
        self.assertTrue((self.dist_dir / "shot-1-dark.svg").exists())
        self.assertTrue((self.dist_dir / "shot-1-light.svg").exists())
        readme_text = self.readme.read_text(encoding="utf-8")
        self.assertTrue(readme_text.startswith("before\n"))
        self.assertTrue(readme_text.endswith("\nafter\n"))
        self.assertIn("alice", readme_text)

        # immediate second issue from same shooter hits cooldown
        cooldown_reply = self.run_shoot("shoot 7", "alice", 2)
        self.assertIn(f"{freethrow.COOLDOWN} seconds", cooldown_reply)
        self.assertEqual(len(json.loads(self.data_file.read_text())["shots"]), 1)

        # re-running the same issue does not duplicate or trigger cooldown
        rerun_reply = self.run_shoot("shoot 7", "alice", 1)
        self.assertIn("1 made out of 1", rerun_reply)
        self.assertEqual(len(json.loads(self.data_file.read_text())["shots"]), 1)

        # after cooldown expires, next shot succeeds and replaces old SVGs
        saved = json.loads(self.data_file.read_text())
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=freethrow.COOLDOWN + 5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        saved["shots"][0]["at"] = old_time
        self.data_file.write_text(json.dumps(saved), encoding="utf-8")

        second_reply = self.run_shoot("shoot 5", "alice", 2)
        self.assertIn("out of 2", second_reply)
        self.assertFalse((self.dist_dir / "shot-1-dark.svg").exists())
        self.assertTrue((self.dist_dir / "shot-2-dark.svg").exists())

    def test_update_readme_exits_when_markers_missing(self) -> None:
        self.readme.write_text("no markers here\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            freethrow.update_readme("block")


if __name__ == "__main__":
    unittest.main()
