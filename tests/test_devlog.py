from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import devlog


class DevlogHelperTests(unittest.TestCase):
    def test_title_of_heading_and_fallback(self) -> None:
        self.assertEqual(
            devlog.title_of("## Subheading\n# Real Title\nbody", "m1.md"),
            "Real Title",
        )
        self.assertEqual(
            devlog.title_of("no heading here", "m1-physics-sandbox.md"),
            "m1 physics sandbox",
        )

    def test_nice_date_converts_to_jakarta(self) -> None:
        self.assertEqual(devlog.nice_date(None), "")
        # 20:00 UTC on Oct 24 is 03:00 WIB on Oct 25
        self.assertEqual(devlog.nice_date("2026-10-24T20:00:00Z"), ", Oct 25, 2026")

    def test_render_empty_order_and_bracket_escaping(self) -> None:
        self.assertEqual(devlog.render([]), "- nothing yet")
        posts = [
            {"title": "Older", "url": "https://example.com/1", "repo": "swish-rl", "date": "2026-10-25T10:00:00Z"},
            {"title": "M2 [draft]", "url": "https://example.com/2", "repo": "swish-rl", "date": "2026-11-20T10:00:00Z"},
        ]
        rendered = devlog.render(posts)
        self.assertEqual(
            rendered,
            "- [M2 (draft)](https://example.com/2) in swish-rl, Nov 20, 2026\n"
            "- [Older](https://example.com/1) in swish-rl, Oct 25, 2026",
        )


class DevlogIoTests(unittest.TestCase):
    def test_update_readme_replaces_block_and_detects_no_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text(
                "top\n<!-- devlog:start -->\nold\n<!-- devlog:end -->\nbottom\n",
                encoding="utf-8",
            )
            with mock.patch.object(devlog, "README", readme):
                self.assertTrue(devlog.update_readme("- new"))
                self.assertEqual(
                    readme.read_text(encoding="utf-8"),
                    "top\n<!-- devlog:start -->\n- new\n<!-- devlog:end -->\nbottom\n",
                )
                self.assertFalse(devlog.update_readme("- new"))

    def test_update_readme_exits_without_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text("no markers\n", encoding="utf-8")
            with mock.patch.object(devlog, "README", readme):
                with self.assertRaises(SystemExit):
                    devlog.update_readme("- new")

    def test_fetch_posts_filters_entries_and_picks_earliest_commit(self) -> None:
        responses = [
            {
                "repository": {
                    "url": "https://github.com/zuromu/swish-rl",
                    "defaultBranchRef": {"name": "main"},
                    "object": {
                        "entries": [
                            {"name": "notes.txt", "object": {"text": "skip"}},
                            {"name": "images.md", "object": {}},
                            {"name": "m1.md", "object": {"text": "# M1: Physics\n"}},
                        ]
                    },
                }
            },
            {
                "repository": {
                    "defaultBranchRef": {
                        "target": {
                            "history": {
                                "nodes": [
                                    {"committedDate": "2026-10-27T10:00:00Z"},
                                    {"committedDate": "2026-10-25T10:00:00Z"},
                                ]
                            }
                        }
                    }
                }
            },
        ]

        with mock.patch.object(devlog, "graphql", side_effect=responses):
            posts = devlog.fetch_posts("tok")

        self.assertEqual(posts, [{
            "title": "M1: Physics",
            "url": "https://github.com/zuromu/swish-rl/blob/main/docs/devlog/m1.md",
            "repo": "swish-rl",
            "date": "2026-10-25T10:00:00Z",
        }])

    def test_fetch_posts_skips_private_or_missing_repo(self) -> None:
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
                "data": {"repository": None},
                "errors": [{"type": "NOT_FOUND", "path": ["repository"], "message": "Could not resolve"}],
            },
            {
                "data": {
                    "repository": {
                        "url": "https://github.com/zuromu/swish-rl",
                        "defaultBranchRef": {"name": "main"},
                        "object": {
                            "entries": [
                                {"name": "m1.md", "object": {"text": "# M1: Physics\n"}},
                            ]
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
                                    "nodes": [{"committedDate": "2026-10-25T10:00:00Z"}]
                                }
                            }
                        }
                    }
                }
            },
        ]

        sources = [("private-repo", "docs/devlog"), ("swish-rl", "docs/devlog")]
        with mock.patch.object(devlog, "SOURCES", sources), \
             mock.patch("urllib.request.urlopen", side_effect=[FakeResponse(p) for p in payloads]):
            posts = devlog.fetch_posts("tok")

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["repo"], "swish-rl")
        self.assertEqual(posts[0]["title"], "M1: Physics")


if __name__ == "__main__":
    unittest.main()
