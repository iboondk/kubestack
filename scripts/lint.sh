#!/usr/bin/env bash
# Proof generation: helm lint and template, kubeconform, the offline validator and the unit tests.
# Every step writes its real output into proof/. A step that cannot run writes a SKIPPED line with
# the reason and is never reported as PASS. Exit status is non-zero when any step that ran failed.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
# lib.sh enables errexit. This script records each failure itself and keeps going.
set +e

cd "$REPO" || exit 2
mkdir -p proof

# Tool and cache paths are relative to the repo root. proof/ is checked for the internal project
# code by tools/validate.py, so absolute paths (which contain the checkout directory) must not be
# written into any proof file.
NOW_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
HELM=.tools/bin/helm
KUBECONFORM=.tools/bin/kubeconform
CHART=charts/tenantly
RESULTS=()
FAILED=0

# record <step> <PASS|FAIL|SKIPPED> <exit code or -> <note>
record() {
  RESULTS+=("$(printf '%-30s %-8s %-6s %s' "$1" "$2" "$3" "$4")")
  if [ "$2" = FAIL ]; then FAILED=1; fi
}

# run_logged <file> <label> <command...>: appends the command line and its full output to <file>,
# prints the exit code, and returns that exit code. Never aborts the script.
run_logged() {
  local file="$1" label="$2"
  shift 2
  {
    printf '\n$ %s\n' "$*"
  } >> "$file"
  "$@" 2>&1 | tee -a "$file"
  local rc=${PIPESTATUS[0]}
  printf '[exit %s] %s\n' "$rc" "$label" >> "$file"
  return "$rc"
}

# ---- (a) helm binary -------------------------------------------------------------------------
HELM_SKIP_REASON=""
if [ ! -x "$HELM" ]; then
  bash "$REPO/scripts/tools.sh" >&2
  tools_rc=$?
  if [ ! -x "$HELM" ]; then
    HELM_SKIP_REASON="helm binary not found at .tools/bin/helm after scripts/tools.sh (exit $tools_rc)"
  fi
fi

# ---- (b) helm lint -----------------------------------------------------------------------------
LINT=proof/helm-lint.txt
if [ -n "$HELM_SKIP_REASON" ]; then
  printf 'SKIPPED: helm not available (%s) at %s\n' "$HELM_SKIP_REASON" "$NOW_UTC" > "$LINT"
  record "helm lint" SKIPPED - "$HELM_SKIP_REASON"
else
  {
    echo "# helm lint output"
    echo "# date (UTC): $NOW_UTC"
    echo "# helm version: $("$HELM" version --short 2>&1)"
  } > "$LINT"

  lint_fail=0
  for values in "" "-f $CHART/values-kind.yaml" "-f $CHART/values-demo.yaml" "-f $CHART/values-sealed.yaml"; do
    # shellcheck disable=SC2086
    run_logged "$LINT" "helm lint ${values:-(no values file)}" "$HELM" lint "$CHART" $values
    rc=$?
    [ "$rc" -eq 0 ] || lint_fail=1
  done
  if [ "$lint_fail" -eq 0 ]; then
    record "helm lint (4 runs)" PASS 0 "see proof/helm-lint.txt"
  else
    record "helm lint (4 runs)" FAIL 1 "one or more runs exited non-zero; see proof/helm-lint.txt"
  fi
fi

# ---- (c) helm template -------------------------------------------------------------------------
if [ -n "$HELM_SKIP_REASON" ]; then
  rm -f proof/helm-template-*.yaml
  record "helm template (3 values)" SKIPPED - "$HELM_SKIP_REASON"
else
  tpl_fail=0
  for pair in "default:" "kind:-f $CHART/values-kind.yaml" "demo:-f $CHART/values-demo.yaml"; do
    name="${pair%%:*}"
    values="${pair#*:}"
    out="proof/helm-template-$name.yaml"
    # shellcheck disable=SC2086
    rendered="$("$HELM" template tenantly "$CHART" --namespace tenantly $values 2>&1)"
    rc=$?
    if [ "$rc" -eq 0 ]; then
      {
        printf '# rendered by %s on %s\n' "$("$HELM" version --short 2>&1)" "$NOW_UTC"
        printf '# command: helm template tenantly %s --namespace tenantly %s\n' "$CHART" "$values"
        printf '%s\n' "$rendered"
      } > "$out"
      rm -f "proof/helm-template-$name-error.txt"
    else
      tpl_fail=1
      rm -f "$out"
      printf '%s\n[exit %s]\n' "$rendered" "$rc" > "proof/helm-template-$name-error.txt"
    fi
  done
  if [ "$tpl_fail" -eq 0 ]; then
    record "helm template (3 values)" PASS 0 "proof/helm-template-{default,kind,demo}.yaml"
  else
    record "helm template (3 values)" FAIL 1 "see proof/helm-template-*-error.txt"
  fi
fi

# ---- (d) kubeconform ---------------------------------------------------------------------------
KC=proof/kubeconform.txt
if [ ! -x "$KUBECONFORM" ]; then
  printf 'SKIPPED: kubeconform not available (binary not found at .tools/bin/kubeconform) at %s\n' "$NOW_UTC" > "$KC"
  record "kubeconform" SKIPPED - "binary not found at .tools/bin/kubeconform"
else
  shopt -s nullglob
  inputs=(proof/helm-template-*.yaml argocd/root-app.yaml argocd/apps/*.yaml)
  shopt -u nullglob
  # Schemas are cached under .tools/ (git-ignored) so a re-run does not download them again.
  kc_args=(-summary -output text -ignore-missing-schemas -strict -cache .tools/kubeconform-cache)
  mkdir -p .tools/kubeconform-cache
  {
    echo "# kubeconform output"
    echo "# date (UTC): $NOW_UTC"
    echo "# version: $("$KUBECONFORM" -v 2>&1)"
    echo "# inputs: ${inputs[*]}"
  } > "$KC"

  # kc_once <k8s version>: runs kubeconform once, appends the command and its output to $KC,
  # and leaves the exit code in kc_rc, the raw output in kc_out and its Summary line in kc_summary.
  kc_out=".tools/tmp/kubeconform-out.txt"
  mkdir -p .tools/tmp
  kc_once() {
    printf '\n$ kubeconform %s -kubernetes-version %s %s\n' "${kc_args[*]}" "$1" "${inputs[*]}" >> "$KC"
    "$KUBECONFORM" "${kc_args[@]}" -kubernetes-version "$1" "${inputs[@]}" > "$kc_out" 2>&1
    kc_rc=$?
    cat "$kc_out" >> "$KC"
    printf '[exit %s] kubeconform k8s %s\n' "$kc_rc" "$1" >> "$KC"
    kc_summary="$(grep -E '^Summary:' "$kc_out" | tail -1)"
  }

  # kc_vacuous: succeeds when the run validated nothing. -ignore-missing-schemas lets a run exit 0
  # when every resource was Skipped (no schema found), so exit status alone is not proof.
  # A missing Summary line also counts as vacuous because the run cannot be confirmed.
  kc_vacuous() {
    local found valid skipped
    found="$(sed -n 's/^Summary: \([0-9]*\) resources.*/\1/p' <<< "$kc_summary")"
    valid="$(sed -n 's/.*- Valid: \([0-9]*\),.*/\1/p' <<< "$kc_summary")"
    skipped="$(sed -n 's/.*Skipped: \([0-9]*\).*/\1/p' <<< "$kc_summary")"
    if [ -z "$found" ] || [ -z "$valid" ]; then
      return 0
    fi
    if [ "$valid" -eq 0 ]; then
      return 0
    fi
    if [ "$found" -gt 0 ] && [ "${skipped:-0}" -eq "$found" ]; then
      return 0
    fi
    return 1
  }

  kc_version="1.36.0"
  kc_once "$kc_version"
  kc_retry_reason=""
  if [ "$kc_rc" -ne 0 ] && grep -qiE 'failed to (fetch|download)|status code|404|not found' "$kc_out"; then
    kc_retry_reason="schema download for kubernetes $kc_version failed"
  elif kc_vacuous; then
    kc_retry_reason="no resources were validated against kubernetes $kc_version schemas (Valid: 0 or every resource Skipped)"
  fi
  if [ -n "$kc_retry_reason" ]; then
    echo "# NOTE: $kc_retry_reason; retried with -kubernetes-version master" >> "$KC"
    kc_version="master"
    kc_once "$kc_version"
  fi
  rm -f "$kc_out"
  summary="${kc_summary:-}"
  if [ "$kc_rc" -eq 0 ] && ! kc_vacuous; then
    record "kubeconform (k8s $kc_version)" PASS 0 "${summary:-no summary line}"
  elif [ "$kc_rc" -eq 0 ]; then
    record "kubeconform (k8s $kc_version)" FAIL 1 "exit 0 but nothing validated: ${summary:-no summary line}"
  else
    record "kubeconform (k8s $kc_version)" FAIL "$kc_rc" "${summary:-see proof/kubeconform.txt}"
  fi
fi

# Python tracebacks print absolute file paths, which would carry the checkout directory into the
# proof files. Output is piped through sed so that the "$REPO/" prefix is removed (paths become
# repo-relative). The exit code is taken from python3 via PIPESTATUS.
redact_repo() {
  sed "s|$REPO/||g"
}

# ---- (e) offline validator ---------------------------------------------------------------------
# tools/validate.py scans every text file in the repo, proof/ included. A failing run quotes the
# internal code in its own output (validate.txt, and unittest.txt through the test assertion), so
# the previous run's copies would make the next run fail on its own output. Remove them first;
# both are regenerated below in this run.
rm -f proof/validate.txt proof/unittest.txt
python3 -B tools/validate.py 2>&1 | redact_repo > proof/validate.txt
rc=${PIPESTATUS[0]}
if [ "$rc" -eq 0 ]; then
  record "validate.py" PASS 0 "$(tail -1 proof/validate.txt)"
else
  record "validate.py" FAIL "$rc" "$(tail -1 proof/validate.txt)"
fi

# ---- (f) unit tests ----------------------------------------------------------------------------
python3 -B -m unittest discover -s tests -v 2>&1 | redact_repo > proof/unittest.txt
rc=${PIPESTATUS[0]}
if [ "$rc" -eq 0 ]; then
  record "unittest" PASS 0 "$(grep -E '^(Ran |OK|FAILED)' proof/unittest.txt | tr '\n' ' ')"
else
  record "unittest" FAIL "$rc" "$(grep -E '^(Ran |OK|FAILED)' proof/unittest.txt | tr '\n' ' ')"
fi

# ---- (g) summary -------------------------------------------------------------------------------
echo
printf '%-30s %-8s %-6s %s\n' "STEP" "STATUS" "EXIT" "NOTE"
printf '%-30s %-8s %-6s %s\n' "------------------------------" "--------" "------" "----"
for line in "${RESULTS[@]}"; do
  echo "$line"
done
echo
if [ "$FAILED" -ne 0 ]; then
  echo "lint.sh: one or more steps FAILED"
  exit 1
fi
echo "lint.sh: all steps that ran passed (skipped steps are listed as SKIPPED above)"
exit 0
