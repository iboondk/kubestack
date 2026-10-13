"""Static checks for the tenantly Helm chart.

Uses PyYAML only. The helm render tests run when .tools/bin/helm exists and are
skipped otherwise. `make test` runs with `python3 -B`, so no .pyc files are written. Running a module
directly without -B still writes bytecode for the test module itself.
"""
import sys

sys.dont_write_bytecode = True

import os
import pathlib
import subprocess
import unittest

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
CHART = REPO / "charts" / "tenantly"
TEMPLATES = CHART / "templates"
HELM = REPO / ".tools" / "bin" / "helm"
VALUES_FILES = sorted(CHART.glob("values*.yaml"))
# helm defaults to Kubernetes v1.20.0, which fails the chart's kubeVersion (>=1.28.0-0).
# Render against the kind node version pinned in versions.env instead.
KUBE_VERSION = "1.36.0"


def load_versions():
    """Parse versions.env (KEY=VALUE lines) into a dict."""
    pairs = {}
    for raw in (REPO / "versions.env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        pairs[key] = value
    return pairs


def template_files():
    """Template files that must carry the shared labels (NOTES and helpers excluded)."""
    return [
        path
        for path in sorted(TEMPLATES.iterdir())
        if path.is_file() and path.name not in ("NOTES.txt", "_helpers.tpl")
    ]


class ChartYamlTest(unittest.TestCase):
    def test_chart_fields(self):
        chart = yaml.safe_load((CHART / "Chart.yaml").read_text(encoding="utf-8"))
        self.assertEqual(chart["apiVersion"], "v2")
        self.assertEqual(chart["name"], "tenantly")
        self.assertEqual(chart["type"], "application")
        self.assertEqual(chart["version"], "0.1.0")
        self.assertEqual(chart["appVersion"], "0.1.0")
        self.assertEqual(chart["kubeVersion"], ">=1.28.0-0")


class ValuesTest(unittest.TestCase):
    def test_every_values_file_parses(self):
        self.assertTrue(VALUES_FILES, "no values*.yaml files found")
        for path in VALUES_FILES:
            with self.subTest(file=path.name):
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertIsInstance(data, dict)

    def test_demo_image_matches_versions_env(self):
        versions = load_versions()
        demo = yaml.safe_load((CHART / "values-demo.yaml").read_text(encoding="utf-8"))
        image = f"{demo['image']['repository']}:{demo['image']['tag']}"
        self.assertEqual(image, versions["PODINFO_IMAGE"])


class TemplateStaticTest(unittest.TestCase):
    def test_templates_exist(self):
        self.assertTrue(template_files(), "no template files found")

    def test_every_template_includes_labels(self):
        for path in template_files():
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn('include "tenantly.labels"', text)

    def test_secret_is_guarded_by_plain_mode(self):
        text = (TEMPLATES / "secret.yaml").read_text(encoding="utf-8")
        self.assertIn('eq .Values.secrets.mode "plain"', text)


class HelmRenderTest(unittest.TestCase):
    def render(self, values_file=None):
        """Run helm template for the given values overlay and return (exit code, parsed docs, stderr)."""
        if not HELM.is_file():
            self.skipTest("helm not built")
        cmd = [str(HELM), "template", "t", "charts/tenantly", "--kube-version", KUBE_VERSION]
        if values_file:
            cmd += ["-f", f"charts/tenantly/{values_file}"]
        # Keep helm's cache and config inside .tools/ so nothing is written outside the repo.
        env = dict(os.environ)
        env["HELM_CACHE_HOME"] = str(REPO / ".tools" / "helm" / "cache")
        env["HELM_CONFIG_HOME"] = str(REPO / ".tools" / "helm" / "config")
        env["HELM_DATA_HOME"] = str(REPO / ".tools" / "helm" / "data")
        proc = subprocess.run(
            cmd, cwd=REPO, env=env, capture_output=True, text=True, check=False
        )
        docs = []
        if proc.returncode == 0:
            docs = [doc for doc in yaml.safe_load_all(proc.stdout) if doc]
        return proc.returncode, docs, proc.stderr

    def kinds(self, docs):
        return {doc.get("kind") for doc in docs}

    def test_default_render(self):
        code, docs, err = self.render()
        self.assertEqual(code, 0, err)
        kinds = self.kinds(docs)
        for kind in ("Deployment", "Service", "Ingress", "Secret", "StatefulSet", "Job"):
            self.assertIn(kind, kinds)

    def test_kind_render(self):
        code, _, err = self.render("values-kind.yaml")
        self.assertEqual(code, 0, err)

    def test_demo_render_has_no_stateful_or_job(self):
        code, docs, err = self.render("values-demo.yaml")
        self.assertEqual(code, 0, err)
        kinds = self.kinds(docs)
        self.assertNotIn("StatefulSet", kinds)
        self.assertNotIn("Job", kinds)

    def test_sealed_render_uses_sealed_secret_not_plain(self):
        code, docs, err = self.render("values-sealed.yaml")
        self.assertEqual(code, 0, err)
        kinds = self.kinds(docs)
        self.assertIn("SealedSecret", kinds)
        self.assertNotIn("Secret", kinds)

    def test_default_hpa_has_cpu_and_external_metrics(self):
        code, docs, err = self.render()
        self.assertEqual(code, 0, err)
        hpas = [d for d in docs if d.get("kind") == "HorizontalPodAutoscaler"]
        self.assertEqual(len(hpas), 1)
        hpa = hpas[0]
        self.assertEqual(hpa["apiVersion"], "autoscaling/v2")
        types = [m["type"] for m in hpa["spec"]["metrics"]]
        self.assertEqual(types, ["Resource", "External"])
        external = hpa["spec"]["metrics"][1]["external"]
        self.assertEqual(external["metric"]["name"], "tenantly_requests_per_second")
        self.assertEqual(external["target"]["type"], "AverageValue")
        self.assertEqual(external["target"]["averageValue"], "20")

    def test_deployment_omits_replicas_when_hpa_enabled(self):
        code, docs, err = self.render()
        self.assertEqual(code, 0, err)
        # Redis is also a Deployment, so pick the API Deployment by its component label.
        deployments = [
            d for d in docs
            if d.get("kind") == "Deployment"
            and d["spec"]["template"]["metadata"]["labels"].get("app.kubernetes.io/component") == "api"
        ]
        self.assertEqual(len(deployments), 1)
        self.assertNotIn("replicas", deployments[0]["spec"])

    def test_api_deployment_metadata_carries_chaos_selector_labels(self):
        # scripts/chaos.sh looks the API Deployment up with "-l <SELECTOR>,app.kubernetes.io/component=api",
        # and that query only matches metadata.labels. The pod template and spec.selector are not enough.
        code, docs, err = self.render()
        self.assertEqual(code, 0, err)
        api = [
            d for d in docs
            if d.get("kind") == "Deployment"
            and d["spec"]["template"]["metadata"]["labels"].get("app.kubernetes.io/component") == "api"
        ]
        self.assertEqual(len(api), 1)
        labels = api[0]["metadata"]["labels"]
        self.assertEqual(labels.get("app.kubernetes.io/component"), "api")
        self.assertEqual(labels.get("app.kubernetes.io/name"), "tenantly")
        chaos_selector = {"app.kubernetes.io/name": "tenantly", "app.kubernetes.io/component": "api"}
        for key, value in chaos_selector.items():
            self.assertEqual(labels.get(key), value, f"Deployment metadata.labels lacks {key}={value}")

    def test_default_render_has_dashboard_configmap_with_grafana_label(self):
        code, docs, err = self.render()
        self.assertEqual(code, 0, err)
        configmaps = [
            d for d in docs
            if d.get("kind") == "ConfigMap"
            and d["metadata"].get("labels", {}).get("grafana_dashboard") == "1"
        ]
        self.assertEqual(len(configmaps), 1)
        data = configmaps[0]["data"]
        self.assertIn("tenantly-overview.json", data)
        self.assertIn("tenantly-autoscaling.json", data)
        self.assertEqual(configmaps[0]["metadata"]["annotations"]["grafana_folder"], "Kubestack")

    def test_default_render_has_no_servicemonitor(self):
        code, docs, err = self.render()
        self.assertEqual(code, 0, err)
        self.assertNotIn("ServiceMonitor", self.kinds(docs))

    def test_demo_render_has_servicemonitor(self):
        code, docs, err = self.render("values-demo.yaml")
        self.assertEqual(code, 0, err)
        monitors = [d for d in docs if d.get("kind") == "ServiceMonitor"]
        self.assertEqual(len(monitors), 1)
        monitor = monitors[0]
        self.assertEqual(monitor["metadata"]["labels"]["release"], "kube-prometheus-stack")
        endpoint = monitor["spec"]["endpoints"][0]
        self.assertEqual(endpoint["port"], "http")
        self.assertEqual(endpoint["path"], "/metrics")
        self.assertEqual(endpoint["interval"], "15s")

        # A ServiceMonitor selects Service metadata labels, so the selector must match
        # exactly one rendered Service, and that Service must expose the scraped port.
        match_labels = monitor["spec"]["selector"]["matchLabels"]
        self.assertTrue(match_labels, "ServiceMonitor selector has no matchLabels")
        services = [
            d for d in docs
            if d.get("kind") == "Service"
            and all(d["metadata"].get("labels", {}).get(k) == v for k, v in match_labels.items())
        ]
        self.assertEqual(len(services), 1, f"selector {match_labels} matched {len(services)} Services")
        port_names = {p.get("name") for p in services[0]["spec"]["ports"]}
        self.assertIn(endpoint["port"], port_names)


if __name__ == "__main__":
    unittest.main()
