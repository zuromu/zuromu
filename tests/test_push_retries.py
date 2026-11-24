"""Runs the workflow steps that push to main, with a fake git that cannot reach GitHub at first."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
STEPS = (("free-throw.yml", "Take the shot and push the scoreboard"), ("profile.yml", "Commit what changed"))

# writes down every call, and fails as many fetches and pulls as $FAILS says
GIT = """#!/bin/sh
echo "$1" >> calls
case "$1" in
  fetch|pull) test "$(grep -cE 'fetch|pull' calls)" -gt "$FAILS" ;;
  diff) exit 1 ;; # there is something to commit
esac
"""


def run_block(workflow: str, name: str) -> str:
    """The shell script of one step."""
    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    step = re.split(r"\n      - ", text.split(f"- name: {name}\n", 1)[1])[0]
    return textwrap.dedent(step.split("        run: |\n", 1)[1])


def run_step(workflow: str, name: str, fails: int) -> tuple[int, list[str]]:
    """Exit code of a step whose first fetches or pulls fail, and the git commands it ran."""
    with tempfile.TemporaryDirectory() as tmp:
        for command, script in (("git", GIT), ("python3", "#!/bin/sh\n"), ("sleep", "#!/bin/sh\n")):
            path = Path(tmp) / command
            path.write_text(script, encoding="utf-8")
            path.chmod(0o755)
        env = {**os.environ, "PATH": f"{tmp}{os.pathsep}{os.environ['PATH']}", "FAILS": str(fails), "ISSUE": "1"}
        # GitHub runs a step's script with bash -e
        result = subprocess.run(["bash", "-e", "-c", run_block(workflow, name)], cwd=tmp, env=env,
                                capture_output=True)
        return result.returncode, (Path(tmp) / "calls").read_text(encoding="utf-8").split()


class PushRetryTests(unittest.TestCase):
    def test_retries_when_github_cannot_be_reached_at_first(self) -> None:
        for workflow, name in STEPS:
            with self.subTest(workflow=workflow):
                code, calls = run_step(workflow, name, fails=2)
                self.assertEqual(code, 0)
                self.assertEqual(calls.count("push"), 1)

    def test_fails_without_pushing_when_github_stays_unreachable(self) -> None:
        for workflow, name in STEPS:
            with self.subTest(workflow=workflow):
                code, calls = run_step(workflow, name, fails=99)
                self.assertNotEqual(code, 0)
                self.assertNotIn("push", calls)


if __name__ == "__main__":
    unittest.main()
