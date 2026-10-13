#!/usr/bin/env python3
"""Offline checks for the Kubestack repo.

Python 3.10, standard library plus PyYAML.

Each check is a function that takes the repo root and returns a list of
(name, ok, detail) results. Add new checks by writing a function and appending
it to CHECKS. Missing directories or files are skipped, never failed.
"""
from __future__ import annotations

import fnmatch
import glob
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Callable

import yaml

CheckResult = tuple[str, bool, str]
Check = Callable[[pathlib.Path], list[CheckResult]]

REQUIRED_VERSION_KEYS = [
    "KIND_VERSION",
    "KIND_NODE_IMAGE",
    "HELM_VERSION",
    "KUBECONFORM_VERSION",
    "ARGOCD_VERSION",
    "SEALED_SECRETS_CHART",
    "METRICS_SERVER_CHART",
    "KPS_CHART",
    "TRAEFIK_CHART",
    "PROM_ADAPTER_CHART",
    "PODINFO_IMAGE",
    "POSTGRES_IMAGE",
    "REDIS_IMAGE",
    "HEY_IMAGE",
    "CLUSTER_NAME",
    "DEFAULT_REPO_URL",
]

PLACEHOLDER_PREFIX = "change-me"
SKIP_DIR_NAMES = {".tools", ".git"}
# Built with join so this file does not contain the literal strings it searches for.
FORBIDDEN_STRINGS = [
    "".join(["Co", "-Authored", "-By"]),
    "".join(["Gener", "ated with ", "Claude"]),
]

_KEY_VALUE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _rel(repo: pathlib.Path, path: pathlib.Path) -> str:
    return path.relative_to(repo).as_posix()


def _parse_key_values(path: pathlib.Path) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _KEY_VALUE.match(line)
        if match:
            pairs.append((match.group(1), match.group(2)))
    return pairs


def check_yaml_parses(repo: pathlib.Path) -> list[CheckResult]:
    """(a) Every YAML file under kind/, argocd/, platform/ and chart values*.yaml parses."""
    files: set[pathlib.Path] = set()
    for sub in ("kind", "argocd", "platform"):
        base = repo / sub
        if not base.is_dir():
            continue
        for pattern in ("*.yaml", "*.yml"):
            for path in base.rglob(pattern):
                if "templates" not in path.relative_to(base).parts:
                    files.add(path)
    charts = repo / "charts"
    if charts.is_dir():
        for path in charts.glob("*/values*.yaml"):
            files.add(path)
    if not files:
        return [("yaml parses", True, "skipped: no YAML files found")]

    results: list[CheckResult] = []
    for path in sorted(files):
        name = f"yaml parses {_rel(repo, path)}"
        try:
            with path.open(encoding="utf-8") as handle:
                list(yaml.safe_load_all(handle))
            results.append((name, True, ""))
        except (yaml.YAMLError, UnicodeDecodeError) as exc:
            results.append((name, False, " ".join(str(exc).split())))
    return results


def check_dashboards_json(repo: pathlib.Path) -> list[CheckResult]:
    """(b) Every JSON file under charts/tenantly/dashboards parses."""
    base = repo / "charts" / "tenantly" / "dashboards"
    if not base.is_dir():
        return [("dashboard json", True, "skipped: no dashboards directory")]
    paths = sorted(base.rglob("*.json"))
    if not paths:
        return [("dashboard json", True, "skipped: no JSON files found")]

    results: list[CheckResult] = []
    for path in paths:
        name = f"dashboard json {_rel(repo, path)}"
        try:
            with path.open(encoding="utf-8") as handle:
                json.load(handle)
            results.append((name, True, ""))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            results.append((name, False, str(exc)))
    return results


def _dashboard_problems(doc: object) -> list[str]:
    """Return the schema problems in one parsed Grafana dashboard (empty list when valid)."""
    if not isinstance(doc, dict):
        return ["top level is not an object"]
    problems: list[str] = []
    for key in ("uid", "title"):
        if not isinstance(doc.get(key), str) or not doc.get(key):
            problems.append(f"missing {key}")
    panels = doc.get("panels")
    if not isinstance(panels, list) or not panels:
        problems.append("panels missing or empty")
        return problems

    seen_ids: set[object] = set()
    for index, panel in enumerate(panels):
        if not isinstance(panel, dict):
            problems.append(f"panel {index} is not an object")
            continue
        pid = panel.get("id")
        if pid in seen_ids:
            problems.append(f"duplicate panel id {pid!r}")
        seen_ids.add(pid)
        for target in panel.get("targets") or []:
            expr = target.get("expr") if isinstance(target, dict) else None
            if not isinstance(expr, str) or not expr.strip():
                problems.append(f"panel {pid!r} has a target with an empty expr")
        datasource = panel.get("datasource") or {}
        uid = datasource.get("uid") if isinstance(datasource, dict) else None
        if uid != "${datasource}":
            problems.append(f"panel {pid!r} datasource uid is {uid!r}, not '${{datasource}}'")
    return problems


def check_dashboards_schema(repo: pathlib.Path) -> list[CheckResult]:
    """(g) Each dashboard JSON has uid, title and panels; unique panel ids; non-empty exprs; ${datasource} uid."""
    base = repo / "charts" / "tenantly" / "dashboards"
    if not base.is_dir():
        return [("dashboards_schema", True, "skipped: no dashboards directory")]
    paths = sorted(base.glob("*.json"))
    if not paths:
        return [("dashboards_schema", True, "skipped: no dashboard JSON files found")]

    results: list[CheckResult] = []
    for path in paths:
        name = f"dashboards_schema {_rel(repo, path)}"
        try:
            with path.open(encoding="utf-8") as handle:
                doc = json.load(handle)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            results.append((name, False, f"unparseable: {exc}"))
            continue
        problems = _dashboard_problems(doc)
        if problems:
            results.append((name, False, "; ".join(problems)))
        else:
            results.append((name, True, ""))
    return results


def check_shell_syntax(repo: pathlib.Path) -> list[CheckResult]:
    """(c) bash -n passes on every scripts/*.sh."""
    paths = sorted(pathlib.Path(p) for p in glob.glob(str(repo / "scripts" / "*.sh")))
    if not paths:
        return [("bash -n", True, "skipped: no scripts/*.sh found")]

    results: list[CheckResult] = []
    for path in paths:
        name = f"bash -n {_rel(repo, path)}"
        proc = subprocess.run(
            ["bash", "-n", str(path)], capture_output=True, text=True, check=False
        )
        if proc.returncode == 0:
            results.append((name, True, ""))
        else:
            results.append((name, False, proc.stderr.strip() or "bash -n failed"))
    return results


def check_versions_env(repo: pathlib.Path) -> list[CheckResult]:
    """(d) versions.env has every required key, in plain KEY=value form with no quotes."""
    path = repo / "versions.env"
    if not path.is_file():
        return [("versions.env", True, "skipped: file missing")]

    name = "versions.env keys"
    pairs = _parse_key_values(path)
    present = {key for key, _ in pairs}
    missing = [key for key in REQUIRED_VERSION_KEYS if key not in present]
    if missing:
        return [(name, False, "missing keys: " + ", ".join(missing))]

    quoted = [key for key, value in pairs if '"' in value or "'" in value]
    if quoted:
        return [(name, False, "values must not be quoted: " + ", ".join(quoted))]
    return [(name, True, "")]


def check_versions_pinned(repo: pathlib.Path) -> list[CheckResult]:
    """(d2) No floating image tag in versions.env. Every *_IMAGE needs a tag other than latest, or a digest."""
    path = repo / "versions.env"
    if not path.is_file():
        return [("versions.env pins", True, "skipped: file missing")]

    name = "versions.env pins"
    problems = []
    for key, value in _parse_key_values(path):
        if value.endswith(":latest") or ":latest@" in value:
            problems.append(f"{key} uses :latest ({value})")
        elif key.endswith("_IMAGE") and "@sha256:" not in value:
            # A tag is a colon after the last slash. A bare name such as "nginx" has no tag.
            last = value.rsplit("/", 1)[-1]
            if ":" not in last:
                problems.append(f"{key} has no tag or digest ({value})")
    if problems:
        return [(name, False, "; ".join(problems))]
    return [(name, True, "")]


def check_env_example(repo: pathlib.Path) -> list[CheckResult]:
    """(e) Every value in .env.example starts with change-me."""
    path = repo / ".env.example"
    if not path.is_file():
        return [(".env.example placeholders", True, "skipped: file missing")]

    name = ".env.example placeholders"
    bad = [
        key
        for key, value in _parse_key_values(path)
        if not value.startswith(PLACEHOLDER_PREFIX)
    ]
    if bad:
        return [(name, False, "not placeholders: " + ", ".join(bad))]
    return [(name, True, "")]


def check_no_attribution(repo: pathlib.Path) -> list[CheckResult]:
    """(f) No file in the repo (except .tools/ and .git/) contains a forbidden attribution string."""
    offenders: list[str] = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES]
        for filename in files:
            path = pathlib.Path(root) / filename
            try:
                data = path.read_bytes()
            except OSError as exc:
                offenders.append(f"{_rel(repo, path)} (unreadable: {exc})")
                continue
            for needle in FORBIDDEN_STRINGS:
                if needle.encode("utf-8") in data:
                    offenders.append(f"{_rel(repo, path)} contains '{needle}'")
                    break
    name = "no AI attribution"
    if offenders:
        return [(name, False, "; ".join(sorted(offenders)))]
    return [(name, True, "")]


# Internal numbering (for example an internal project code) must not appear in published files.
INTERNAL_CODE_PATTERN = re.compile(r"(?i)\bp[0-9]+-(tenantly|kubestack)\b|\bP[0-9]\b")


def _gitignore_patterns(repo: pathlib.Path) -> list[str]:
    path = repo / ".gitignore"
    if not path.is_file():
        return []
    lines = (raw.strip() for raw in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _is_gitignored(rel: str, patterns: list[str]) -> bool:
    """Approximate git's matching for the simple patterns in this repo. Negations are not applied,
    so a file that a negation would re-include is still scanned."""
    parts = rel.split("/")
    for pattern in patterns:
        if pattern.startswith("!"):
            continue
        body = pattern.rstrip("/").lstrip("/")
        if "/" in body:
            if fnmatch.fnmatchcase(rel, body) or rel.startswith(body + "/"):
                return True
        elif any(fnmatch.fnmatchcase(part, body) for part in parts):
            return True
    return False


def check_no_internal_codes(repo: pathlib.Path) -> list[CheckResult]:
    """(i) No tracked text file (not .tools/, .git/ or gitignored) carries an internal project code."""
    patterns = _gitignore_patterns(repo)
    offenders: list[str] = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES]
        for filename in files:
            path = pathlib.Path(root) / filename
            rel = _rel(repo, path)
            if _is_gitignored(rel, patterns):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue  # binary file: not a text file, so not scanned
            except OSError as exc:
                offenders.append(f"{rel} (unreadable: {exc})")
                continue
            match = INTERNAL_CODE_PATTERN.search(text)
            if match:
                offenders.append(f"{rel} contains '{match.group(0)}'")
    name = "no internal project codes"
    if offenders:
        return [(name, False, "; ".join(sorted(offenders)))]
    return [(name, True, "")]


# Chart source targetRevision must equal the versions.env key for the app file (file stem -> key).
ARGO_CHART_VERSION_KEYS = {
    "sealed-secrets": "SEALED_SECRETS_CHART",
    "metrics-server": "METRICS_SERVER_CHART",
    "kube-prometheus-stack": "KPS_CHART",
    "traefik": "TRAEFIK_CHART",
    "prometheus-adapter": "PROM_ADAPTER_CHART",
}
ARGO_WAVE_ANNOTATION = "argocd.argoproj.io/sync-wave"
ARGO_WORKLOAD_APP = "tenantly"


def check_argocd_apps(repo: pathlib.Path) -> list[CheckResult]:
    """(h) Each argocd/apps file is an Application with a sync wave; chart sources are pinned to versions.env; tenantly has the highest wave."""
    base = repo / "argocd" / "apps"
    if not base.is_dir():
        return [("argocd_apps", True, "skipped: no argocd/apps directory")]
    paths = sorted(base.glob("*.yaml"))
    if not paths:
        return [("argocd_apps", False, "argocd/apps has no YAML files")]

    name = "argocd_apps"
    problems: list[str] = []
    versions: dict[str, str] = {}
    versions_path = repo / "versions.env"
    if versions_path.is_file():
        versions = dict(_parse_key_values(versions_path))
    waves: dict[str, int] = {}

    for path in paths:
        rel = _rel(repo, path)
        app_name = path.stem
        try:
            with path.open(encoding="utf-8") as handle:
                doc = yaml.safe_load(handle)
        except (yaml.YAMLError, UnicodeDecodeError) as exc:
            problems.append(f"{rel} does not parse: {' '.join(str(exc).split())}")
            continue
        if not isinstance(doc, dict) or doc.get("kind") != "Application":
            problems.append(f"{rel} is not kind Application")
            continue

        metadata = doc.get("metadata") or {}
        annotations = metadata.get("annotations") or {}
        raw_wave = annotations.get(ARGO_WAVE_ANNOTATION)
        try:
            wave = int(str(raw_wave))
        except ValueError:
            problems.append(f"{rel} has no integer {ARGO_WAVE_ANNOTATION} annotation")
            continue
        waves[app_name] = wave

        spec = doc.get("spec") or {}
        sources = spec.get("sources") or ([spec["source"]] if spec.get("source") else [])
        for source in sources:
            if not isinstance(source, dict) or "chart" not in source:
                continue
            revision = str(source.get("targetRevision", ""))
            if revision in ("", "HEAD", "*"):
                problems.append(f"{rel} chart source has targetRevision {revision!r}, not a pinned version")
                continue
            key = ARGO_CHART_VERSION_KEYS.get(app_name)
            if key is None:
                problems.append(f"{rel} is a chart source but has no versions.env key mapping")
            elif key not in versions:
                problems.append(f"{rel} needs {key}, which is missing from versions.env")
            elif revision != versions[key]:
                problems.append(f"{rel} targetRevision {revision} != {key}={versions[key]}")

    if ARGO_WORKLOAD_APP not in waves:
        problems.append(f"no {ARGO_WORKLOAD_APP} Application found")
    else:
        top = max(waves.values())
        others = [w for app, w in waves.items() if app != ARGO_WORKLOAD_APP]
        if others and waves[ARGO_WORKLOAD_APP] <= max(others):
            problems.append(f"{ARGO_WORKLOAD_APP} wave {waves[ARGO_WORKLOAD_APP]} is not the highest (max {top})")

    if problems:
        return [(name, False, "; ".join(problems))]
    return [(name, True, f"{len(paths)} apps checked")]


CHECKS: list[Check] = [
    check_yaml_parses,
    check_dashboards_json,
    check_dashboards_schema,
    check_shell_syntax,
    check_versions_env,
    check_versions_pinned,
    check_env_example,
    check_no_attribution,
    check_no_internal_codes,
    check_argocd_apps,
]


def run_checks(repo: pathlib.Path) -> list[CheckResult]:
    """Run every registered check against repo and return all (name, ok, detail) results."""
    repo = pathlib.Path(repo)
    results: list[CheckResult] = []
    for check in CHECKS:
        results.extend(check(repo))
    return results


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    repo = pathlib.Path(args[0]) if args else pathlib.Path(__file__).resolve().parents[1]
    results = run_checks(repo)
    failed = 0
    for name, ok, detail in results:
        if ok:
            suffix = f" ({detail})" if detail else ""
            print(f"PASS {name}{suffix}")
        else:
            failed += 1
            print(f"FAIL {name}: {detail}")
    print(f"{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
