#!/usr/bin/env bash
# Load test with hey, run in a one-off pod inside the cluster.
# The requests go through Traefik, so they count toward traefik_service_requests_total and
# therefore toward the tenantly_requests_per_second External metric that drives the HPA.
#
# Usage: scripts/load.sh [--rps N] [--duration 120s] [--concurrency 20] [--watch]
#   --rps N          total requests per second. hey gets -q N/concurrency per worker.
#                    Without it, hey sends as fast as it can.
#   --duration D     hey -z value, for example 120s or 2m
#   --concurrency C  hey -c value, the number of workers
#   --watch          also run "kubectl get hpa -n tenantly -w" in the background during the load
#
# Exit codes: 0 hey finished, 1 kubectl run failed, 2 bad usage or kubectl is missing.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TARGET="http://traefik.traefik.svc.cluster.local/healthz"
NAMESPACE=tenantly
RPS=""
DURATION=120s
CONCURRENCY=20
WATCH=0
WATCH_PID=""

usage() {
  cat >&2 <<'USAGE'
Usage: scripts/load.sh [--rps N] [--duration 120s] [--concurrency 20] [--watch]
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

while [ "$#" -gt 0 ]; do
  case "$1" in
    --rps) need_value "$@"; RPS="$2"; shift 2 ;;
    --duration) need_value "$@"; DURATION="$2"; shift 2 ;;
    --concurrency) need_value "$@"; CONCURRENCY="$2"; shift 2 ;;
    --watch) WATCH=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

[[ "$DURATION" =~ ^[0-9]+[smh]$ ]] || { echo "--duration must look like 120s, 2m or 1h" >&2; exit 2; }
[[ "$CONCURRENCY" =~ ^[0-9]+$ ]] && [ "$CONCURRENCY" -ge 1 ] || { echo "--concurrency must be a positive integer" >&2; exit 2; }
if [ -n "$RPS" ]; then
  [[ "$RPS" =~ ^[0-9]+$ ]] && [ "$RPS" -ge 1 ] || { echo "--rps must be a positive integer" >&2; exit 2; }
fi

if ! need kubectl; then
  echo "load.sh needs kubectl on PATH. Install it, then re-run: make load" >&2
  exit 2
fi

cleanup() {
  if [ -n "$WATCH_PID" ]; then
    kill "$WATCH_PID" 2>/dev/null || true
    wait "$WATCH_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

HEY_ARGS=(-z "$DURATION" -c "$CONCURRENCY")
if [ -n "$RPS" ]; then
  per_worker="$(awk -v r="$RPS" -v c="$CONCURRENCY" 'BEGIN { printf "%.3f", r / c }')"
  HEY_ARGS+=(-q "$per_worker")
  log "target ${RPS} rps total, ${per_worker} rps per worker"
fi

if [ "$WATCH" -eq 1 ]; then
  log "watching the HPA in the background (kubectl get hpa -n $NAMESPACE -w)"
  kubectl get hpa -n "$NAMESPACE" -w &
  WATCH_PID=$!
fi

POD="hey-load-$(date +%s)"
log "running hey in pod $POD: duration $DURATION, concurrency $CONCURRENCY, target $TARGET"
if kubectl run "$POD" -n "$NAMESPACE" --rm -i --restart=Never --image="$HEY_IMAGE" -- \
     "${HEY_ARGS[@]}" "$TARGET"; then
  log "hey finished"
else
  log "kubectl run failed; see the output above"
  exit 1
fi
