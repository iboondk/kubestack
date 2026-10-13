#!/usr/bin/env bash
# Delete the kind cluster.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if ! need kind; then
  echo "cluster-down needs kind on PATH. Install it, then re-run: make down" >&2
  exit 2
fi

kind delete cluster --name "$CLUSTER_NAME"
