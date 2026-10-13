# ADR-0003: Measure requests per second at the Traefik edge

- **Status:** Accepted (2026-10-11)

## Context

The HPA should scale on requests per second as well as CPU. The Tenantly API (Postgres-backed, with OpenTelemetry traces) has no Prometheus `/metrics` endpoint, so it cannot report its own request rate. This project does not change the application repo.

## Decision

Traefik is the ingress. It counts requests per backend service in `traefik_service_requests_total`, and Prometheus scrapes it through a ServiceMonitor. prometheus-adapter turns this PromQL rule into an External metric:

```
sum(rate(traefik_service_requests_total{service=~"tenantly-.*"}[1m]))
```

The metric is `tenantly_requests_per_second`. The HPA uses `type: External` with an `AverageValue` target of 20 per pod, next to a 70% CPU target from metrics-server. ingress-nginx is not used, because its project has been retired upstream.

## Consequences

- The RPS signal works the same way for Tenantly and for the podinfo fallback, which is the demo's default values file.
- The number counts requests that reach Traefik, not requests the app handled. Traffic that bypasses Traefik is not counted.
- An empty or zero value is normal until traffic arrives. The runbook covers the `<unknown>` case.
- The target of 20 is a starting value. It is not based on a measured benchmark, and the README and writeup do not claim one.
