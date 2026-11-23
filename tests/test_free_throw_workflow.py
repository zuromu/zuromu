"""Checks when .github/workflows/free-throw.yml says a shot did not go through."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "free-throw.yml"


def step(name: str) -> str:
    """The text of one step, from its name up to the next step."""
    text = WORKFLOW.read_text(encoding="utf-8")
    return re.split(r"\n      - ", text.split(f"- name: {name}\n", 1)[1])[0]


def failure_comment_runs(title: str, shot_outcome: str) -> bool:
    """Work out the failure step's condition for a job where some step failed."""
    condition = re.search(r"^        if: (.+)$", step("Say so if the shot did not go through"), re.MULTILINE)[1]
    # GitHub compares strings without looking at case
    for name, value in (("github.event.issue.title", title.lower()), ("steps.shot.outcome", shot_outcome)):
        condition = condition.replace(name, repr(value))
    condition = condition.replace("&&", "and").replace("||", "or")
    return bool(eval(condition, {"failure": lambda: True, "startsWith": str.startswith}))


class FailureCommentTests(unittest.TestCase):
    def test_shot_step_has_the_id_the_condition_uses(self) -> None:
        self.assertIn("        id: shot\n", step("Take the shot and push the scoreboard"))

    def test_comments_when_a_shot_failed_or_never_started(self) -> None:
        for title in ("shoot 7", "Shoot 7", "shoot", "shoot 99"):
            for outcome in ("failure", "skipped"):  # skipped: checkout or python setup failed first
                with self.subTest(title=title, outcome=outcome):
                    self.assertTrue(failure_comment_runs(title, outcome))

    def test_leaves_issues_that_are_not_shots_alone(self) -> None:
        for title in ("shooting star idea", "shootout mode"):
            for outcome in ("failure", "skipped"):
                with self.subTest(title=title, outcome=outcome):
                    self.assertFalse(failure_comment_runs(title, outcome))

    def test_does_not_deny_a_shot_that_was_counted(self) -> None:
        # the shot was pushed and only the reply failed
        self.assertFalse(failure_comment_runs("shoot 7", "success"))


if __name__ == "__main__":
    unittest.main()
