#!/usr/bin/env bash
# Seals the tenantly secrets from REPO/.env into charts/tenantly/values-sealed.yaml.
# Reads only .env (never .env.example). Plaintext values are never printed.
# Usage: bash scripts/seal-secrets.sh
# Exit codes: 0 success, 1 failure, 2 a required tool is missing.
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

need kubeseal kubectl || exit 2

ENV_FILE="$REPO/.env"
OUT_FILE="$REPO/charts/tenantly/values-sealed.yaml"
SECRET_NAME=tenantly-secrets
SECRET_NS=tenantly
# Argo CD names the Helm release after the Application, so the release is "tenantly".
RELEASE=tenantly
SECRET_KEYS=(DATABASE_URL MIGRATE_DATABASE_URL JWT_SECRET OAUTH_CLIENT_SECRET POSTGRES_PASSWORD APP_DB_PASSWORD)

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE. Copy .env.example to .env and put real values there." >&2
  echo "This script refuses to read .env.example, which holds placeholders only." >&2
  exit 1
fi

# env_value KEY: print the value of KEY from .env without sourcing the file.
# Only the first "=" splits the line, so values may contain "=". Surrounding quotes are stripped.
env_value() {
  awk -v k="$1" '
    /^[[:space:]]*#/ { next }
    { line = $0; sub(/\r$/, "", line) }
    index(line, k "=") == 1 {
      v = substr(line, length(k) + 2)
      if (v ~ /^".*"$/ || v ~ /^'\''.*'\''$/) v = substr(v, 2, length(v) - 2)
      print v
      exit
    }
  ' "$ENV_FILE"
}

# url_query_escape: same encoding as Go's urlquery (url.QueryEscape), which the chart uses
# for the passwords in DATABASE_URL and MIGRATE_DATABASE_URL. Space becomes "+".
url_query_escape() {
  local input="$1" out="" i c hex
  local LC_ALL=C
  for (( i = 0; i < ${#input}; i++ )); do
    c="${input:i:1}"
    case "$c" in
      [a-zA-Z0-9.~_-]) out+="$c" ;;
      ' ') out+="+" ;;
      *) printf -v hex '%%%02X' "'$c"; out+="$hex" ;;
    esac
  done
  printf '%s' "$out"
}

APP_DB_PASSWORD="$(env_value APP_DB_PASSWORD)"
POSTGRES_PASSWORD="$(env_value POSTGRES_PASSWORD)"
JWT_SECRET="$(env_value JWT_SECRET)"
OAUTH_CLIENT_SECRET="$(env_value OAUTH_CLIENT_SECRET)"

for name in APP_DB_PASSWORD POSTGRES_PASSWORD JWT_SECRET OAUTH_CLIENT_SECRET; do
  if [ -z "${!name}" ]; then
    echo "$name is empty or missing in $ENV_FILE" >&2
    exit 1
  fi
done

# Same shapes as charts/tenantly/templates/secret.yaml, with sslmode=disable as in the chart.
DATABASE_URL="postgres://tenantly_app:$(url_query_escape "$APP_DB_PASSWORD")@${RELEASE}-postgres:5432/tenantly?sslmode=disable"
MIGRATE_DATABASE_URL="postgres://postgres:$(url_query_escape "$POSTGRES_PASSWORD")@${RELEASE}-postgres:5432/tenantly?sslmode=disable"

# plain_value KEY: the plaintext for one secret key. Only used as a pipe into kubeseal.
plain_value() {
  case "$1" in
    DATABASE_URL) printf '%s' "$DATABASE_URL" ;;
    MIGRATE_DATABASE_URL) printf '%s' "$MIGRATE_DATABASE_URL" ;;
    JWT_SECRET) printf '%s' "$JWT_SECRET" ;;
    OAUTH_CLIENT_SECRET) printf '%s' "$OAUTH_CLIENT_SECRET" ;;
    POSTGRES_PASSWORD) printf '%s' "$POSTGRES_PASSWORD" ;;
    APP_DB_PASSWORD) printf '%s' "$APP_DB_PASSWORD" ;;
    *) return 1 ;;
  esac
}

# Indexed arrays only: macOS ships bash 3.2, which has no associative arrays.
SEALED_LINES=()
for key in "${SECRET_KEYS[@]}"; do
  log "Sealing $key"
  if ! ciphertext="$(plain_value "$key" | kubeseal --raw \
        --namespace "$SECRET_NS" --name "$SECRET_NAME" \
        --controller-name sealed-secrets-controller --controller-namespace kube-system)"; then
    echo "kubeseal failed for $key. Is the sealed-secrets controller running in the cluster?" >&2
    exit 1
  fi
  SEALED_LINES+=("$key=$ciphertext")
done
unset APP_DB_PASSWORD POSTGRES_PASSWORD JWT_SECRET OAUTH_CLIENT_SECRET DATABASE_URL MIGRATE_DATABASE_URL

TMP_OUT="$OUT_FILE.tmp"
{
  cat <<'HEADER'
# Overlay that reads secrets from a SealedSecret. Written by scripts/seal-secrets.sh.
# The ciphertext below is bound to namespace tenantly and secret name tenantly-secrets.
# It works only with the sealed-secrets controller of the cluster that sealed it.
secrets:
  # sealed: render a SealedSecret instead of a plain Secret.
  mode: sealed

sealedSecrets:
  encryptedData:
HEADER
  for line in "${SEALED_LINES[@]}"; do
    printf '    %s: "%s"\n' "${line%%=*}" "${line#*=}"
  done
} > "$TMP_OUT"
mv "$TMP_OUT" "$OUT_FILE"
log "Wrote $OUT_FILE with ${#SECRET_KEYS[@]} sealed keys"
