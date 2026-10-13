#!/usr/bin/env bash
# Build tenantly-api:dev from the Tenantly API checkout (sibling directory) and load it into the kind cluster.
# Build context is the sibling Tenantly API checkout.
#
# Exit codes:
#   0  image built and loaded
#   2  docker or kind is missing
#   3  Tenantly API has no go.mod yet; use values-demo.yaml (podinfo)
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if ! need docker kind; then
  exit 2
fi

# Sibling checkout of the Tenantly API (one directory up from this repo).
TENANTLY_DIR="$(ls -d "$REPO"/../p[0-9]*-tenantly 2>/dev/null | head -1 || true)"

if [ -z "$TENANTLY_DIR" ] || [ ! -f "$TENANTLY_DIR/go.mod" ]; then
  echo "Tenantly API has no code yet; use values-demo.yaml (podinfo)"
  exit 3
fi

log "building tenantly-api:dev from $TENANTLY_DIR"
docker build -f "$REPO/images/tenantly/Dockerfile" -t tenantly-api:dev "$TENANTLY_DIR"

log "loading tenantly-api:dev into kind cluster $CLUSTER_NAME"
kind load docker-image tenantly-api:dev --name "$CLUSTER_NAME"
