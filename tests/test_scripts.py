"""Checks for the demo scripts and the runbook.

The chaos dry run must work with no kubectl, curl or cluster. The tests therefore run it with
PATH set to /usr/bin:/bin only. Standard library only. `make test` runs with `python3 -B`, so no
.pyc files are written.
"""
import sys

sys.dont_write_bytecode = True

import pathlib
import shutil
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
CHAOS = SCRIPTS / "chaos.sh"
RUNBOOK = REPO / "docs" / "RUNBOOK.md"
PROOF_CHAOS = REPO / "proof" / "chaos"

# Resolved once, before any test changes PATH. Tests always call bash by this absolute path.
BASH = shutil.which("bash") or "/bin/bash"
LIMITED_ENV = {"PATH": "/usr/bin:/bin"}


def _run(args, env=None):
    return subprocess.run(
        [BASH, *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


class ChaosDryRunTests(unittest.TestCase):
    def test_dry_run_all_exits_zero_with_no_kubectl_on_path(self):
        before = sorted(p.name for p in PROOF_CHAOS.iterdir()) if PROOF_CHAOS.is_dir() else []
        result = _run([str(CHAOS), "--dry-run", "--experiment", "all"], env=LIMITED_ENV)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        output = result.stdout + result.stderr
        self.assertIn("DRY-RUN: kubectl delete pod", output)
        self.assertIn("DRY-RUN: kubectl drain", output)
        self.assertIn("DRY-RUN: kubectl uncordon", output)
        after = sorted(p.name for p in PROOF_CHAOS.iterdir()) if PROOF_CHAOS.is_dir() else []
        self.assertEqual(before, after, msg="dry-run must not write files under proof/chaos")

    def test_dry_run_without_experiment_flag_exits_zero(self):
        result = _run([str(CHAOS), "--dry-run"], env=LIMITED_ENV)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("DRY-RUN:", result.stdout)

    def test_unknown_experiment_exits_nonzero_with_usage(self):
        result = _run([str(CHAOS), "--experiment", "bogus", "--dry-run"], env=LIMITED_ENV)
        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn("Usage", combined)
        self.assertIn("bogus", combined)


class ShellSyntaxTests(unittest.TestCase):
    def test_bash_syntax_is_valid_for_every_script(self):
        scripts = sorted(SCRIPTS.glob("*.sh"))
        self.assertTrue(scripts, msg="no shell scripts found under scripts/")
        for script in scripts:
            with self.subTest(script=script.name):
                result = _run(["-n", str(script)])
                self.assertEqual(result.returncode, 0, msg=result.stderr)


class RunbookTests(unittest.TestCase):
    def setUp(self):
        self.text = RUNBOOK.read_text(encoding="utf-8")

    def test_runbook_mentions_bootstrap_chaos_and_grafana_port(self):
        for needle in ("make bootstrap", "chaos.sh", "30300"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.text)


if __name__ == "__main__":
    unittest.main()
