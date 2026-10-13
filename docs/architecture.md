# Kubestack architecture

Kubestack runs the Tenantly API ([github.com/iboondk/tenantly](https://github.com/iboondk/tenantly)) on a local Kubernetes cluster:

- **Cluster:** kind.
- **Packaging:** a Helm chart.
- **Delivery:** Argo CD, using the app-of-apps pattern (GitOps).
- **Autoscaling:** an HPA on CPU and on requests per second (RPS).
- **Secrets:** SealedSecrets.
- **Monitoring:** Prometheus and Grafana dashboards.
- **Resilience test:** a chaos script that kills pods and measures recovery.

Everything runs on the local machine. Nothing is deployed to a cloud, nothing costs money, and no real credentials are used. Only `.env.example` holds placeholders.

Verification artifacts live in `proof/`:

1. `proof/helm-lint.txt` and `proof/helm-template-*.yaml`: output from a Helm binary built locally with Go.
2. `proof/kubeconform.txt`: schema validation of the rendered manifests.
3. `charts/tenantly/dashboards/*.json`: the Grafana dashboards.
4. `scripts/chaos.sh` and `docs/RUNBOOK.md`. A chaos report in `proof/chaos/` exists only after someone runs it on a cluster.

---

## 1. Architecture

```
 git repo (this folder). Argo CD repoURL defaults to https://github.com/iboondk/kubestack.git
 and can be overridden with REPO_URL in scripts/bootstrap-argocd.sh.
                                   |
 kind cluster "kubestack": 1 control-plane + 2 workers (kindest/node v1.36.4)
 +----------------------------------------------------------------------------------+
 | ns argocd     Argo CD v3.5.4 (install.yaml, server-side apply)                   |
 |               argocd-cm patch: health check for argoproj.io/Application, so the  |
 |               sync waves of child apps wait until each child app is Healthy      |
 |               root Application "kubestack-root" -> path argocd/apps (app-of-apps) |
 |   wave -3  sealed-secrets 2.20.0      (ns kube-system)                           |
 |   wave -3  metrics-server 3.14.0      (--kubelet-insecure-tls, needed on kind)   |
 |   wave -2  kube-prometheus-stack 92.3.0 (ns monitoring; ServerSideApply=true)     |
 |   wave -1  traefik 41.7.1             (ns traefik; NodePort 30080; Prometheus     |
 |                                         metrics and a ServiceMonitor)             |
 |   wave -1  prometheus-adapter 5.3.0   (ns monitoring; external metric             |
 |                                         tenantly_requests_per_second)             |
 |   wave  0  tenantly (charts/tenantly from this repo, ns tenantly)                 |
 |                                                                                  |
 | ns tenantly   postgres StatefulSet (postgres:16-alpine, dev only, 1Gi PVC)       |
 |               redis Deployment (redis:7-alpine, dev only)                        |
 |               Job tenantly-migrate (Argo Sync hook, wave 1): migrate up, then     |
 |                   ALTER ROLE tenantly_app LOGIN PASSWORD from the secret          |
 |               Deployment tenantly-api (wave 2; replicas >= 2; topology spread)   |
 |               Service :80 -> containerPort; Ingress (class traefik, path /)      |
 |               HPA v2: CPU 70% avg AND External tenantly_requests_per_second      |
 |               PDB minAvailable 1; SealedSecret -> Secret tenantly-secrets        |
 |               ConfigMap tenantly-dashboards (label grafana_dashboard: "1")       |
 +----------------------------------------------------------------------------------+
   host:30080 -> traefik -> tenantly     host:30300 -> Grafana     host:30090 -> Prometheus
   (kind extraPortMappings, published on 127.0.0.1 only)
```

### How RPS autoscaling works, and why it uses Traefik

The Tenantly API ([github.com/iboondk/tenantly](https://github.com/iboondk/tenantly)) has OpenTelemetry traces but **no Prometheus `/metrics` endpoint**, so the app cannot report its own request rate. Kubestack does not modify the application, so RPS is measured at the edge instead:

1. Traefik counts requests per backend service in `traefik_service_requests_total`.
2. Prometheus scrapes Traefik through a ServiceMonitor.
3. prometheus-adapter has an `externalRules` entry: `sum(rate(traefik_service_requests_total{service=~"tenantly-.*"}[1m]))`. It exposes the result as the External metric `tenantly_requests_per_second`, which is not namespaced.
4. The HPA uses `type: External` with a `target.type: AverageValue` of 20 (per pod).
5. CPU usage comes from metrics-server.

This approach works the same way for Tenantly and for the podinfo fallback.

ingress-nginx is **not** used. Its last chart release was 4.15.1 in March 2026, and the project has been retired upstream. Traefik is actively maintained.

### Contract with the Tenantly API

Every Tenantly detail is a value in `values.yaml`, so nothing is hard-coded. The defaults below follow the Tenantly configuration (`internal/config/config.go` in the tenantly repo); override them if your checkout differs.

| What | Default | Values key |
|---|---|---|
| Image | `tenantly-api:dev`, built by `images/tenantly/Dockerfile` with build context set to a tenantly checkout (`TENANTLY_SRC`) and loaded with `kind load docker-image` | `image.repository`, `image.tag` |
| Binaries in the image | `/app/api` (cmd/api) and `/app/migrate` (cmd/migrate) | `api.command`, `migrate.command` |
| HTTP port | 8080 | `containerPort` |
| Liveness and readiness | `GET /healthz` (Tenantly has no `/readyz`) | `probes.*.path` |
| Plain env | `APP_ENV=dev`, `HTTP_ADDR=:8080`, `REDIS_URL=redis://tenantly-redis:6379/0`, `OTEL_EXPORTER=stdout` | `env` (map) |
| Secret env | `DATABASE_URL` (connects as `tenantly_app`), `MIGRATE_DATABASE_URL` (superuser), `JWT_SECRET`, `OAUTH_CLIENT_SECRET`, `POSTGRES_PASSWORD`, `APP_DB_PASSWORD` | `secrets.*` |

**Fallback.** `values-demo.yaml` switches to `ghcr.io/stefanprodan/podinfo:6.7.0` on port 9898, turns off postgres, redis and migrate, and checks `/healthz`. With it, the whole stack, the HPA and the chaos demo run without a Tenantly checkout.

---

## 2. Tech choices

| Concern | Choice (pinned) | Why |
|---|---|---|
| Local cluster | kind v0.33.0, node `kindest/node:v1.36.4` | Free and local. Three nodes, so the chaos test shows pods being rescheduled |
| Packaging | Helm 3 chart `charts/tenantly` (apiVersion v2) | Standard packaging for Kubernetes apps |
| Helm binary on this machine | `go build helm.sh/helm/v3/cmd/helm@v3.22.0` into `.tools/bin/` | helm is not installed, but Go 1.26.5 and the module proxy are. This gives **real** lint and template output |
| Schema validation | kubeconform v0.8.0, built the same way, with `-ignore-missing-schemas` for CRDs | Checks the rendered manifests offline |
| GitOps | Argo CD v3.5.4 (upstream `install.yaml`), app-of-apps, sync waves | Needs only kubectl to bootstrap |
| Upstream charts | Argo `Application` with `chart:` and a pinned `targetRevision` | No vendored charts. Versions are in `versions.env` |
| Ingress / edge metrics | Traefik chart 41.7.1 | Maintained, and has built-in Prometheus metrics |
| Secrets | sealed-secrets chart 2.20.0 (controller 0.40.0) and the `kubeseal` CLI | Encrypted values are safe to commit |
| Monitoring | kube-prometheus-stack 92.3.0 | Includes Prometheus Operator, Grafana with a dashboard sidecar, and kube-state-metrics |
| RPS to HPA | prometheus-adapter 5.3.0 (`externalRules`) | Turns PromQL into an External metric |
| CPU to HPA | metrics-server 3.14.0 | Required for resource metrics |
| Dev database and cache | `postgres:16-alpine` and `redis:7-alpine`, as plain templates | Bitnami charts are avoided because of their 2025 image catalog changes |
| Load | `hey` in a one-off pod (`williamyeh/hey`, pinned by digest in `versions.env`; the digest is the current `latest` tag, taken from Docker Hub on 2026-10-11) | Nothing to install on the host |
| Chaos | Bash plus kubectl | Easy to read. Measures real recovery time |
| Offline checks | Python 3.10 stdlib plus PyYAML (`tools/validate.py`, `unittest`) | Runs here |

Upstream chart and image versions were checked on 2026-10-11 against GitHub releases and the chart indexes. They live in **`versions.env`**, and every script and Argo app uses that file as the single source.

Environment rules for every script that uses Go:

- Set `GOMODCACHE=/Volumes/Hazel/caches/go`.
- Set `GOCACHE=$REPO/.tools/go-build` and `GOBIN`/output to `$REPO/.tools/bin`.
- Write nothing to the main drive.

---

## 3. Directory layout

```
kubestack/
  LICENSE                         MIT, "Copyright (c) 2026 iboondk"
  Makefile                        tools, cluster-up, image, bootstrap, seal, lint, validate,
                                  test, load, chaos, chaos-dry, down
  versions.env                    KIND_VERSION, KIND_NODE_IMAGE, HELM_VERSION, KUBECONFORM_VERSION,
                                  ARGOCD_VERSION, SEALED_SECRETS_CHART, METRICS_SERVER_CHART,
                                  KPS_CHART, TRAEFIK_CHART, PROM_ADAPTER_CHART, PODINFO_IMAGE,
                                  POSTGRES_IMAGE, REDIS_IMAGE, HEY_IMAGE
  .env.example                    placeholder secrets only
  .gitignore                      .env, .tools/, kubeconfig*, *.tmp, __pycache__/, proof/chaos/*.tmp
  kind/cluster.yaml
  images/tenantly/Dockerfile      multi-stage Go build of a tenantly checkout (cmd/api, cmd/migrate)
  scripts/
    lib.sh                        shared helpers: load versions.env, need <tool>, log, REPO root
    tools.sh                      builds helm and kubeconform into .tools/bin with Go
    cluster-up.sh  cluster-down.sh
    build-image.sh                docker build Tenantly and kind load (falls back to a demo-values hint)
    bootstrap-argocd.sh           installs Argo CD, patches argocd-cm, applies the root app
    seal-secrets.sh               .env -> charts/tenantly/values-sealed.yaml (kubeseal --raw)
    load.sh                       hey job against http://traefik.traefik.svc/ (host tenantly.local)
    chaos.sh                      pod-kill / node-drain / db-kill experiments with a recovery report
    lint.sh                       helm lint and template, kubeconform, validate.py -> proof/
  charts/tenantly/
    Chart.yaml  values.yaml  values-kind.yaml  values-demo.yaml  values-sealed.yaml  .helmignore
    dashboards/tenantly-overview.json  dashboards/tenantly-autoscaling.json
    templates/_helpers.tpl  serviceaccount.yaml  configmap.yaml  secret.yaml  sealedsecret.yaml
              deployment.yaml  service.yaml  ingress.yaml  hpa.yaml  pdb.yaml
              postgres.yaml  redis.yaml  migrate-job.yaml  servicemonitor.yaml
              dashboards-configmap.yaml  NOTES.txt
  argocd/
    bootstrap/argocd-cm-patch.yaml
    root-app.yaml
    apps/sealed-secrets.yaml  metrics-server.yaml  kube-prometheus-stack.yaml
         traefik.yaml  prometheus-adapter.yaml  tenantly.yaml
  platform/
    metrics-server-values.yaml  kube-prometheus-stack-values.yaml
    traefik-values.yaml  prometheus-adapter-values.yaml
  tools/validate.py
  tests/test_validate.py  test_chart_static.py  test_argocd.py  test_dashboards.py  test_scripts.py  test_chaos_recovery.py
  docs/RUNBOOK.md  docs/architecture.md  docs/ADR-*.md
  proof/                          helm-lint.txt, helm-template-{default,kind,demo}.yaml,
                                  kubeconform.txt, validate.txt, unittest.txt, chaos/ (real runs only)
```

Argo Applications that point at upstream charts and need values from this repo use Argo CD **multi-source** apps: `sources:` holds the chart plus `ref: values` pointing at this repo, and `valueFiles` uses `$values/platform/<x>-values.yaml`.

The Argo CD UI has no NodePort. Reach it with `kubectl port-forward svc/argocd-server -n argocd 8443:443`, then open https://localhost:8443.

---

## 4. Development environment notes (checked 2026-10-11)

The chart and scripts were developed on a machine with the following tools.

| Tool | Status |
|---|---|
| docker, kind, kubectl, helm, kubeseal, argocd, kubeconform, shellcheck, yamllint | **missing** |
| go 1.26.5 (module proxy reachable), python3 3.10 + PyYAML 6.0.3, jq | present |
| Disk | main drive has 5.9 GiB free; `/Volumes/Hazel` has 79 GiB free. Everything stays on Hazel |

What this means:

- **Can run here:**
  - `helm lint` and `helm template`, using the Go-built helm.
  - kubeconform. It needs network access to fetch schemas, and a failure is recorded.
  - YAML and JSON checks, `bash -n`, the unit tests, and `chaos.sh --dry-run`.
- **Cannot run here (no docker):**
  - kind, the image build, the Argo sync, sealing secrets, HPA scaling and real chaos runs.
  - The runbook explains how to run them on a machine with Docker. No chaos timings, replica counts or Grafana screenshots are made up. `proof/chaos/` stays empty, apart from `.gitkeep`, until someone does a real run.

---

## 5. Risks and how they are handled

- **The Tenantly checkout may be missing or differ.** Every Tenantly detail is a value, and `values-demo.yaml` (podinfo) keeps the stack demoable.
- **The app has no `/metrics`.** RPS comes from Traefik, and the app ServiceMonitor is off by default (`metrics.enabled: false`). The demo values turn it on for podinfo.
- **SealedSecrets belong to one cluster.** The committed `values-sealed.yaml` holds placeholder ciphertext (`AgPLACEHOLDER`). Until `seal-secrets.sh` runs, `values-kind.yaml` uses `secrets.mode: plain` with values copied from `.env.example`. These are dev placeholders, never real credentials. The Argo app uses `secrets.mode: sealed`, and the runbook says to seal secrets and commit them to your own fork first.
- **Argo waves on child apps.** Argo CD needs an Application health check for these, which `argocd-cm-patch.yaml` adds.
- **kube-prometheus-stack CRDs are large.** The app sets `ServerSideApply=true`, plus `Replace=false` and `SkipDryRunOnMissingResource=true` on the tenantly app, because it creates a ServiceMonitor.
- **Migration order.** The migrate Job is an Argo `Sync` hook at wave 1, after postgres at wave 0. It uses `BeforeHookCreation`. An init container waits for `pg_isready`. Under plain `helm install`, it runs as an ordinary Job.
- **Repo URL.** The default is `https://github.com/iboondk/kubestack.git`, `REPO_URL=... scripts/bootstrap-argocd.sh` overrides it at apply time, and the files in git are left unchanged. The Makefile `repo-url` target, the Argo apps and `versions.env` all use this value.
