"""Checks for the packaging deliverables: README, DEMO, WRITEUP, ADRs, CI workflow and LICENSE.

Standard library plus PyYAML. `make test` runs with `python3 -B`, so no .pyc files are written.
"""
import sys

sys.dont_write_bytecode = True

import pathlib
import re
import unittest

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
README = REPO / "README.md"
DEMO = REPO / "DEMO.md"
WRITEUP = REPO / "WRITEUP.md"
ADR_DIR = REPO / "docs"
CI = REPO / ".github" / "workflows" / "ci.yml"
LICENSE = REPO / "LICENSE"

ATTRIBUTION = "".join(["Gener", "ated with ", "Claude"])
CO_AUTHOR = "".join(["Co", "-Authored", "-By"])


def word_count(text):
    return len(re.findall(r"\S+", text))


class ReadmeTest(unittest.TestCase):
    def setUp(self):
        self.text = README.read_text(encoding="utf-8")

    def test_has_mermaid_architecture_diagram(self):
        self.assertIn("```mermaid", self.text)

    def test_has_ci_badge_placeholder(self):
        self.assertIn("workflows/ci.yml/badge.svg", self.text)

    def test_quick_start_uses_make_targets_that_exist(self):
        makefile = (REPO / "Makefile").read_text(encoding="utf-8")
        for target in ("make test", "make lint", "make validate", "make chaos-dry", "make cluster-up"):
            with self.subTest(command=target):
                self.assertIn(target, self.text)
                self.assertIn(target.split()[1] + ":", makefile)

    def test_states_docker_compose_is_not_applicable(self):
        self.assertIn("docker compose", self.text)

    def test_links_resolve(self):
        for rel in ("docs/RUNBOOK.md", "DEMO.md", "LICENSE", "docs/ADR-0003-rps-measured-at-traefik-edge.md"):
            with self.subTest(link=rel):
                self.assertTrue((REPO / rel).is_file(), rel)


class DemoTest(unittest.TestCase):
    def test_has_offline_and_full_cuts_with_timed_rows(self):
        text = DEMO.read_text(encoding="utf-8")
        self.assertIn("Offline cut", text)
        self.assertIn("Full cut", text)
        self.assertGreaterEqual(len(re.findall(r"^\| \d+:\d\d", text, re.M)), 8)

    def test_commands_in_demo_exist_as_make_targets_or_scripts(self):
        text = DEMO.read_text(encoding="utf-8")
        makefile = (REPO / "Makefile").read_text(encoding="utf-8")
        for target in ("make test", "make lint", "make validate", "make chaos-dry", "make load", "make down"):
            with self.subTest(command=target):
                self.assertIn(target, text)
                self.assertIn(target.split()[1] + ":", makefile)
        self.assertTrue((REPO / "scripts" / "chaos.sh").is_file())
        self.assertTrue((REPO / "scripts" / "load.sh").is_file())


class WriteupTest(unittest.TestCase):
    def test_word_count_is_about_400(self):
        count = word_count(WRITEUP.read_text(encoding="utf-8"))
        self.assertTrue(350 <= count <= 450, f"WRITEUP.md has {count} words, expected about 400")

    def test_has_required_sections(self):
        text = WRITEUP.read_text(encoding="utf-8")
        for heading in ("## Problem", "## Architecture", "## Key decisions", "## Results", "## What I would do next"):
            with self.subTest(heading=heading):
                self.assertIn(heading, text)


class AdrTest(unittest.TestCase):
    def test_at_least_three_adrs_with_status_and_decision(self):
        adrs = sorted(ADR_DIR.glob("ADR-*.md"))
        self.assertGreaterEqual(len(adrs), 3)
        for path in adrs:
            text = path.read_text(encoding="utf-8")
            with self.subTest(adr=path.name):
                self.assertRegex(text, r"(?m)^- \*\*Status:\*\* Accepted")
                self.assertIn("## Decision", text)
                self.assertIn("## Consequences", text)


class CiWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))

    def test_workflow_has_offline_and_chart_jobs(self):
        self.assertEqual(set(self.workflow["jobs"]), {"offline", "chart"})

    def test_chart_job_needs_offline_job(self):
        self.assertEqual(self.workflow["jobs"]["chart"].get("needs"), "offline")

    def test_ci_runs_tests_and_validator(self):
        text = CI.read_text(encoding="utf-8")
        self.assertIn("make test", text)
        self.assertIn("make validate", text)

    def test_ci_pins_helm_from_versions_env(self):
        text = CI.read_text(encoding="utf-8")
        self.assertIn("versions.env", text)
        versions = (REPO / "versions.env").read_text(encoding="utf-8")
        self.assertIn("HELM_VERSION=v3.22.0", versions)


class LicenseTest(unittest.TestCase):
    def test_mit_license_with_author(self):
        text = LICENSE.read_text(encoding="utf-8")
        self.assertIn("MIT License", text)
        self.assertIn("iboondk", text)


class NoAttributionTest(unittest.TestCase):
    def test_packaging_files_carry_no_ai_attribution(self):
        paths = [README, DEMO, WRITEUP, CI, *sorted(ADR_DIR.glob("ADR-*.md"))]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                self.assertNotIn(ATTRIBUTION, text)
                self.assertNotIn(CO_AUTHOR, text)


if __name__ == "__main__":
    unittest.main()
