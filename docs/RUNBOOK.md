# Kubestack demo runbook

This runbook shows the whole demo on your own machine: a kind cluster, Argo CD syncing a Helm chart from your fork, the Tenantly API behind Traefik, autoscaling on CPU and requests per second, Grafana dashboards, and a chaos test with a recovery report.

Everything runs locally. Nothing is deployed to a cloud and nothing costs money. The only secrets are the ones you create in your own `.env`, which git ignores.

## Prerequisites

Install these on the machine that runs the demo:

- **Docker**, with at least **8 GB of RAM** given to Docker. The cluster has three nodes.
- **kind v0.33.0**, the Kubernetes-in-Docker tool.
- **kubectl**, any recent version.
- **kubeseal**, the SealedSecrets CLI. The controller version is pinned in `versions.env`.
- **Go**, 1.26 or newer. The project builds `helm` and `kubeconform` with it. Run `make tools` to build both into `.tools/bin/`.
- **git**, and a GitHub account for your fork.

The pinned versions live in `versions.env`. Every script reads that file.

## Step 1: create the cluster

```bash
make cluster-up
```

This creates the kind cluster `kubestack` from `kind/cluster.yaml`. It has one control-plane node and two workers. Host ports 30080 (the app through Traefik), 30300 (Grafana) and 30090 (Prometheus) are published on `127.0.0.1` only. The command waits until every node is Ready.

## Step 2: the application image

Tenantly API source lives in its own repo. Check it out next to this one, at `../tenantly`, or set `TENANTLY_SRC` to its path.

**If the Tenantly source is present**, build the image and load it into kind:

```bash
make image
```

`make image` runs `docker build` on `$TENANTLY_SRC` (default `../tenantly`) and then `kind load docker-image tenantly-api:dev`.

**If the Tenantly source is missing**, `make image` stops with the message `Tenantly source not found at <path>; use values-demo.yaml (podinfo)`. Use the demo values instead. The demo runs podinfo, a small public HTTP server, in place of Tenantly. It has the same port layout, health path and metrics endpoint, so the autoscaling and chaos parts of the demo work unchanged.

To switch to the demo values, edit `argocd/apps/tenantly.yaml` in your fork. Under `spec.source.helm.valueFiles`, replace `values-kind.yaml` with `values-demo.yaml`, so the list reads:

```yaml
valueFiles:
  - values-demo.yaml
  - values-sealed.yaml
```

Keep `values-sealed.yaml`. The Secret that the Deployment reads comes from the SealedSecret, even in the demo. Step 5 covers it.

## Step 3: fork and point Argo CD at your fork

1. Fork `https://github.com/iboondk/kubestack` on GitHub.
2. Rewrite the repo URL in the Argo CD apps to your fork:

   ```bash
   make repo-url REPO_URL=https://github.com/<you>/kubestack.git
   ```

   This edits `argocd/root-app.yaml` and `argocd/apps/*.yaml`.
3. Commit and push to your fork's `main` branch:

   ```bash
   git add -A
   git commit -m "Point Argo CD at my fork"
   git push origin main
   ```

Argo CD reads the repo from git, so it sees only what you have pushed.

## Step 4: bootstrap Argo CD

```bash
make bootstrap
```

The script installs Argo CD v3.5.4 from its upstream manifest, applies the health patch in `argocd/bootstrap/argocd-cm-patch.yaml`, and applies the root Application `kubestack-root`. The root Application syncs the six child Applications in `argocd/apps/`, in sync waves:

| Wave | App |
|---|---|
| -3 | sealed-secrets, metrics-server |
| -2 | kube-prometheus-stack |
| -1 | traefik, prometheus-adapter |
| 0 | tenantly |

Watch the apps come up:

```bash
kubectl get applications -n argocd -w
```

To open the Argo CD UI, run the port-forward in a second terminal:

```bash
kubectl port-forward svc/argocd-server -n argocd 8443:443
```

Then open `https://localhost:8443` and log in as `admin`. The initial password is printed by the bootstrap script. To read it yourself:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
```

The `tenantly` app stays Degraded or Progressing until Step 5 is done. That is expected.

## Step 5: secrets

Secrets never go into git in plain text. They are encrypted with `kubeseal` against the public key of your cluster's SealedSecrets controller.

1. Create your local secrets file:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and replace every `change-me-...` value with your own. `.env` is listed in `.gitignore`. `.env.example` holds placeholders only and the seal script never reads it.
3. Seal the values:

   ```bash
   make seal
   ```

   This writes the encrypted values to `charts/tenantly/values-sealed.yaml`. Plain values are never printed.
4. Commit the sealed file and push it to your fork:

   ```bash
   git add charts/tenantly/values-sealed.yaml
   git commit -m "Add sealed secrets for my cluster"
   git push origin main
   ```

Argo CD does not sync the `tenantly` app until it sees the new commit. It polls the repo every few minutes, or you can press **Refresh** in the Argo CD UI. Once it syncs, the SealedSecret decrypts and the `tenantly-secrets` Secret appears in the `tenantly` namespace.

Sealed values only decrypt in the cluster that sealed them. If you rebuild the cluster, run `make seal` again.

## Step 6: verify the stack

Check the app through Traefik:

```bash
curl localhost:30080/healthz
```

Check that the external metric answers. Until Traefik has served some traffic, the value may be empty or zero:

```bash
kubectl get --raw /apis/external.metrics.k8s.io/v1beta1/namespaces/*/tenantly_requests_per_second
```

Check the autoscaler:

```bash
kubectl get hpa -n tenantly
```

The HPA has two metrics: CPU from metrics-server, and `tenantly_requests_per_second` from prometheus-adapter. If the external metric shows `<unknown>`, see the troubleshooting section.

## Step 7: Grafana

Open `http://localhost:30300` in a browser. Log in as `admin`. Get the password with:

```bash
kubectl get secret -n monitoring kube-prometheus-stack-grafana -o jsonpath='{.data.admin-password}' | base64 -d
```

The two dashboards are in the **Kubestack** folder. They come from `charts/tenantly/dashboards/`, which the Grafana sidecar loads from the `tenantly-dashboards` ConfigMap:

- **Tenantly overview**
- **Tenantly autoscaling**

## Step 8: autoscaling demo

Open the HPA in one terminal, and start the load in another:

```bash
make load
```

`make load` runs `scripts/load.sh` with the defaults: 120 seconds, 20 concurrent workers, and no rate limit. The load is [hey](https://github.com/rakyll/hey) running in a one-off pod inside the cluster. Its requests go through Traefik, so they count toward the request metric.

Useful variations:

```bash
scripts/load.sh --rps 200 --duration 180s --concurrency 20 --watch
```

- `--rps N` sets the total requests per second. The script gives hey `-q N/concurrency` per worker.
- `--watch` runs `kubectl get hpa -n tenantly -w` in the background while the load runs.

**What to expect.** While the load runs, the HPA's requests-per-second reading rises, and the number of Tenantly API replicas should increase, up to the HPA maximum. After the load stops, the replica count falls back down, more slowly than it rose, because the scale-down window is longer. The dashboards show the same change in request rate and replica count. The exact numbers depend on your machine and are not written here. The chaos report and your own load output are the only sources for numbers.

## Step 9: chaos

Check the plan first. This needs no cluster and no kubectl:

```bash
make chaos-dry
```

The dry run prints each kubectl command the real run would execute, prefixed `DRY-RUN:`. It writes no report.

Run the real experiments:

```bash
scripts/chaos.sh --experiment all
```

The three experiments are:

- **pod-kill**: force-deletes one random API pod per round and waits for the Deployment to have all its replicas ready again.
- **node-drain**: drains the worker node that runs the most app pods, measures recovery, and uncordons the node.
- **db-kill**: deletes the postgres pod and waits for the StatefulSet to be ready again.

Options: `--rounds 3`, `--timeout 180` (seconds), `--url http://localhost:30080/healthz`, `--namespace tenantly`, `--selector app.kubernetes.io/name=tenantly`.

The pod-kill and node-drain recovery checks add `app.kubernetes.io/component=api` to `--selector`. They look at the API pods and the API Deployment only. Redis and postgres share the `app.kubernetes.io/name=tenantly` label, so without this narrowing they would be counted too. The API Deployment carries the component label in its metadata. If it ever does not, chaos.sh finds the Deployment from a running API pod's owner chain (pod, ReplicaSet, Deployment).

While it runs, a probe loop requests the URL every 0.5 s with a 1 s timeout, so the report can show whether the app stayed reachable.

**Where the report lands.** The report is written to `proof/chaos/chaos-report-<UTC timestamp>.md`. It has:

- a table of experiment, round, target, recovery_seconds and result,
- total probes, non-200 probes and the longest consecutive failure window in seconds,
- `kubectl get pods -o wide` before and after the run,
- the kubectl and cluster versions.

Every number in the report is measured. A failed round is recorded with its result as FAIL.

The script always uncordons a drained node on exit, even after an error or Ctrl-C.

## Step 10: teardown

```bash
make down
```

This deletes the kind cluster. Your fork and your `.env` stay on disk and in git.

## Troubleshooting

**Argo waves are stuck and child apps never start.**
The health check for `argoproj.io/Application` is missing. The sync waves only wait for a child app to become Healthy if Argo CD knows how to judge that health. Check that the patch is applied:

```bash
kubectl -n argocd get configmap argocd-cm -o yaml | grep -c 'health.argoproj.io_Application'
```

If the output is `0`, run `make bootstrap` again, or apply `argocd/bootstrap/argocd-cm-patch.yaml` by hand. Then restart the Argo application controller with `kubectl -n argocd rollout restart statefulset/argocd-application-controller`.

**The HPA shows `<unknown>` for `tenantly_requests_per_second`.**
Either the prometheus-adapter rule is missing or Prometheus has no Traefik data. Check both:

```bash
kubectl get --raw /apis/external.metrics.k8s.io/v1beta1 | head
kubectl get servicemonitor -n traefik
```

The adapter rule is `sum(rate(traefik_service_requests_total{service=~"tenantly-.*"}[1m]))`. It needs a ServiceMonitor for Traefik, and the kube-prometheus-stack Prometheus must select it. The Traefik chart values in `platform/traefik-values.yaml` add the label `release: kube-prometheus-stack` to that ServiceMonitor. Check the label with `kubectl get servicemonitor -n traefik --show-labels`. The rate is also empty until requests reach Traefik, so send some traffic first with `make load`.

**The SealedSecret does not decrypt.**
The secret was sealed for another cluster, or the sealing key changed. The controller's events show it:

```bash
kubectl get sealedsecrets -n tenantly
kubectl describe sealedsecret tenantly-secrets -n tenantly
```

Run `make seal` against this cluster, commit `charts/tenantly/values-sealed.yaml`, and push.

**Port 30080, 30300 or 30090 is already in use.**
Another process holds the host port. Find it with `lsof -nP -iTCP:30080 -sTCP:LISTEN`, stop it, and run `make cluster-up` again. kind publishes these ports on `127.0.0.1` only.

**`make image` says Tenantly source not found.**
This is expected until the Tenantly API checkout (`../tenantly`, or `TENANTLY_SRC`) has a `go.mod`. Use the demo values from Step 2.

## What was verified where

The plan was written on a machine that had **no Docker, no kind and no kubectl**. Nothing in this runbook was run against a real cluster on that machine. The chaos and load commands and the Grafana and Argo steps have not been run for real there.

The offline checks that ran on that machine:

- `bash -n` on every script in `scripts/`.
- `scripts/chaos.sh --dry-run --experiment all` and `--dry-run` with `PATH` limited to `/usr/bin:/bin`. Both exit 0 and print the DRY-RUN plan. An unknown `--experiment` exits 2 with a usage message.
- `scripts/chaos.sh` without `--dry-run` and without kubectl exits 2 with `MISSING: kubectl`.
- `tests/test_chaos_recovery.py` runs `chaos.sh --experiment pod-kill` against a stub `kubectl` and `curl` in a scratch folder. The stub only checks the script's flow, the probe CSV, the recovery loop and the report format. It is not a cluster, its output is not evidence of recovery times, and it does not check the chart's labels. A second stub case removes the component label from the Deployment lookup, so the owner-chain fallback runs.
- `tests/test_chart_static.py` renders the chart with `helm template` and checks that the API Deployment's `metadata.labels` carry the labels that chaos.sh selects on.

Run these from the repo root on a machine with Python 3:

```bash
python3 -m unittest discover -s tests -v   # 78 tests OK
python3 tools/validate.py                  # 37 passed, 0 failed
```

The helm render tests need `.tools/bin/helm` (`make tools`). Without it they are skipped, not passed. Anything not in that list was not run on that machine. Run Steps 1 to 10 on a machine with Docker to get real results.
