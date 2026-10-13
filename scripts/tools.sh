#!/usr/bin/env bash
# Build helm and kubeconform into .tools/bin with Go, stamping the pinned versions.
# A binary is reused only when it reports the pinned version; otherwise it is rebuilt.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

go_env

if ! need go; then
  echo "Go is required to build the project tools. Install Go (1.26 or newer), then re-run: make tools" >&2
  exit 2
fi

mkdir -p "$REPO/.tools/bin" "$GOCACHE" "$GOPATH"

# has_version <binary> <command...> <expected>: true when the binary's output contains <expected>.
# The last argument is the expected version string; the rest is the command to run.
has_version() {
  local bin="$1" expected="${*: -1}"
  local args=("${@:2:$#-2}")
  [ -x "$bin" ] || return 1
  "$bin" "${args[@]}" 2>&1 | grep -qF "$expected"
}

# Helm's Makefile stamps the Kubernetes version that `helm template` assumes. Without it the
# default is v1.20.0, which fails the chart's kubeVersion (>=1.28.0-0). Match the pinned kind node.
K8S_MINOR="$(sed -nE 's#^kindest/node:v1\.([0-9]+)\..*#\1#p' <<< "$KIND_NODE_IMAGE")"
if ! [[ "$K8S_MINOR" =~ ^[0-9]+$ ]] || [ "$K8S_MINOR" -lt 28 ]; then
  echo "Cannot read a Kubernetes minor >= 28 from KIND_NODE_IMAGE='$KIND_NODE_IMAGE'" >&2
  exit 2
fi
HELM_LDFLAGS=(
  "-X helm.sh/helm/v3/internal/version.version=$HELM_VERSION"
  "-X helm.sh/helm/v3/pkg/chartutil.k8sVersionMajor=1"
  "-X helm.sh/helm/v3/pkg/chartutil.k8sVersionMinor=$K8S_MINOR"
  "-X helm.sh/helm/v3/pkg/lint/rules.k8sVersionMajor=1"
  "-X helm.sh/helm/v3/pkg/lint/rules.k8sVersionMinor=$K8S_MINOR"
)

# helm_stamped: true when the built binary carries the Kubernetes stamp. `helm version` does not
# show it, so read the -ldflags that go embeds in the binary's build info.
helm_stamped() {
  go version -m "$REPO/.tools/bin/helm" 2>/dev/null | grep -qF "chartutil.k8sVersionMinor=$K8S_MINOR"
}

if has_version "$REPO/.tools/bin/helm" version --short "$HELM_VERSION" && helm_stamped; then
  log "skip: helm $HELM_VERSION (kube $K8S_MINOR) already present at .tools/bin/helm"
else
  log "building helm $HELM_VERSION stamped for Kubernetes 1.$K8S_MINOR"
  rm -f "$REPO/.tools/bin/helm"
  GOBIN="$REPO/.tools/bin" go install \
    -ldflags "${HELM_LDFLAGS[*]}" \
    "helm.sh/helm/v3/cmd/helm@$HELM_VERSION"
fi

if has_version "$REPO/.tools/bin/kubeconform" -v "$KUBECONFORM_VERSION"; then
  log "skip: kubeconform $KUBECONFORM_VERSION already present at .tools/bin/kubeconform"
else
  log "building kubeconform $KUBECONFORM_VERSION"
  rm -f "$REPO/.tools/bin/kubeconform"
  GOBIN="$REPO/.tools/bin" go install \
    -ldflags "-X main.version=$KUBECONFORM_VERSION" \
    "github.com/yannh/kubeconform/cmd/kubeconform@$KUBECONFORM_VERSION"
fi

echo "--- installed versions ---"
"$REPO/.tools/bin/helm" version --short
"$REPO/.tools/bin/kubeconform" -v
