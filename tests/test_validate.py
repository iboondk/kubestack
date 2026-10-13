"""Unit tests for tools/validate.py.

`make test` runs with `python3 -B`, so no .pyc files are written. Running a module
directly without -B still writes bytecode for the test module itself.
"""
import sys

sys.dont_write_bytecode = True

import pathlib
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import validate  # noqa: E402


CLAUDE_TAG = "".join(["Gener", "ated with ", "Claude"])
# Built with join so this file does not contain the code the check searches for.
INTERNAL_CODE = "".join(["p", "3-", "tenantly"])
NUMBERED_LABEL = "".join(["P", "3"])


def _result(results, name):
    matches = [r for r in results if r[0] == name]
    if not matches:
        raise AssertionError(f"no result named {name!r} in {[r[0] for r in results]}")
    return matches[0]


class ValidateChecksTest(unittest.TestCase):
    def test_bad_yaml_fails_check_a(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / "kind").mkdir()
            (repo / "kind" / "bad.yaml").write_text("a: [unclosed\nb: : :\n", encoding="utf-8")
            (repo / "kind" / "good.yaml").write_text("a: 1\n", encoding="utf-8")

            results = validate.run_checks(repo)

            _, ok, detail = _result(results, "yaml parses kind/bad.yaml")
            self.assertFalse(ok)
            self.assertTrue(detail)
            _, good_ok, _ = _result(results, "yaml parses kind/good.yaml")
            self.assertTrue(good_ok)

    def test_yaml_under_templates_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / "charts" / "tenantly" / "templates").mkdir(parents=True)
            (repo / "charts" / "tenantly" / "templates" / "deployment.yaml").write_text(
                "{{ include \"x\" . }}: [\n", encoding="utf-8"
            )

            results = validate.run_checks(repo)

            self.assertFalse(any("templates" in r[0] for r in results))

    def test_non_placeholder_env_example_fails_check_e(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / ".env.example").write_text(
                "POSTGRES_PASSWORD=change-me-postgres\nJWT_SECRET=s3cr3t-real-value\n",
                encoding="utf-8",
            )

            results = validate.run_checks(repo)

            _, ok, detail = _result(results, ".env.example placeholders")
            self.assertFalse(ok)
            self.assertIn("JWT_SECRET", detail)

    def test_placeholder_env_example_passes_check_e(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / ".env.example").write_text(
                "JWT_SECRET=change-me-jwt\n", encoding="utf-8"
            )

            results = validate.run_checks(repo)

            _, ok, _ = _result(results, ".env.example placeholders")
            self.assertTrue(ok)

    def test_missing_versions_key_fails_check_d(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / "versions.env").write_text("KIND_VERSION=v0.33.0\n", encoding="utf-8")

            results = validate.run_checks(repo)

            _, ok, detail = _result(results, "versions.env keys")
            self.assertFalse(ok)
            self.assertIn("HELM_VERSION", detail)

    def test_latest_tag_in_versions_env_fails_pin_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / "versions.env").write_text(
                "HEY_IMAGE=williamyeh/hey:latest\n", encoding="utf-8"
            )

            results = validate.run_checks(repo)

            _, ok, detail = _result(results, "versions.env pins")
            self.assertFalse(ok)
            self.assertIn("HEY_IMAGE", detail)

    def test_untagged_image_in_versions_env_fails_pin_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / "versions.env").write_text(
                "HEY_IMAGE=williamyeh/hey\n", encoding="utf-8"
            )

            results = validate.run_checks(repo)

            _, ok, _ = _result(results, "versions.env pins")
            self.assertFalse(ok)

    def test_digest_pinned_image_passes_pin_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / "versions.env").write_text(
                "HEY_IMAGE=williamyeh/hey@sha256:" + "a" * 64 + "\n"
                "REDIS_IMAGE=redis:7-alpine\n",
                encoding="utf-8",
            )

            results = validate.run_checks(repo)

            _, ok, detail = _result(results, "versions.env pins")
            self.assertTrue(ok, msg=detail)

    def test_attribution_string_fails_check_f(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / "README.md").write_text(
                CLAUDE_TAG + "\n", encoding="utf-8"
            )
            (repo / ".tools").mkdir()
            (repo / ".tools" / "ignored.txt").write_text(
                CLAUDE_TAG + "\n", encoding="utf-8"
            )

            results = validate.run_checks(repo)

            _, ok, detail = _result(results, "no AI attribution")
            self.assertFalse(ok)
            self.assertIn("README.md", detail)
            self.assertNotIn(".tools", detail)

    def test_internal_project_code_in_tracked_file_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / "docs").mkdir()
            (repo / "docs" / "RUNBOOK.md").write_text(
                "Source lives in the " + INTERNAL_CODE + " repo.\n", encoding="utf-8"
            )

            results = validate.run_checks(repo)

            _, ok, detail = _result(results, "no internal project codes")
            self.assertFalse(ok)
            self.assertIn("docs/RUNBOOK.md", detail)

    def test_internal_codes_in_gitignored_file_are_not_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / ".gitignore").write_text("PLAN.md\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# " + INTERNAL_CODE + ": Plan\n", encoding="utf-8")
            (repo / ".tools").mkdir()
            (repo / ".tools" / "notes.txt").write_text(INTERNAL_CODE + "\n", encoding="utf-8")

            results = validate.run_checks(repo)

            _, ok, detail = _result(results, "no internal project codes")
            self.assertTrue(ok, msg=detail)

    def test_numbered_project_label_fails_internal_code_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / "README.md").write_text("Part of the " + NUMBERED_LABEL + " series.\n", encoding="utf-8")

            results = validate.run_checks(repo)

            _, ok, detail = _result(results, "no internal project codes")
            self.assertFalse(ok)
            self.assertIn("README.md", detail)

    def test_real_repo_passes(self):
        results = validate.run_checks(REPO)
        failures = [r for r in results if not r[1]]
        self.assertEqual(failures, [], f"real repo has failing checks: {failures}")


if __name__ == "__main__":
    unittest.main()
