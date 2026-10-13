#!/usr/bin/env bash
# Chaos experiments for the Tenantly demo on kind, with a measured recovery report.
#
# Usage:
#   scripts/chaos.sh [--dry-run] [--experiment pod-kill|node-drain|db-kill|all]
#                    [--rounds 3] [--namespace tenantly]
#                    [--selector app.kubernetes.io/name=tenantly]
#                    [--url http://localhost:30080/healthz] [--timeout 180]
#
# --dry-run prints every kubectl command it would run, prefixed "DRY-RUN:". It needs no
# cluster, no kubectl and no curl, and it writes no report.
#
# pod-kill targets the API pods: the selector plus app.kubernetes.io/component=api.
# db-kill targets the postgres pod (app.kubernetes.io/component=postgres).
#
# Exit codes: 0 every round recovered (or dry-run), 1 a round failed or the cluster is
#             unreachable, 2 bad usage or a required tool is missing.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

API_COMPONENT="app.kubernetes.io/component=api"
POSTGRES_COMPONENT="app.kubernetes.io/component=postgres"
PROBE_INTERVAL=0.5
ROUND_PAUSE=10
DRAIN_TIMEOUT=120s

EXPERIMENT=all
DRY_RUN=0
ROUNDS=3
NAMESPACE=tenantly
SELECTOR=app.kubernetes.io/name=tenantly
URL=http://localhost:30080/healthz
TIMEOUT=180

PROBE_PID=""
DRAIN_NODE=""
FAILURES=0
RUN_TS=""
CSV_FILE=""
RESULTS_FILE=""
PODS_BEFORE_FILE=""
PODS_AFTER_FILE=""

usage() {
  cat >&2 <<'USAGE'
Usage: scripts/chaos.sh [--dry-run] [--experiment pod-kill|node-drain|db-kill|all]
                        [--rounds N] [--namespace NS] [--selector LABELS]
                        [--url URL] [--timeout SECONDS]
USAGE
}

# need_value FLAG [VALUE]: exit 2 when a flag has no value.
need_value() {
  if [ "$#" -lt 2 ] || [ -z "$2" ]; then
    echo "Missing value for $1" >&2
    usage
    exit 2
  fi
}

# is_positive_int VALUE: true for integers >= 1.
is_positive_int() {
  [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 1 ]
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --experiment) need_value "$@"; EXPERIMENT="$2"; shift 2 ;;
    --rounds) need_value "$@"; ROUNDS="$2"; shift 2 ;;
    --namespace) need_value "$@"; NAMESPACE="$2"; shift 2 ;;
    --selector) need_value "$@"; SELECTOR="$2"; shift 2 ;;
    --url) need_value "$@"; URL="$2"; shift 2 ;;
    --timeout) need_value "$@"; TIMEOUT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

case "$EXPERIMENT" in
  pod-kill|node-drain|db-kill|all) ;;
  *) echo "Unknown experiment: $EXPERIMENT (use pod-kill, node-drain, db-kill or all)" >&2; usage; exit 2 ;;
esac
is_positive_int "$ROUNDS" || { echo "--rounds must be a positive integer" >&2; exit 2; }
is_positive_int "$TIMEOUT" || { echo "--timeout must be a positive integer (seconds)" >&2; exit 2; }

# now_ms: epoch milliseconds, for the probe CSV. perl starts far faster than python3, which keeps the
# probe interval close to PROBE_INTERVAL. Falls back to python3, then to whole seconds from date.
now_ms() {
  local ms
  if ms="$(perl -MTime::HiRes=time -e 'printf "%d\n", time * 1000' 2>/dev/null)" && [ -n "$ms" ]; then
    echo "$ms"
  elif ms="$(python3 -c 'import time; print(int(time.time() * 1000))' 2>/dev/null)" && [ -n "$ms" ]; then
    echo "$ms"
  else
    echo $(( $(date +%s) * 1000 ))
  fi
}

# act CMD...: run a side-effecting command, or print it as DRY-RUN when --dry-run is set.
act() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf 'DRY-RUN: %s\n' "$*"
    return 0
  fi
  "$@"
}

# dry_note TEXT...: describe a read or a wait that dry-run does not perform.
dry_note() {
  printf 'DRY-RUN: %s\n' "$*"
}

# plan: print what this run will do.
plan() {
  local list
  if [ "$EXPERIMENT" = "all" ]; then
    list="pod-kill node-drain db-kill"
  else
    list="$EXPERIMENT"
  fi
  printf 'PLAN: experiments=[%s] rounds=%s namespace=%s selector=%s url=%s timeout=%ss dry_run=%s\n' \
    "$list" "$ROUNDS" "$NAMESPACE" "$SELECTOR" "$URL" "$TIMEOUT" "$DRY_RUN"
}

# probe_loop CSV: every PROBE_INTERVAL seconds, curl the URL and append "epoch_ms,status_code".
# A failed or timed-out request is recorded as 000.
probe_loop() {
  local csv="$1" ms code
  while :; do
    ms="$(now_ms)"
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 1 "$URL" 2>/dev/null || true)"
    [ -n "$code" ] || code=000
    printf '%s,%s\n' "$ms" "$code" >> "$csv"
    sleep "$PROBE_INTERVAL"
  done
}

# start_probe: start probe_loop in the background (or describe it in dry-run).
start_probe() {
  if [ "$DRY_RUN" -eq 1 ]; then
    dry_note "curl -s -o /dev/null -w '%{http_code}' --max-time 1 $URL every ${PROBE_INTERVAL}s, appending to proof/chaos/run-<ts>.tmp"
    return 0
  fi
  probe_loop "$CSV_FILE" 2>/dev/null &
  PROBE_PID=$!
  log "probe loop started (pid $PROBE_PID), CSV $CSV_FILE"
}

# stop_probe: stop the probe loop if it runs.
stop_probe() {
  if [ -n "$PROBE_PID" ]; then
    pkill -P "$PROBE_PID" 2>/dev/null || true
    kill "$PROBE_PID" 2>/dev/null || true
    wait "$PROBE_PID" 2>/dev/null || true
    PROBE_PID=""
  fi
}

# uncordon_node: uncordon the drained node. Called after each node-drain round and from the EXIT trap.
uncordon_node() {
  if [ -n "$DRAIN_NODE" ]; then
    local node="$DRAIN_NODE"
    DRAIN_NODE=""
    act kubectl uncordon "$node" || log "uncordon of $node failed; run: kubectl uncordon $node"
  fi
}

# cleanup: EXIT trap. Stops the probe loop and makes sure no node stays cordoned.
cleanup() {
  stop_probe
  uncordon_node
}

# pick_pod SELECTOR: print one random running pod that matches the selector (may print nothing).
pick_pod() {
  kubectl get pods -n "$NAMESPACE" -l "$1" --field-selector=status.phase=Running \
      -o jsonpath='{.items[*].metadata.name}' 2>/dev/null \
    | tr ' ' '\n' \
    | awk 'NF { names[++n] = $0 } END { if (n) { srand(); print names[int(rand() * n) + 1] } }'
}

# pick_drain_node: print the worker node that runs the most pods matching SELECTOR.
pick_drain_node() {
  local workers pods best
  workers="$(kubectl get nodes -l '!node-role.kubernetes.io/control-plane' \
    -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)" || return 1
  pods="$(kubectl get pods -n "$NAMESPACE" -l "$SELECTOR" \
    -o jsonpath='{.items[*].spec.nodeName}' 2>/dev/null)" || return 1
  best="$(
    for w in $workers; do
      count="$(printf '%s\n' "$pods" | tr ' ' '\n' | grep -cx "$w" || true)"
      printf '%s %s\n' "$count" "$w"
    done | sort -k1,1nr -k2,2 | awk 'NR == 1 { print $2 }'
  )"
  printf '%s' "$best"
}

# deploy_of_pod POD: print the Deployment that owns POD, following pod -> ReplicaSet -> Deployment.
deploy_of_pod() {
  local rs
  rs="$(kubectl get pod "$1" -n "$NAMESPACE" \
    -o jsonpath='{.metadata.ownerReferences[?(@.kind=="ReplicaSet")].name}' 2>/dev/null || true)"
  [ -n "$rs" ] || return 0
  kubectl get replicaset "$rs" -n "$NAMESPACE" \
    -o jsonpath='{.metadata.ownerReferences[?(@.kind=="Deployment")].name}' 2>/dev/null || true
}

# resolve_api_deploy: print the API Deployment name. The Deployment is found by label first. When no
# Deployment carries the component label, it is found from the owner chain of a running API pod.
resolve_api_deploy() {
  local api_sel="$SELECTOR,$API_COMPONENT" deploy="" pod=""
  deploy="$(kubectl get deploy -n "$NAMESPACE" -l "$api_sel" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  if [ -z "$deploy" ]; then
    pod="$(pick_pod "$api_sel" || true)"
    if [ -n "$pod" ]; then
      deploy="$(deploy_of_pod "$pod" || true)"
    fi
  fi
  printf '%s' "$deploy"
}

# replacement_ready OLD_POD_UID POD_SELECTOR: true when the victim pod is gone and at least one
# matching pod that is not terminating is Ready. Pod state is read directly, not from the controller.
replacement_ready() {
  local old_uid="$1" pod_sel="$2" pods line uid deleting ready_state found=0
  pods="$(kubectl get pods -n "$NAMESPACE" -l "$pod_sel" \
    -o jsonpath='{range .items[*]}{.metadata.uid}|{.metadata.deletionTimestamp}|{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' \
    2>/dev/null || true)"
  while IFS='|' read -r uid deleting ready_state; do
    [ -n "$uid" ] || continue
    if [ -n "$old_uid" ] && [ "$uid" = "$old_uid" ]; then
      return 1
    fi
    if [ -z "$deleting" ] && [ "$ready_state" = "True" ]; then
      found=1
    fi
  done <<< "$pods"
  [ "$found" -eq 1 ]
}

# wait_recovered TARGET DESIRED START_EPOCH TIMEOUT [OLD_POD_UID] [POD_SELECTOR]
# Polls TARGET (kind/name) until readyReplicas == DESIRED and, when POD_SELECTOR is given,
# replacement_ready holds. The victim pod must be gone, so a stale controller status cannot end the
# wait early. Prints the elapsed seconds on stdout. Returns 1 when the timeout passes first.
wait_recovered() {
  local target="$1" desired="$2" start="$3" timeout="$4" old_uid="${5:-}" pod_sel="${6:-}"
  local ready now elapsed
  while :; do
    ready="$(kubectl get "$target" -n "$NAMESPACE" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)"
    ready="${ready:-0}"
    now="$(date +%s)"
    elapsed=$(( now - start ))
    if [ "$ready" = "$desired" ]; then
      if [ -z "$pod_sel" ] || replacement_ready "$old_uid" "$pod_sel"; then
        echo "$elapsed"
        return 0
      fi
    fi
    if [ "$elapsed" -ge "$timeout" ]; then
      echo "$elapsed"
      return 1
    fi
    sleep 1
  done
}

# record EXPERIMENT ROUND TARGET RECOVERY_SECONDS RESULT: one row for the report. Counts failures.
record() {
  if [ "$DRY_RUN" -eq 1 ]; then
    return 0
  fi
  printf '%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" >> "$RESULTS_FILE"
  if [ "$5" != "PASS" ]; then
    FAILURES=$(( FAILURES + 1 ))
  fi
  log "$1 round $2: target $3 recovery ${4}s result $5"
}

# exp_pod_kill: delete one random API pod per round and wait for the Deployment to recover.
exp_pod_kill() {
  local api_sel="$SELECTOR,$API_COMPONENT" round deploy desired victim uid start rec result
  for (( round = 1; round <= ROUNDS; round++ )); do
    if [ "$DRY_RUN" -eq 1 ]; then
      dry_note "round $round: kubectl get deploy -n $NAMESPACE -l '$api_sel' -o jsonpath='{.items[0].metadata.name}' and '{.spec.replicas}' (desired replicas)"
      dry_note "round $round: kubectl get pods -n $NAMESPACE -l '$api_sel' --field-selector=status.phase=Running, then pick one at random"
      act kubectl delete pod "<random-api-pod>" -n "$NAMESPACE" --grace-period=0 --force
      dry_note "round $round: poll deploy/<api> until readyReplicas == desired, timeout ${TIMEOUT}s"
      dry_note "round $round: sleep ${ROUND_PAUSE}s before the next round"
      continue
    fi

    deploy="$(resolve_api_deploy || true)"
    desired="$(kubectl get deploy "$deploy" -n "$NAMESPACE" -o jsonpath='{.spec.replicas}' 2>/dev/null || true)"
    victim="$(pick_pod "$api_sel" || true)"
    if [ -z "$deploy" ] || [ -z "$victim" ] || ! is_positive_int "${desired:-x}"; then
      log "pod-kill round $round: no API deployment or running pod matches '$api_sel'"
      record pod-kill "$round" "-" "-" FAIL
      continue
    fi

    uid="$(kubectl get pod "$victim" -n "$NAMESPACE" -o jsonpath='{.metadata.uid}' 2>/dev/null || true)"
    log "pod-kill round $round: deleting pod/$victim (desired replicas $desired)"
    start="$(date +%s)"
    if ! act kubectl delete pod "$victim" -n "$NAMESPACE" --grace-period=0 --force; then
      record pod-kill "$round" "pod/$victim" "-" FAIL
      continue
    fi
    if rec="$(wait_recovered "deployment/$deploy" "$desired" "$start" "$TIMEOUT" "$uid" "$api_sel")"; then
      result=PASS
    else
      result=FAIL
    fi
    record pod-kill "$round" "pod/$victim" "$rec" "$result"
    if [ "$round" -lt "$ROUNDS" ]; then
      sleep "$ROUND_PAUSE"
    fi
  done
}

# exp_node_drain: drain the worker that runs the most app pods, measure recovery, then uncordon.
exp_node_drain() {
  local api_sel="$SELECTOR,$API_COMPONENT" round node deploy desired start rec result
  for (( round = 1; round <= ROUNDS; round++ )); do
    if [ "$DRY_RUN" -eq 1 ]; then
      dry_note "round $round: kubectl get nodes -l '!node-role.kubernetes.io/control-plane' -o jsonpath='{.items[*].metadata.name}'"
      dry_note "round $round: kubectl get pods -n $NAMESPACE -l '$SELECTOR' -o jsonpath='{.items[*].spec.nodeName}', then pick the worker with the most pods"
      node="<busiest-worker>"
      DRAIN_NODE="$node"
      act kubectl drain "$node" --ignore-daemonsets --delete-emptydir-data --timeout="$DRAIN_TIMEOUT"
      dry_note "round $round: poll deploy/<api> until readyReplicas == desired, timeout ${TIMEOUT}s"
      uncordon_node
      continue
    fi

    if ! node="$(pick_drain_node)" || [ -z "$node" ]; then
      log "node-drain round $round: no worker node found"
      record node-drain "$round" "-" "-" FAIL
      continue
    fi
    DRAIN_NODE="$node"
    deploy="$(resolve_api_deploy || true)"
    desired="$(kubectl get deploy "$deploy" -n "$NAMESPACE" -o jsonpath='{.spec.replicas}' 2>/dev/null || true)"

    log "node-drain round $round: draining node/$node"
    start="$(date +%s)"
    if act kubectl drain "$node" --ignore-daemonsets --delete-emptydir-data --timeout="$DRAIN_TIMEOUT"; then
      if [ -n "$deploy" ] && is_positive_int "${desired:-x}"; then
        if rec="$(wait_recovered "deployment/$deploy" "$desired" "$start" "$TIMEOUT" "" "$api_sel")"; then
          result=PASS
        else
          result=FAIL
        fi
      else
        rec="-"
        result=FAIL
      fi
    else
      rec="-"
      result=FAIL
    fi
    uncordon_node
    record node-drain "$round" "node/$node" "$rec" "$result"
    if [ "$round" -lt "$ROUNDS" ]; then
      sleep "$ROUND_PAUSE"
    fi
  done
}

# exp_db_kill: delete the postgres pod per round and wait for its StatefulSet to be ready again.
exp_db_kill() {
  local round sts desired pod uid start rec result
  for (( round = 1; round <= ROUNDS; round++ )); do
    if [ "$DRY_RUN" -eq 1 ]; then
      dry_note "round $round: kubectl get statefulset -n $NAMESPACE -l '$POSTGRES_COMPONENT' -o jsonpath='{.items[0].metadata.name}'"
      dry_note "round $round: kubectl get pods -n $NAMESPACE -l '$POSTGRES_COMPONENT' -o jsonpath='{.items[0].metadata.name}'"
      act kubectl delete pod "<postgres-pod>" -n "$NAMESPACE"
      dry_note "round $round: poll statefulset/<postgres> until readyReplicas == desired, timeout ${TIMEOUT}s"
      dry_note "round $round: sleep ${ROUND_PAUSE}s before the next round"
      continue
    fi

    sts="$(kubectl get statefulset -n "$NAMESPACE" -l "$POSTGRES_COMPONENT" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
    desired="$(kubectl get statefulset "$sts" -n "$NAMESPACE" -o jsonpath='{.spec.replicas}' 2>/dev/null || true)"
    pod="$(kubectl get pods -n "$NAMESPACE" -l "$POSTGRES_COMPONENT" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
    if [ -z "$sts" ] || [ -z "$pod" ] || ! is_positive_int "${desired:-x}"; then
      log "db-kill round $round: no postgres StatefulSet or pod found"
      record db-kill "$round" "-" "-" FAIL
      continue
    fi

    uid="$(kubectl get pod "$pod" -n "$NAMESPACE" -o jsonpath='{.metadata.uid}' 2>/dev/null || true)"
    log "db-kill round $round: deleting pod/$pod"
    start="$(date +%s)"
    if ! act kubectl delete pod "$pod" -n "$NAMESPACE"; then
      record db-kill "$round" "pod/$pod" "-" FAIL
      continue
    fi
    if rec="$(wait_recovered "statefulset/$sts" "$desired" "$start" "$TIMEOUT" "$uid" "$SELECTOR,$POSTGRES_COMPONENT")"; then
      result=PASS
    else
      result=FAIL
    fi
    record db-kill "$round" "pod/$pod" "$rec" "$result"
    if [ "$round" -lt "$ROUNDS" ]; then
      sleep "$ROUND_PAUSE"
    fi
  done
}

# run_selected: run the chosen experiments in order.
run_selected() {
  case "$EXPERIMENT" in
    pod-kill) exp_pod_kill ;;
    node-drain) exp_node_drain ;;
    db-kill) exp_db_kill ;;
    all)
      exp_pod_kill
      exp_node_drain
      exp_db_kill
      ;;
  esac
}

# mean_interval CSV: print the mean gap in seconds between consecutive probe timestamps, or n/a
# when there are fewer than two probes.
mean_interval() {
  awk -F, '
    NF == 2 {
      if (n) sum += $1 - prev
      prev = $1
      n++
    }
    END {
      if (n > 1) printf "%.3f\n", sum / (n - 1) / 1000
      else print "n/a"
    }
  ' "$1"
}

# probe_summary CSV: print "total bad longest_seconds" from the probe CSV.
# A failure window runs from the first non-200 probe to the next 200 probe. If the run ends in a
# failure, the window runs to the last failed probe.
probe_summary() {
  awk -F, '
    NF == 2 {
      total++
      if ($2 != "200") {
        bad++
        if (!inrun) { inrun = 1; start = $1 }
        last = $1
      } else if (inrun) {
        len = ($1 - start) / 1000
        if (len > longest) longest = len
        inrun = 0
      }
    }
    END {
      if (inrun) {
        len = (last - start) / 1000
        if (len > longest) longest = len
      }
      printf "%d %d %.1f\n", total + 0, bad + 0, longest + 0
    }
  ' "$1"
}

# write_report: write proof/chaos/chaos-report-<ts>.md from the measured results.
write_report() {
  local report="$REPO/proof/chaos/chaos-report-$RUN_TS.md" total bad longest version_out cluster_out
  [ -f "$CSV_FILE" ] || : > "$CSV_FILE"
  read -r total bad longest <<< "$(probe_summary "$CSV_FILE")"
  local mean measured
  mean="$(mean_interval "$CSV_FILE")"
  if [ "$mean" = "n/a" ]; then
    measured="not available (fewer than two probes)"
  else
    measured="$mean s"
  fi
  version_out="$(kubectl version 2>&1 || true)"
  cluster_out="$(kubectl cluster-info 2>&1 | head -n 2 || true)"

  {
    echo "# Chaos report $RUN_TS"
    echo
    echo "Every number in this report was measured during the run. Nothing is estimated or a placeholder."
    echo
    echo "- Generated (UTC): $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "- Experiments: $EXPERIMENT, rounds per experiment: $ROUNDS, recovery timeout: ${TIMEOUT}s"
    echo "- Namespace: \`$NAMESPACE\`, selector: \`$SELECTOR\`, API pods: \`$SELECTOR,$API_COMPONENT\`"
    echo "- Probe: \`curl --max-time 1 $URL\`, target interval ${PROBE_INTERVAL}s, measured mean interval $measured"
    echo "- recovery_seconds has a resolution of 1 s, measured from the disruption to the recovered state."
    echo
    echo "## Results"
    echo
    echo "| experiment | round | target | recovery_seconds | result |"
    echo "|---|---|---|---|---|"
    if [ -s "$RESULTS_FILE" ]; then
      while IFS=$'\t' read -r e r t rec res; do
        echo "| $e | $r | $t | $rec | $res |"
      done < "$RESULTS_FILE"
    else
      echo "| - | - | - | - | no round produced a result |"
    fi
    echo
    echo "## Probe summary"
    echo
    echo "| metric | value |"
    echo "|---|---|"
    echo "| total probes | $total |"
    echo "| non-200 probes | $bad |"
    echo "| longest consecutive failure window (s) | $longest |"
    echo
    echo "## Pods before the run"
    echo
    echo '```'
    cat "$PODS_BEFORE_FILE"
    echo '```'
    echo
    echo "## Pods after the run"
    echo
    echo '```'
    cat "$PODS_AFTER_FILE"
    echo '```'
    echo
    echo "## Versions"
    echo
    echo '```'
    echo "$version_out"
    echo "$cluster_out"
    echo '```'
  } > "$report"

  log "report written: $report"
}

main() {
  plan
  if [ "$DRY_RUN" -eq 1 ]; then
    start_probe
    run_selected
    uncordon_node
    exit 0
  fi

  if ! need kubectl curl; then
    echo "chaos.sh needs kubectl and curl on PATH. Use --dry-run to print the plan without them." >&2
    exit 2
  fi
  if ! kubectl cluster-info >/dev/null 2>&1; then
    echo "The cluster is not reachable with the current kubeconfig. Run make cluster-up first." >&2
    exit 1
  fi

  RUN_TS="$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$REPO/proof/chaos"
  CSV_FILE="$REPO/proof/chaos/run-$RUN_TS.tmp"
  RESULTS_FILE="$REPO/proof/chaos/run-$RUN_TS.results.tmp"
  PODS_BEFORE_FILE="$REPO/proof/chaos/run-$RUN_TS.pods-before.tmp"
  PODS_AFTER_FILE="$REPO/proof/chaos/run-$RUN_TS.pods-after.tmp"
  : > "$CSV_FILE"
  : > "$RESULTS_FILE"

  trap cleanup EXIT

  kubectl get pods -n "$NAMESPACE" -o wide > "$PODS_BEFORE_FILE" 2>&1 || true
  start_probe
  run_selected
  stop_probe
  uncordon_node
  kubectl get pods -n "$NAMESPACE" -o wide > "$PODS_AFTER_FILE" 2>&1 || true
  write_report

  if [ "$FAILURES" -gt 0 ]; then
    log "$FAILURES round(s) did not recover within ${TIMEOUT}s"
    exit 1
  fi
  log "all rounds recovered"
}

main
