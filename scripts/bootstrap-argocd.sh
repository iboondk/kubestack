#!/usr/bin/env bash
# Installs Argo CD into the kind cluster, applies the health patch and the root app.
# Usage: REPO_URL=https://github.com/<you>/<repo>.git bash scripts/bootstrap-argocd.sh
# Without REPO_URL the root app uses DEFAULT_REPO_URL from versions.env.
# Exit codes: 0 success, 1 failure, 2 a required tool is missing.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

need kubectl || exit 2

ARGO_NS=argocd
TARGET_REPO_URL="${REPO_URL:-$DEFAULT_REPO_URL}"
TMP_DIR="$REPO/.tools/tmp"
ROOT_APP_TMP="$TMP_DIR/root-app.yaml"

log "Creating namespace $ARGO_NS"
kubectl create ns "$ARGO_NS" --dry-run=client -o yaml | kubectl apply -f -

log "Installing Argo CD $ARGOCD_VERSION (server-side apply)"
kubectl apply -n "$ARGO_NS" --server-side --force-conflicts \
  -f "https://raw.githubusercontent.com/argoproj/argo-cd/$ARGOCD_VERSION/manifests/install.yaml"

log "Waiting up to 300s for Argo CD deployments to become available"
kubectl -n "$ARGO_NS" wait --for=condition=Available deployment --all --timeout=300s
# The application controller is a StatefulSet in Argo CD v3, so it needs its own wait.
kubectl -n "$ARGO_NS" rollout status statefulset/argocd-application-controller --timeout=300s

log "Applying the argocd-cm health patch for Application resources"
kubectl apply -n "$ARGO_NS" --server-side --force-conflicts -f "$REPO/argocd/bootstrap/argocd-cm-patch.yaml"

log "Applying the root app (repoURL: $TARGET_REPO_URL)"
mkdir -p "$TMP_DIR"
sed -E "s|repoURL: https://github.com/[^[:space:]]*\\.git|repoURL: $TARGET_REPO_URL|" \
  "$REPO/argocd/root-app.yaml" > "$ROOT_APP_TMP"
kubectl apply -f "$ROOT_APP_TMP"

log "Bootstrap done. Argo CD sync progresses in the background."
cat >&2 <<MSG
Initial admin password (the password itself is not printed here):
  kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
Argo CD UI (user admin):
  kubectl port-forward svc/argocd-server -n argocd 8443:443
  then open https://localhost:8443
MSG
