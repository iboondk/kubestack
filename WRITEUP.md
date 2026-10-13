# Kubestack: write-up

## Problem

A small API team wants its service to run the way production would: deployed from Git, scaled on load, with secrets kept out of plain text, metrics on a dashboard, and a known recovery time when a pod or node fails. Showing this usually means a cloud bill. Kubestack shows it on one machine, with no cloud account and no real credentials.

## Architecture

A kind cluster runs Argo CD, which syncs six child Applications from one root Application, in sync waves. Platform components come first: SealedSecrets, metrics-server, kube-prometheus-stack, Traefik and prometheus-adapter. The Tenantly Helm chart comes last. Prometheus scrapes Traefik, and prometheus-adapter exposes the request rate to the HPA, which also scales on CPU.

## Key decisions

- **kind, not a managed cluster.** It is free and pinned, and three nodes give the chaos test a node to drain (ADR-0001).
- **Argo CD with sync waves.** Git is the source of truth. Upstream charts are referenced by pinned version, not vendored (ADR-0002).
- **Requests per second measured at the edge.** The application has no metrics endpoint, and this project does not change the application repo, so Traefik counts requests per service (ADR-0003).
- **SealedSecrets.** Encrypted values are safe in Git. The repo holds only placeholders (ADR-0004).
- **Plain templates for dev Postgres and Redis.** This avoids a chart dependency whose image catalog changed (ADR-0005).

## Results

Only offline checks have run, because the build machine has no Docker, kind or kubectl.

- `make test`: 78 unit tests, all passing.
- `make validate`: 37 checks, all passing, covering syntax, version pins and placeholder-only secrets.
- `make lint`: helm lint passes for four value sets, and helm template renders three. kubeconform validated 37 of 45 resources and skipped 8 that have no schema.

No cluster run has happened, so autoscaling, chaos recovery times and Grafana panels are unmeasured. `proof/chaos/` stays empty until someone runs the chaos script with Docker. No benchmark numbers are claimed.

## What I would do next

1. Run the full demo on a machine with Docker, and commit the real chaos and load results to `proof/chaos/`.
2. Swap the podinfo fallback for the real Tenantly image, and check the config values against the Tenantly code.
3. Run kubeconform and the Helm steps in CI, not only locally.
4. Replace the dev database with a managed or operator-run one, with backups.
5. Replace the RPS target of 20 per pod with a measured value.
