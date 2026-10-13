"""Stub-kubectl tests for the recovery measurement in scripts/chaos.sh.

The stub kubectl reports a stale Deployment status (readyReplicas == desired) on the very first
poll, while the victim pod is already gone and its replacement is not ready yet. A round must not
be recorded as a 0s PASS. Recovery has to come from the pods, not from controller status.

The test copies scripts/ and versions.env into a scratch repo under .tools/ (gitignored), so the
run writes its proof/chaos output there and never touches the real proof/ directory. Standard
library only. Nothing here talks to a cluster.
"""
import sys

sys.dont_write_bytecode = True

import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
BASH = shutil.which("bash") or "/bin/bash"
SCRATCH_ROOT = REPO / ".tools"

KUBECTL_STUB = r"""#!/usr/bin/env bash
state="$STUB_STATE"
echo "$*" >> "$state/calls.log"
args="$*"
case "$args" in
  "cluster-info"*) echo "Kubernetes control plane is running at https://stub" ;;
  "version"*) echo "Client Version: stub" ;;
  *"range"*)
    # Pod listing used for recovery: "uid|deletionTimestamp|Ready" per pod.
    n=$(( $(cat "$state/polls" 2>/dev/null || echo 0) + 1 ))
    echo "$n" > "$state/polls"
    if [ "${STUB_MODE:-recover}" = "never" ]; then
      printf 'uid-new||False\n'
    else
      case "$n" in
        1) ;;                             # victim gone, replacement not created yet
        2) printf 'uid-new||False\n' ;;   # replacement created, not Ready
        *) printf 'uid-new||True\n' ;;    # replacement Ready
      esac
    fi
    ;;
  # The victim is already gone from the API server, so the UID list is empty.
  *"items[*].metadata.uid"*) ;;
  *"--field-selector"*) echo "tenantly-api-old" ;;
  # Label lookup. STUB_DEPLOY_LABEL=missing models a Deployment without the component label.
  *"get deploy -n"*"items[0].metadata.name"*)
    if [ "${STUB_DEPLOY_LABEL:-ok}" != missing ]; then echo "tenantly-api"; fi ;;
  # Owner chain used when the label lookup finds nothing: pod -> ReplicaSet -> Deployment.
  *"get pod tenantly-api-old"*"ownerReferences"*"ReplicaSet"*) echo "tenantly-api-7d9f" ;;
  *"get replicaset tenantly-api-7d9f"*"Deployment"*) echo "tenantly-api" ;;
  # chaos.sh reads the Deployment as "get deploy NAME" (desired) and "get deployment/NAME" (status).
  *"tenantly-api -n"*"spec.replicas"*) echo 1 ;;
  # Stale controller status: already "desired" although the old pod is gone and no new pod is Ready.
  *"tenantly-api -n"*"readyReplicas"*) echo 1 ;;
  *"get pod tenantly-api-old"*"metadata.uid"*) echo "uid-old" ;;
  *"delete pod"*) echo 'pod "tenantly-api-old" force deleted' ;;
  *"get pods"*"-o wide"*) echo "NAME READY STATUS" ;;
  *) ;;
esac
"""

CURL_STUB = """#!/usr/bin/env bash
echo 200
"""

RECOVERY_LINE = re.compile(
    r"pod-kill round 1: target \S+ recovery (\d+)s result (PASS|FAIL)"
)


class ChaosRecoveryStubTests(unittest.TestCase):
    def setUp(self):
        SCRATCH_ROOT.mkdir(exist_ok=True)
        self._tmp = tempfile.TemporaryDirectory(dir=SCRATCH_ROOT)
        self.work = pathlib.Path(self._tmp.name)
        self.repo = self.work / "repo"
        self.stub_dir = self.work / "stubs"
        self.state = self.work / "state"
        (self.repo / "scripts").mkdir(parents=True)
        self.stub_dir.mkdir()
        self.state.mkdir()
        for script in (REPO / "scripts").glob("*.sh"):
            shutil.copy(script, self.repo / "scripts" / script.name)
        shutil.copy(REPO / "versions.env", self.repo / "versions.env")
        self._write_stub("kubectl", KUBECTL_STUB)
        self._write_stub("curl", CURL_STUB)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_stub(self, name, body):
        path = self.stub_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    def _run_pod_kill(self, mode, timeout, deploy_label="ok"):
        env = {
            "PATH": f"{self.stub_dir}:/usr/bin:/bin",
            "STUB_STATE": str(self.state),
            "STUB_MODE": mode,
            "STUB_DEPLOY_LABEL": deploy_label,
            "HOME": str(self.work),
        }
        return subprocess.run(
            [
                BASH,
                str(self.repo / "scripts" / "chaos.sh"),
                "--experiment", "pod-kill",
                "--rounds", "1",
                "--timeout", str(timeout),
                "--url", "http://stub.invalid/healthz",
            ],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def _report_text(self):
        reports = sorted((self.repo / "proof" / "chaos").glob("chaos-report-*.md"))
        self.assertEqual(len(reports), 1, msg="expected exactly one report")
        return reports[0].read_text(encoding="utf-8")

    def test_stale_status_does_not_yield_zero_second_pass(self):
        result = self._run_pod_kill("recover", timeout=30)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, msg=output)
        match = RECOVERY_LINE.search(output)
        self.assertIsNotNone(match, msg=output)
        recovery, outcome = int(match.group(1)), match.group(2)
        self.assertEqual(outcome, "PASS", msg=output)
        self.assertGreaterEqual(
            recovery, 1,
            msg="recovery must wait for a Ready replacement pod, not the stale status",
        )

    def test_no_ready_replacement_is_a_failure(self):
        result = self._run_pod_kill("never", timeout=2)
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, msg=output)
        match = RECOVERY_LINE.search(output)
        self.assertIsNotNone(match, msg=output)
        self.assertEqual(match.group(2), "FAIL", msg=output)

    def test_deployment_found_through_owner_chain_when_label_missing(self):
        result = self._run_pod_kill("recover", timeout=30, deploy_label="missing")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, msg=output)
        match = RECOVERY_LINE.search(output)
        self.assertIsNotNone(match, msg=output)
        self.assertEqual(match.group(2), "PASS", msg=output)
        calls = (self.state / "calls.log").read_text(encoding="utf-8")
        self.assertIn("get replicaset tenantly-api-7d9f", calls)

    def test_report_states_target_and_measured_probe_interval(self):
        result = self._run_pod_kill("recover", timeout=30)
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        report = self._report_text()
        self.assertIn("target interval 0.5s", report)
        self.assertRegex(report, r"measured mean interval [0-9]+\.[0-9]+ s")
        self.assertNotIn("every 0.5s", report)


if __name__ == "__main__":
    unittest.main()
