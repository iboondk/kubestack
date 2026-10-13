# ADR-0002: Argo CD with an app-of-apps and sync waves

- **Status:** Accepted (2026-10-11)

## Context

The cluster needs several platform components and the application, in a fixed order. Installing them by hand with `helm install` would make the order and the versions hard to review.

## Decision

Bootstrap Argo CD v3.5.4 from its upstream `install.yaml`. Apply one root Application, `kubestack-root`, which points at `argocd/apps/`. That path holds six child Applications, ordered with `argocd.argoproj.io/sync-wave`:

- wave -3: sealed-secrets, metrics-server
- wave -2: kube-prometheus-stack
- wave -1: traefik, prometheus-adapter
- wave 0: tenantly

Upstream charts are referenced by `chart:` and a pinned `targetRevision`. Values that live in this repo come in through a second source, `ref: values`. Nothing upstream is vendored. A health patch in `argocd/bootstrap/argocd-cm-patch.yaml` makes waves wait until each child is Healthy.

## Consequences

- The Git repo is the single source of what runs. Pins are checked by `tests/test_argocd.py` against `versions.env`.
- Argo CD needs its health check for `argoproj.io/Application`, or the waves do not wait. The bootstrap script applies the patch.
- The repo URL defaults to `https://github.com/iboondk/kubestack.git`. A fork changes it with `make repo-url`.
- Nothing has been synced on a live cluster in this repo yet. The Argo wave test checks the files, not the running system.
