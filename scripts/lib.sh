#!/usr/bin/env bash
# Shared helpers. Source this file from other scripts; do not run it directly.
set -euo pipefail

# Repo root, resolved from this file's own location (scripts/ -> ..).
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export REPO

# Load pinned versions (plain KEY=value lines) and export every key.
set -a
# shellcheck disable=SC1091
source "$REPO/versions.env"
set +a

# Project-local tools win over anything on the system PATH.
export PATH="$REPO/.tools/bin:$PATH"

# log: timestamped line on stderr.
log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$*" >&2
}

# need <tool>...: returns 1 and prints MISSING: <tool> for each tool not on PATH.
need() {
  local tool missing=0
  for tool in "$@"; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      echo "MISSING: $tool" >&2
      missing=1
    fi
  done
  return "$missing"
}

# go_env: set up the Go environment per docs/architecture.md section 2.
# - GOMODCACHE: an exported GOMODCACHE wins. Otherwise /Volumes/Hazel/caches/go when that volume
#   exists (the build machine), else .tools/gomodcache inside the repo.
# - GOCACHE, GOPATH and TMPDIR live under the repo's git-ignored .tools/ directory.
# - Deviation from the spec: TMPDIR is not in the spec. It is set to .tools/tmp so temp files
#   stay inside the repo and out of the system temp directory.
go_env() {
  if [[ -z "${GOMODCACHE:-}" ]]; then
    if [[ -d /Volumes/Hazel ]]; then
      GOMODCACHE=/Volumes/Hazel/caches/go
    else
      GOMODCACHE="$REPO/.tools/gomodcache"
    fi
  fi
  export GOMODCACHE
  export TMPDIR="$REPO/.tools/tmp"
  mkdir -p "$TMPDIR"
  export GOCACHE="$REPO/.tools/go-build"
  export GOPATH="$REPO/.tools/gopath"
  export GOFLAGS=-mod=mod
}
