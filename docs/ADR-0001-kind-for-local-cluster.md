# ADR-0001: Use kind for the local cluster

- **Status:** Accepted (2026-10-11)

## Context

The demo must run on one developer machine, cost nothing, and show pods being rescheduled when a node drains. It must not touch a cloud account.

## Decision

Use [kind](https://kind.sigs.k8s.io/) v0.33.0 with the node image `kindest/node:v1.36.4`. The cluster `kubestack` has one control-plane node and two workers (`kind/cluster.yaml`). Three host ports (30080, 30300, 30090) are mapped to 127.0.0.1 only.

## Consequences

- Free, local, and reproducible from a pinned file.
- Two workers give the node-drain experiment a real target.
- kind needs Docker. Machines without Docker can run only the offline checks (`make test`, `make lint`, `make chaos-dry`). The runbook says so.
- kind is not a production distribution. The demo does not claim anything about production clusters.
