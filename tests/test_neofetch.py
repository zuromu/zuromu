from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import neofetch


class TextHelperTests(unittest.TestCase):
    def test_uptime_birthday_and_month_borrow(self) -> None:
        self.assertEqual(
            neofetch.uptime(date(2026, 8, 23)),
            "19 years, 0 months, 0 days (birthday!)",
        )
        self.assertEqual(
            neofetch.uptime(date(2026, 8, 22)),
            "18 years, 11 months, 30 days",
        )
        # March 1 borrows February's length (29 in leap year 2024, 28 in 2025)
        self.assertEqual(neofetch.uptime(date(2024, 3, 1)), "16 years, 6 months, 7 days")
        self.assertEqual(neofetch.uptime(date(2025, 3, 1)), "17 years, 6 months, 6 days")

        with mock.patch.object(neofetch, "SHOW_FULL_UPTIME", False):
            self.assertEqual(neofetch.uptime(date(2026, 8, 23)), "19 years")

    def test_semester_clamps_to_valid_range(self) -> None:
        self.assertEqual(neofetch.semester(date(2025, 1, 1)), "Computer Science, semester 1")
        self.assertEqual(neofetch.semester(date(2025, 8, 1)), "Computer Science, semester 1")
        self.assertEqual(neofetch.semester(date(2026, 2, 1)), "Computer Science, semester 2")
        self.assertEqual(neofetch.semester(date(2035, 8, 1)), "Computer Science, semester 8")

    def test_leader_and_section_widths(self) -> None:
        pieces = neofetch.leader("Shell", "zsh", neofetch.LINE_CHARS)
        rendered = "".join(text for text, _ in pieces)
        self.assertEqual(len(rendered), neofetch.LINE_CHARS)
        self.assertTrue(rendered.startswith("Shell: "))
        self.assertTrue(rendered.endswith(" zsh"))

        with self.assertRaises(ValueError):
            neofetch.leader("Key", "x" * 20, 20)

        header = "".join(text for text, _ in neofetch.section("GitHub Stats"))
        self.assertEqual(len(header), neofetch.LINE_CHARS)

    def test_last_push_text_converts_to_jakarta_time(self) -> None:
        self.assertEqual(neofetch.last_push_text(None), "-")
        self.assertEqual(neofetch.last_push_text({"name": "empty", "pushedAt": None}), "-")
        # 19:26 UTC on Oct 24 is 02:26 WIB on Oct 25
        push = {"name": "swish-rl", "pushedAt": "2026-10-24T19:26:55Z"}
        self.assertEqual(neofetch.last_push_text(push), "swish-rl, Oct 25")

    def test_last_push_text_truncates_long_repo_names(self) -> None:
        exact_fit = {"name": "a" * 36, "pushedAt": "2026-11-20T00:00:00Z"}
        self.assertEqual(neofetch.last_push_text(exact_fit), f"{'a' * 36}, Nov 20")

        too_long = {"name": "platform-based-development-assignment", "pushedAt": "2026-11-20T00:00:00Z"}
        text = neofetch.last_push_text(too_long)
        self.assertTrue(text.endswith("..., Nov 20"))
        rendered = "".join(t for t, _ in neofetch.leader("Last Push", text, neofetch.LINE_CHARS))
        self.assertEqual(len(rendered), neofetch.LINE_CHARS)

        lines = neofetch.card_lines({**neofetch.DEMO_STATS, "last_push": too_long}, date(2026, 11, 21))
        last_push_line = next(
            "".join(t for t, _ in line) for line in lines
            if isinstance(line, list) and line and line[0][0] == "Last Push"
        )
        self.assertEqual(len(last_push_line), neofetch.LINE_CHARS)


class CardAndSvgTests(unittest.TestCase):
    def test_card_lines_placeholder_and_demo(self) -> None:
        placeholder = neofetch.card_lines(None, date(2026, 11, 21))
        self.assertEqual(placeholder[-1], "palette")
        self.assertTrue(any(
            "".join(t for t, _ in line).startswith("Repos:") and "-" in "".join(t for t, _ in line)
            for line in placeholder if isinstance(line, list)
        ))

        demo = neofetch.card_lines(neofetch.DEMO_STATS, date(2026, 11, 21))
        loc_line = next(
            line for line in demo
            if isinstance(line, list) and line and line[0][0] == "Lines of Code"
        )
        classes = [cls for _, cls in loc_line]
        self.assertIn("add", classes)
        self.assertIn("del", classes)

    def test_donut_frame_and_build_svg_valid_xml(self) -> None:
        frame = neofetch.donut_frame(0.5, 0.25)
        self.assertEqual(len(frame), neofetch.DONUT_ROWS)
        self.assertTrue(any(row.strip() for row in frame))

        lines = neofetch.card_lines(neofetch.DEMO_STATS, date(2026, 11, 21))
        frames = [frame, neofetch.donut_frame(1.0, 0.5)]
        for theme in neofetch.THEMES.values():
            svg = neofetch.build_svg(theme, frames, lines, "2026-11-21 04:17 WIB")
            ET.fromstring(svg)
            self.assertIn("<!-- last drawn 2026-11-21 04:17 WIB -->", svg)


class FetchStatsTests(unittest.TestCase):
    def test_fetch_stats_skips_unpushed_recent_repo(self) -> None:
        response = {
            "user": {
                "id": "U_1",
                "followers": {"totalCount": 1},
                "contributionsCollection": {"contributionCalendar": {"totalContributions": 10}},
                "recent": {"nodes": [
                    {"name": "zuromu", "pushedAt": "2026-11-20T00:00:00Z"},
                    {"name": "brand-new-empty", "pushedAt": None},
                    {"name": "dropdone", "pushedAt": "2026-11-19T12:00:00Z"},
                ]},
                "repositories": {
                    "totalCount": 0,
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [],
                },
                "repositoriesContributedTo": {
                    "totalCount": 0,
                    "nodes": [],
                },
            }
        }
        with mock.patch.object(neofetch, "graphql", return_value=response):
            stats = neofetch.fetch_stats("tok")
        self.assertEqual(stats["last_push"]["name"], "dropdone")

    def test_fetch_stats_paginates_skips_merges_and_profile_repo(self) -> None:
        responses = [
            {
                "user": {
                    "id": "U_1",
                    "followers": {"totalCount": 6},
                    "contributionsCollection": {"contributionCalendar": {"totalContributions": 120}},
                    "recent": {"nodes": [
                        {"name": "zuromu", "pushedAt": "2026-11-20T00:00:00Z"},
                        {"name": "dropdone", "pushedAt": "2026-11-19T12:00:00Z"},
                    ]},
                    "repositories": {
                        "totalCount": 2,
                        "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                        "nodes": [{"nameWithOwner": "zuromu/dropdone", "stargazerCount": 3}],
                    },
                    "repositoriesContributedTo": {
                        "totalCount": 1,
                        "nodes": [{"nameWithOwner": "friend/app"}],
                    },
                }
            },
            {
                "user": {
                    "id": "U_1",
                    "followers": {"totalCount": 6},
                    "contributionsCollection": {"contributionCalendar": {"totalContributions": 120}},
                    "recent": {"nodes": [
                        {"name": "zuromu", "pushedAt": "2026-11-20T00:00:00Z"},
                        {"name": "dropdone", "pushedAt": "2026-11-19T12:00:00Z"},
                    ]},
                    "repositories": {
                        "totalCount": 2,
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [{"nameWithOwner": "zuromu/empty", "stargazerCount": 1}],
                    },
                    "repositoriesContributedTo": {
                        "totalCount": 1,
                        "nodes": [{"nameWithOwner": "friend/app"}],
                    },
                }
            },
            # history for zuromu/dropdone: 1 normal commit + 1 merge commit (skipped)
            {
                "repository": {
                    "defaultBranchRef": {
                        "target": {
                            "history": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [
                                    {"additions": 100, "deletions": 20, "parents": {"totalCount": 1}},
                                    {"additions": 999, "deletions": 999, "parents": {"totalCount": 2}},
                                ],
                            }
                        }
                    }
                }
            },
            # history for zuromu/empty: no default branch
            {"repository": {"defaultBranchRef": None}},
            # history for friend/app
            {
                "repository": {
                    "defaultBranchRef": {
                        "target": {
                            "history": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [
                                    {"additions": 50, "deletions": 5, "parents": {"totalCount": 1}},
                                ],
                            }
                        }
                    }
                }
            },
        ]

        with mock.patch.object(neofetch, "graphql", side_effect=responses):
            stats = neofetch.fetch_stats("tok")

        self.assertEqual(stats["repos"], 2)
        self.assertEqual(stats["contributed"], 1)
        self.assertEqual(stats["stars"], 4)
        self.assertEqual(stats["commits"], 2)
        self.assertEqual(stats["additions"], 150)
        self.assertEqual(stats["deletions"], 25)
        self.assertEqual(stats["last_push"]["name"], "dropdone")

    def test_fetch_stats_skips_not_found_repo_history(self) -> None:
        class FakeResponse:
            def __init__(self, payload: dict) -> None:
                self.raw = io.BytesIO(json.dumps(payload).encode())

            def read(self) -> bytes:
                return self.raw.read()

            def __enter__(self) -> FakeResponse:
                return self

            def __exit__(self, *args) -> None:
                return None

        payloads = [
            {
                "data": {
                    "user": {
                        "id": "U_1",
                        "followers": {"totalCount": 3},
                        "contributionsCollection": {"contributionCalendar": {"totalContributions": 50}},
                        "recent": {"nodes": [{"name": "dropdone", "pushedAt": "2026-11-19T12:00:00Z"}]},
                        "repositories": {
                            "totalCount": 1,
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [{"nameWithOwner": "zuromu/dropdone", "stargazerCount": 2}],
                        },
                        "repositoriesContributedTo": {
                            "totalCount": 1,
                            "nodes": [{"nameWithOwner": "friend/gone-repo"}],
                        },
                    }
                }
            },
            {
                "data": {
                    "repository": {
                        "defaultBranchRef": {
                            "target": {
                                "history": {
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    "nodes": [
                                        {"additions": 40, "deletions": 10, "parents": {"totalCount": 1}},
                                    ],
                                }
                            }
                        }
                    }
                }
            },
            {
                "data": {"repository": None},
                "errors": [{"type": "NOT_FOUND", "path": ["repository"], "message": "Could not resolve"}],
            },
        ]

        with mock.patch("urllib.request.urlopen", side_effect=[FakeResponse(p) for p in payloads]):
            stats = neofetch.fetch_stats("tok")

        self.assertEqual(stats["commits"], 1)
        self.assertEqual(stats["additions"], 40)
        self.assertEqual(stats["deletions"], 10)


class MainTests(unittest.TestCase):
    def run_main(self, argv: list[str], **env: str) -> str:
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(sys, "argv", ["neofetch.py", *argv]), \
             mock.patch.object(neofetch, "FRAMES", 2), \
             contextlib.redirect_stdout(stdout):
            neofetch.main()
        return stdout.getvalue()

    def test_demo_and_placeholder_write_both_themes(self) -> None:
        for flag, expected in (("--demo", "(274 commits, 4 stars)"), ("--placeholder", "a placeholder card")):
            with tempfile.TemporaryDirectory() as tmp:
                out = self.run_main([flag, "--out", str(Path(tmp) / "out")])
                self.assertIn(expected, out)
                for name in neofetch.THEMES:
                    svg = (Path(tmp) / "out" / f"neofetch-{name}.svg").read_text(encoding="utf-8")
                    ET.fromstring(svg)

    def test_requires_token_and_fetches_when_present(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(sys, "argv", ["neofetch.py"]):
            with self.assertRaises(SystemExit) as ctx:
                neofetch.main()
        self.assertIn("GITHUB_TOKEN", str(ctx.exception))

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(neofetch, "fetch_stats", return_value=neofetch.DEMO_STATS) as fetch_mock:
            out = self.run_main(["--out", tmp], GITHUB_TOKEN="tok")
            self.assertTrue((Path(tmp) / "neofetch-dark.svg").exists())
        fetch_mock.assert_called_once_with("tok")
        self.assertIn("drew the card into", out)


if __name__ == "__main__":
    unittest.main()
