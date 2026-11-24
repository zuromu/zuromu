"""Checks that actions from outside GitHub are pinned to an exact commit."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


class PinnedActionTests(unittest.TestCase):
    def test_outside_actions_use_a_full_commit_sha(self) -> None:
        actions = [(path.name, action) for path in sorted(WORKFLOWS.glob("*.yml"))
                   for action in re.findall(r"^ +(?:- )?uses: (\S+)", path.read_text(encoding="utf-8"), re.MULTILINE)]
        self.assertTrue(actions)
        for workflow, action in actions:
            if action.startswith("actions/"):  # GitHub's own
                continue
            with self.subTest(workflow=workflow, action=action):
                self.assertRegex(action, r"@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
