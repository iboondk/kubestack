# ADR-0004: SealedSecrets for secrets in Git

- **Status:** Accepted (2026-10-11)

## Context

The GitOps flow needs the app's secrets (database passwords, JWT key, OAuth client secret) in Git, but plain Secrets must never be committed. The repo must hold no real credentials.

## Decision

Use the sealed-secrets chart 2.20.0 (controller 0.40.0) and the `kubeseal` CLI. `scripts/seal-secrets.sh` reads the local `.env` and writes encrypted values to `charts/tenantly/values-sealed.yaml`, using `kubeseal --raw`. The committed `values-sealed.yaml` holds only placeholder ciphertext, marked `AgPLACEHOLDER`. `.env.example` holds only `change-me-` values, and a check in `tools/validate.py` enforces that.

For local development, `values-kind.yaml` uses `secrets.mode: plain` with the example values. The Argo app uses `secrets.mode: sealed`.

## Consequences

- Encrypted values are safe to commit. A sealed secret only decrypts in the cluster that sealed it, so a rebuilt cluster needs `make seal` again.
- The placeholder file does not decrypt. Until a user seals secrets for their own cluster, the sealed path cannot be used.
- The kubeseal step needs a running cluster, so it is not run in this repo's offline checks.
