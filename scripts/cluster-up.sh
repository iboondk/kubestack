#!/usr/bin/env bash
# Create the kind cluster (if missing) and wait until every node is Ready.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if ! need docker kind kubectl; then
  echo "cluster-up needs docker, kind and kubectl on PATH. Install the missing tools, then re-run: make cluster-up" >&2
  exit 2
fi

if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  log "cluster '$CLUSTER_NAME' already exists, reusing it"
else
  log "creating cluster '$CLUSTER_NAME'"
  kind create cluster --config "$REPO/kind/cluster.yaml" --name "$CLUSTER_NAME"
fi

kubectl wait --for=condition=Ready nodes --all --timeout=180s
log "cluster '$CLUSTER_NAME' is ready"
