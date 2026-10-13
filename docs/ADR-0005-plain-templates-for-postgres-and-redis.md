# ADR-0005: Plain templates for dev Postgres and Redis

- **Status:** Accepted (2026-10-11)

## Context

The demo needs a database and a cache for the API. The Bitnami charts are the usual choice, but their 2025 image catalog changes make pinned versions harder to keep.

## Decision

Write the dev Postgres StatefulSet (`postgres:16-alpine`, 1Gi PVC) and the Redis Deployment (`redis:7-alpine`) as plain templates inside the tenantly chart. The migrate Job is an Argo `Sync` hook at wave 1, and an init container waits for `pg_isready`.

## Consequences

- No extra chart dependency. Image tags come from `versions.env` and are checked by the tests.
- Dev only. There are no backups, no replicas and no failover, and the README says so.
- Production use would need a managed database or a maintained operator. That choice is outside this demo.
