"""Checks for the Argo CD app-of-apps, the platform values and the kind port mapping.

Uses PyYAML and the standard library only. `make test` runs with `python3 -B`, so no
.pyc files are written.
"""
import sys

sys.dont_write_bytecode = True

import importlib.util
import pathlib
import unittest

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("validate", REPO / "tools" / "validate.py")
validate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate)

APPS = REPO / "argocd" / "apps"
PLATFORM = REPO / "platform"
TENANTLY_VALUES = REPO / "charts" / "tenantly" / "values.yaml"
KIND_CONFIG = REPO / "kind" / "cluster.yaml"


def load_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def app_wave(name):
    doc = load_yaml(APPS / f"{name}.yaml")
    return int(doc["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"])


class ArgoAppsTest(unittest.TestCase):
    def test_argocd_apps_check_passes(self):
        results = [r for r in validate.check_argocd_apps(REPO) if r[0] == "argocd_apps"]
        self.assertEqual(len(results), 1)
        _, ok, detail = results[0]
        self.assertTrue(ok, detail)

    def test_wave_order(self):
        early = max(app_wave("sealed-secrets"), app_wave("metrics-server"))
        self.assertLess(early, app_wave("kube-prometheus-stack"))
        self.assertLess(app_wave("kube-prometheus-stack"), min(app_wave("traefik"), app_wave("prometheus-adapter")))
        self.assertLess(max(app_wave("traefik"), app_wave("prometheus-adapter")), app_wave("tenantly"))

    def test_sealed_secrets_and_metrics_server_share_the_earliest_wave(self):
        self.assertEqual(app_wave("sealed-secrets"), app_wave("metrics-server"))

    def test_root_app_points_at_argocd_apps(self):
        root = load_yaml(REPO / "argocd" / "root-app.yaml")
        self.assertEqual(root["metadata"]["name"], "kubestack-root")
        self.assertEqual(root["spec"]["source"]["path"], "argocd/apps")
        self.assertFalse(root["spec"]["source"]["directory"]["recurse"])
        self.assertEqual(root["spec"]["destination"]["namespace"], "argocd")

    def test_every_child_app_has_finalizer_and_retry(self):
        for path in sorted(APPS.glob("*.yaml")):
            doc = load_yaml(path)
            self.assertIn("resources-finalizer.argocd.argoproj.io", doc["metadata"]["finalizers"], path.name)
            retry = doc["spec"]["syncPolicy"]["retry"]
            self.assertEqual(retry["limit"], 5, path.name)
            self.assertIn("backoff", retry, path.name)


class PlatformValuesTest(unittest.TestCase):
    def test_adapter_external_rule_name_matches_hpa_metric(self):
        adapter = load_yaml(PLATFORM / "prometheus-adapter-values.yaml")
        rule_name = adapter["rules"]["external"][0]["name"]["as"]
        values = load_yaml(TENANTLY_VALUES)
        self.assertEqual(rule_name, values["hpa"]["rps"]["metricName"])

    def test_traefik_nodeport_is_mapped_by_kind(self):
        traefik = load_yaml(PLATFORM / "traefik-values.yaml")
        self.assertEqual(traefik["service"]["spec"]["type"], "NodePort")
        node_port = traefik["ports"]["web"]["nodePort"]
        self.assertEqual(node_port, 30080)
        kind = load_yaml(KIND_CONFIG)
        mapped = [
            mapping["containerPort"]
            for node in kind["nodes"]
            for mapping in node.get("extraPortMappings", [])
        ]
        self.assertIn(node_port, mapped)

    def test_traefik_metrics_service_is_enabled_for_servicemonitor(self):
        # The main traefik Service does not expose the metrics port, so the ServiceMonitor
        # needs the dedicated metrics Service. Without it Prometheus never scrapes
        # traefik_service_requests_total and the tenantly RPS HPA metric stays unknown.
        traefik = load_yaml(PLATFORM / "traefik-values.yaml")
        self.assertIs(traefik["metrics"]["prometheus"]["service"]["enabled"], True)
        self.assertIs(traefik["metrics"]["prometheus"]["serviceMonitor"]["enabled"], True)

    def test_grafana_sidecar_label_matches_chart_label(self):
        kps = load_yaml(PLATFORM / "kube-prometheus-stack-values.yaml")
        sidecar = kps["grafana"]["sidecar"]["dashboards"]
        values = load_yaml(TENANTLY_VALUES)
        self.assertEqual(sidecar["label"], values["dashboards"]["label"])
        self.assertEqual(str(sidecar["labelValue"]), str(values["dashboards"]["labelValue"]))


if __name__ == "__main__":
    unittest.main()
