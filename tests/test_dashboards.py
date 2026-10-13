"""Tests for the Grafana dashboard JSON models shipped in the tenantly chart.

Uses only the standard library and tools/validate.py. `make test` runs with `python3 -B`, so no .pyc files are written. Running a module
directly without -B still writes bytecode for the test module itself.
"""
import sys

sys.dont_write_bytecode = True

import json
import pathlib
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import validate  # noqa: E402

DASHBOARDS = REPO / "charts" / "tenantly" / "dashboards"
OVERVIEW = DASHBOARDS / "tenantly-overview.json"
AUTOSCALING = DASHBOARDS / "tenantly-autoscaling.json"
DS = {"type": "prometheus", "uid": "${datasource}"}


def _minimal_dashboard():
    """A dashboard that passes every schema rule. Tests break one rule at a time."""
    return {
        "uid": "kubestack-test",
        "title": "Test",
        "panels": [
            {
                "id": 1,
                "type": "timeseries",
                "datasource": DS,
                "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
                "targets": [{"datasource": DS, "expr": "up", "refId": "A"}],
            },
            {
                "id": 2,
                "type": "stat",
                "datasource": DS,
                "gridPos": {"h": 4, "w": 6, "x": 12, "y": 0},
                "targets": [{"datasource": DS, "expr": "sum(up)", "refId": "A"}],
            },
        ],
    }


class DashboardSchemaRuleTest(unittest.TestCase):
    def test_valid_dashboard_has_no_problems(self):
        self.assertEqual(validate._dashboard_problems(_minimal_dashboard()), [])

    def test_missing_uid_is_reported(self):
        doc = _minimal_dashboard()
        del doc["uid"]
        self.assertIn("missing uid", validate._dashboard_problems(doc))

    def test_missing_title_is_reported(self):
        doc = _minimal_dashboard()
        doc["title"] = ""
        self.assertIn("missing title", validate._dashboard_problems(doc))

    def test_empty_panels_are_reported(self):
        doc = _minimal_dashboard()
        doc["panels"] = []
        self.assertIn("panels missing or empty", validate._dashboard_problems(doc))

    def test_duplicate_panel_ids_are_reported(self):
        doc = _minimal_dashboard()
        doc["panels"][1]["id"] = 1
        problems = validate._dashboard_problems(doc)
        self.assertTrue(any("duplicate panel id" in p for p in problems), problems)

    def test_empty_expr_is_reported(self):
        doc = _minimal_dashboard()
        doc["panels"][0]["targets"][0]["expr"] = "   "
        problems = validate._dashboard_problems(doc)
        self.assertTrue(any("empty expr" in p for p in problems), problems)

    def test_missing_expr_is_reported(self):
        doc = _minimal_dashboard()
        del doc["panels"][0]["targets"][0]["expr"]
        problems = validate._dashboard_problems(doc)
        self.assertTrue(any("empty expr" in p for p in problems), problems)

    def test_wrong_datasource_uid_is_reported(self):
        doc = _minimal_dashboard()
        doc["panels"][0]["datasource"] = {"type": "prometheus", "uid": "prom-hardcoded"}
        problems = validate._dashboard_problems(doc)
        self.assertTrue(any("not '${datasource}'" in p for p in problems), problems)

    def test_run_checks_fails_for_broken_dashboard_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            base = repo / "charts" / "tenantly" / "dashboards"
            base.mkdir(parents=True)
            broken = _minimal_dashboard()
            broken["panels"][0]["targets"][0]["expr"] = ""
            (base / "broken.json").write_text(json.dumps(broken), encoding="utf-8")

            results = validate.check_dashboards_schema(repo)

            self.assertEqual(len(results), 1)
            name, ok, detail = results[0]
            self.assertIn("broken.json", name)
            self.assertFalse(ok)
            self.assertIn("empty expr", detail)

    def test_run_checks_passes_for_valid_dashboard_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            base = repo / "charts" / "tenantly" / "dashboards"
            base.mkdir(parents=True)
            (base / "good.json").write_text(json.dumps(_minimal_dashboard()), encoding="utf-8")

            results = validate.check_dashboards_schema(repo)

            self.assertEqual(len(results), 1)
            self.assertTrue(results[0][1])


class ShippedDashboardsTest(unittest.TestCase):
    def load(self, path):
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)

    def all_exprs(self, doc):
        return [t.get("expr", "") for p in doc.get("panels", []) for t in p.get("targets", [])]

    def test_shipped_dashboards_pass_schema(self):
        for path in (OVERVIEW, AUTOSCALING):
            with self.subTest(file=path.name):
                self.assertEqual(validate._dashboard_problems(self.load(path)), [])

    def test_uids_and_common_fields(self):
        expected = {OVERVIEW: "kubestack-overview", AUTOSCALING: "kubestack-autoscaling"}
        for path, uid in expected.items():
            with self.subTest(file=path.name):
                doc = self.load(path)
                self.assertEqual(doc["uid"], uid)
                self.assertEqual(doc["schemaVersion"], 39)
                self.assertEqual(doc["tags"], ["kubestack"])
                self.assertEqual(doc["refresh"], "10s")
                self.assertEqual(doc["time"]["from"], "now-30m")

    def test_template_variables(self):
        for path in (OVERVIEW, AUTOSCALING):
            with self.subTest(file=path.name):
                variables = {v["name"]: v for v in self.load(path)["templating"]["list"]}
                self.assertEqual(variables["datasource"]["type"], "datasource")
                self.assertEqual(variables["datasource"]["query"], "prometheus")
                self.assertEqual(variables["namespace"]["query"], "label_values(kube_pod_info, namespace)")
                self.assertEqual(variables["namespace"]["current"]["value"], "tenantly")

    def test_overview_contains_traefik_requests_expr(self):
        exprs = self.all_exprs(self.load(OVERVIEW))
        self.assertTrue(
            any("traefik_service_requests_total" in e for e in exprs),
            "overview has no traefik_service_requests_total expr",
        )

    def test_autoscaling_contains_hpa_current_replicas_expr(self):
        exprs = self.all_exprs(self.load(AUTOSCALING))
        self.assertTrue(
            any("kube_horizontalpodautoscaler_status_current_replicas" in e for e in exprs),
            "autoscaling dashboard has no kube_horizontalpodautoscaler_status_current_replicas expr",
        )


if __name__ == "__main__":
    unittest.main()
