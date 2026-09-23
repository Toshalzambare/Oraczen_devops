# DevOps Internship Assignment Write-up

**Author:** Toshal Zambare
**Repository:** https://github.com/Toshalzambare/Oraczen_devops

---

## Part 1 — Containerization

### What Was Built

The application is containerized using a **multi-stage Dockerfile** at `app/Dockerfile`. The reason for a multi-stage build is straightforward: Python dependencies like `psycopg2` require `gcc` and system headers to compile, but those tools serve no purpose at runtime. A single-stage build would carry all of that dead weight into the final image, making it larger and exposing more attack surface.

The Dockerfile has two stages. The builder stage installs all Python dependencies using `pip install --prefix=/install`, which places the compiled packages into a self-contained directory rather than the system Python. The runtime stage starts fresh from the same slim base image, copies only that compiled packages directory from the builder, creates a dedicated non-root system user, and runs the application as that user. Nothing from the builder — no `gcc`, no build caches, no intermediate files — makes it into the final image.

The application runs as user `app` (not root), which is a security requirement. If the container is ever compromised, the attacker has very limited privileges on the host.

### Multi-stage Build vs Python `venv` — A Better Alternative?

You raised a good question about using a virtual environment instead. Yes, the `venv` copy pattern is a very common alternative in production Dockerfiles. In that approach, the builder stage creates a virtual environment, installs all packages into it, and then the runtime stage copies the entire `venv` directory. The `PATH` is updated to point to the venv's `bin/` folder so Python uses it automatically.

Both approaches produce **identical results** — the same packages, the same Python version, the same final image size. The difference is only in how you organise the dependency directory before copying it. The `venv` approach is arguably more conventional Python practice because it matches what developers do locally. The `--prefix` approach is slightly simpler (no need to update `PATH`).

**Would switching to `venv` break anything?** No, absolutely not. The application code in `main.py`, `database.py`, and all other files has zero knowledge of how packages were installed. `uvicorn` would start identically. It is purely an internal Dockerfile structure change.

A third, more modern option is `uv` (from Astral). It is a Rust-based package manager that installs Python dependencies 10 to 100 times faster than `pip`. Replacing `pip` with `uv` in the builder stage would dramatically reduce CI pipeline build times without changing the final image at all.

### Why HEALTHCHECK and Kubernetes Probes Both Exist

The Dockerfile includes a `HEALTHCHECK` instruction that polls `/healthz` every 30 seconds. However, in a Kubernetes environment, we also configure dedicated liveness and readiness probes in the Helm chart, and those are the ones that actually matter.

The Kubernetes **liveness probe** hits `/healthz` — it only checks if the process is alive. If it fails three times, Kubernetes restarts the pod.

The Kubernetes **readiness probe** hits `/readyz` — this endpoint performs a real `SELECT 1` query against PostgreSQL. If the database is not reachable (e.g., still booting), `/readyz` returns HTTP 503, and Kubernetes removes the pod from the Service's endpoint list so no traffic is routed to it. This is critical during startup: the API pod starts before Postgres is ready, and without the readiness probe, it would receive traffic before it can actually serve it.

Docker health checks cannot do this. They have no concept of readiness vs liveness, and they cannot interact with the Kubernetes scheduler or service endpoints.

### Verification Commands

Build the image:
```bash
docker build -t notes-api:local -f app/Dockerfile app
```
This produces a final image tagged `notes-api:local`. The multi-stage build means the image contains only the runtime — no build tools.

Run locally with a Postgres container for verification:
```bash
docker run -d --name local-pg -e POSTGRES_PASSWORD=notes -p 5432:5432 postgres:16
docker run --rm -p 8000:8000 \
  -e POSTGRES_HOST=host.docker.internal \
  -e POSTGRES_PASSWORD=notes \
  notes-api:local
```
The API container starts, the `wait_for_db()` function retries until Postgres is reachable, then logs `Startup complete, database schema ensured` and begins accepting connections.

Verify readiness (confirms the DB connection works):
```bash
curl http://localhost:8000/readyz
```
Returns `{"status":"ready"}`. If this returns 200, the entire stack — application and database — is working correctly.

We also verified using a `.env` file (which is gitignored and never committed):
```bash
docker run --rm -p 8000:8000 --env-file .env notes-api:local
```
This is how a developer would run it locally without typing credentials on the command line.

### Trade-offs / Things I'd Do Differently

With more time, I would switch the runtime stage base image from `python:3.12-slim` to a Distroless Python image. Distroless images contain only the application runtime and its dependencies — no shell, no package manager. This dramatically reduces the attack surface and the number of CVEs Trivy would report. I would also pin the base image to a specific digest rather than a tag, to prevent the image from silently changing between builds.

---

## Part 2 — Helm Chart & Database Dependency

### What Was Built

A complete Helm chart at `helm/notes-api/` containing a Deployment, Service, ConfigMap, ServiceAccount, and HorizontalPodAutoscaler. All resource names, labels, and selector labels are generated through helper functions in `_helpers.tpl` so that no name is ever hardcoded directly in a template. This means the chart works identically whether you install it as `notes-dev` or `notes-prod` — the release name is composed in automatically.

### Database as a Subchart — The Most Important Part

The `Chart.yaml` declares Bitnami's PostgreSQL chart as a dependency. Running `helm dependency build` downloads the full PostgreSQL chart and embeds it. A single `helm install` command then provisions both the Notes API and the PostgreSQL database at the same time — no separate database setup step is needed.

When the PostgreSQL subchart installs, it automatically generates a cryptographically random password and stores it in a Kubernetes `Secret` named `<release-name>-postgresql`. Our Deployment reads the password from that Secret at startup using `secretKeyRef`. This means the password is never written anywhere — not in `values.yaml`, not in any Git-committed file, not in any environment variable that a human created. The only entities that ever see the password are the cluster's Secret store, the PostgreSQL pod, and the API pod.

The non-sensitive connection details (host, port, database name, username) are stored in a `ConfigMap` and loaded into the API container via `envFrom`. This separation directly mirrors the design of `database.py`, which was written to accept individual `POSTGRES_*` environment variables precisely because in Kubernetes these values come from two different sources.

### Liveness and Readiness Probes

The liveness probe polls `/healthz` starting 10 seconds after the container starts. The readiness probe polls `/readyz` starting 15 seconds after start. The longer delay on readiness is intentional — it gives PostgreSQL time to boot and accept connections before the API is checked. Until the readiness probe passes, Kubernetes does not route any traffic to the pod.

### Resource Requests and Limits

Resource requests and limits are defined on the API container for both CPU and memory. Dev uses lower limits (250m CPU, 128Mi memory) appropriate for a laptop. Prod uses higher limits (500m CPU, 512Mi memory). Having limits is critical: without them, a memory leak or traffic spike could crash other pods on the same node.

### Environment Override Files

The base `values.yaml` provides shared defaults. The override files only contain values that meaningfully differ between environments.

`values-dev.yaml` sets 1 replica, disables PostgreSQL persistence (no disk storage needed on a laptop cluster), and uses `IfNotPresent` pull policy (use the locally loaded image, don't try to pull from a registry).

`values-prod.yaml` sets 2 replicas for high availability, enables PostgreSQL persistence with a 10Gi disk (so data survives pod restarts), sets `pullPolicy: Always` (always pull the latest stable image from the registry), and increases resource requests to handle real traffic.

### Verification Commands

Download the PostgreSQL subchart:
```bash
helm dependency build helm/notes-api
```
This downloads the Bitnami PostgreSQL chart and places it in `helm/notes-api/charts/`. Both `helm lint` and `helm template` require this to run first.

Validate the chart:
```bash
helm lint helm/notes-api
```
Output: `1 chart(s) linted, 0 chart(s) failed`. This checks template syntax, required fields, and obvious misconfigurations.

Render to raw YAML to inspect what will be deployed:
```bash
helm template notes-dev helm/notes-api -f helm/notes-api/values-dev.yaml
```
Produces the full Kubernetes manifest with all template variables resolved. This is useful to verify that `secretKeyRef` is pointing to the correct Secret name before installing.

Install to the cluster:
```bash
helm install notes-api helm/notes-api -f helm/notes-api/values-dev.yaml \
  --namespace notes-dev --create-namespace
```

Helm prints the `NOTES.txt` on completion, showing the exact `kubectl port-forward` command to reach the service.

Check that pods came up:
```bash
kubectl get pods -n notes-dev
```
Expected: both `notes-api-notes-api-<hash>` and `notes-api-postgresql-0` show `1/1 Running`.

Port-forward and test end to end:
```bash
kubectl port-forward svc/notes-api-notes-api 8000:8000 -n notes-dev
curl http://localhost:8000/readyz
curl -X POST http://localhost:8000/notes \
  -H 'content-type: application/json' \
  -d '{"title":"it works","content":"hello from kind"}'
```
The `readyz` call returns `{"status":"ready"}`. The POST call returns the created note object with an auto-generated `id` — confirming that both the API and database are working correctly.

### Trade-offs / Things I'd Do Differently

I would add a `NetworkPolicy` restricting inbound traffic to the PostgreSQL pod so that only pods belonging to the Notes API can connect to it on port 5432. Currently any pod in the namespace could theoretically reach the database. For production I would also add a `PodDisruptionBudget` ensuring at least one API pod remains available during node maintenance or rolling upgrades.

---

## Part 3 — CI Pipeline (GitHub Actions)

### What Was Built

A GitHub Actions workflow at `.github/workflows/ci.yaml` that triggers on pull requests targeting `main`. It has three jobs.

**Job 1 — Lint and Test** must pass before anything else runs. It installs dependencies, runs `ruff` for linting, and `pytest` for tests. `ruff` was chosen over `flake8` because it is dramatically faster (written in Rust), covers a superset of lint rules, and is becoming the community standard. `pytest -v` gives verbose output so the failing test name and assertion are visible directly in the Actions log.

**Job 2 — Build and Scan** runs after Job 1 passes. It builds the Docker image tagged with the exact Git commit SHA (not `latest` — every image is traceable to the exact code that produced it). It then runs Trivy against the built image. The pipeline fails on HIGH and CRITICAL severity findings only, with `ignore-unfixed: true` — meaning we only fail on CVEs that actually have a patch available. Failing on LOW/MEDIUM findings would mean the build is permanently red due to unactionable CVEs in system packages, which trains developers to ignore security alerts entirely.

**Job 3 — IaC Config Scan** runs in parallel with Job 2. It installs Helm, builds chart dependencies, runs `helm lint`, renders the chart to a YAML file, and runs Trivy's config scanner against that rendered YAML. This catches Kubernetes misconfigurations before they reach the cluster — things like containers running as root, missing resource limits, or missing security contexts. Our Deployment already sets `runAsNonRoot: true` and full resource limits, so this scan passes cleanly.

### Verification Commands

The pipeline runs automatically on pull requests. To replicate locally:

```bash
# Lint
ruff check app/

# Tests
pytest tests/ -v

# Build and scan image
docker build -t notes-api:$(git rev-parse HEAD) -f app/Dockerfile app
trivy image --severity HIGH,CRITICAL --ignore-unfixed notes-api:$(git rev-parse HEAD)

# Render and scan Helm config
helm dependency build helm/notes-api
helm lint helm/notes-api
helm template notes-ci helm/notes-api > /tmp/rendered.yaml
trivy config --severity HIGH,CRITICAL /tmp/rendered.yaml
```

### Trade-offs / Things I'd Do Differently

Currently the image is built but not pushed anywhere because the assignment does not require a real registry. In a real setup, the build job would push to GitHub Container Registry using `GITHUB_TOKEN` and the image would be tagged with the Git SHA. ArgoCD would then reference `ghcr.io/org/notes-api:<sha>` directly, making the exact deployed image fully traceable. I would also add pip dependency caching and Docker layer caching to reduce the pipeline from ~3-4 minutes to under a minute.

---

## Part 4 — GitOps with ArgoCD

### What Was Built

Two ArgoCD `Application` manifests in `argocd/`. Each Application tells ArgoCD which Git repository, branch, Helm chart path, and values files to use, and where to deploy it in the cluster.

`notes-api-dev.yaml` tracks the `add-helm-chart` branch and deploys to the `notes-dev` namespace using `values-dev.yaml`.

`notes-api-prod.yaml` tracks the `main` branch and deploys to the `notes-prod` namespace using `values-prod.yaml`.

### Sync Policy Reasoning

**Dev — fully automated sync with `prune: true` and `selfHeal: true`:**

`automated` means ArgoCD watches the branch and applies any new commit automatically within a few minutes — no human action required. `prune: true` means that if a Kubernetes resource is deleted from Git, ArgoCD deletes it from the cluster to keep them in sync. `selfHeal: true` means if someone applies a manual `kubectl` change to the dev cluster (bypassing Git), ArgoCD immediately reverts it back to what Git says.

This is appropriate for dev because fast feedback is the priority. Developers want to push code and see it running in the cluster within minutes.

**Prod — manual sync only (no automated block):**

This is a deliberate safety decision. Automated sync with pruning in production means one bad `git merge` to `main` could automatically delete a running StatefulSet or database within minutes. Data loss would follow with no human having reviewed the change. With manual sync, ArgoCD detects that the production cluster is `OutOfSync` but does nothing until an engineer explicitly opens the ArgoCD UI, reviews the diff, and clicks Sync.

### Rollout Sequence — From Merge to Running Pod

*If someone edits `values-prod.yaml` and merges to `main`, what happens?*

1. The merge triggers the GitHub Actions CI pipeline on `main`. It lints, tests, builds the image, and runs both Trivy scans. If any step fails, the process stops here.
2. ArgoCD polls the GitHub repository approximately every 3 minutes (or immediately if a webhook is configured). It detects the new commit on `main`.
3. ArgoCD compares the rendered Helm manifests from the new commit against the live state of the `notes-prod` namespace and marks the Application as `OutOfSync`. It shows a visual diff.
4. A DevOps engineer opens the ArgoCD UI, reviews exactly what changed (e.g., replica count went from 2 to 3), and clicks Sync.
5. ArgoCD applies the updated manifests to the Kubernetes API server.
6. Kubernetes performs a rolling update — it creates new pods with the new spec, waits for their readiness probes to pass (`/readyz` returning 200), then terminates the old pods. Zero downtime.

*Where would you look if it didn't roll out?*

First, the ArgoCD UI — the Application's `Health Status` will show `Degraded` and individual resources (Deployment, pods) will show their status. This is the quickest way to see what is wrong.

Second, run `kubectl describe pod -n notes-prod -l app.kubernetes.io/name=notes-api`. The `Events` section at the bottom shows the exact reason — `ImagePullBackOff` means the image tag does not exist in the registry; `CrashLoopBackOff` means the container is starting and immediately crashing.

Third, run `kubectl logs -n notes-prod deploy/notes-prod-notes-api --previous` to see the logs from the crashed container and identify the startup error.

### Verification Commands

Install ArgoCD into the cluster:
```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```
This installs all ArgoCD components (API server, repo server, application controller, Redis, Dex).

Wait for all pods to be ready:
```bash
kubectl get pods -n argocd -w
```

Apply the Application manifests:
```bash
kubectl apply -f argocd/notes-api-dev.yaml
kubectl apply -f argocd/notes-api-prod.yaml
```

Check sync status:
```bash
kubectl get applications -n argocd
```
`notes-api-dev` will show `Synced` and `Healthy` shortly after applying (automated). `notes-api-prod` will show `OutOfSync` until manually synced.

### Trade-offs / Things I'd Do Differently

Rather than polling, I would configure a GitHub webhook to trigger ArgoCD immediately on push, reducing the lag from 3 minutes to under 5 seconds. For a real team, I would also configure ArgoCD RBAC so that developers can see and sync the dev Application but only a senior DevOps engineer can sync production.

---

## Part 5 — Stretch Goals

### 1. Autoscaling Under Load (HPA) — Fully Implemented and Verified

The `hpa.yaml` template in the Helm chart creates a `HorizontalPodAutoscaler` using the `autoscaling/v2` API. It is gated behind a `Values.autoscaling.enabled` flag — disabled by default in `values.yaml`, enabled in both `values-dev.yaml` and `values-prod.yaml`. The HPA scales the Deployment up when average CPU utilization across pods exceeds 70%.

One important detail: HPA requires `metrics-server` to be running in the cluster to supply CPU utilization numbers. On `kind`, `metrics-server` is not installed by default, and when installed naively, it fails because `kind` nodes use self-signed TLS certificates. We patched the deployment to run with `--kubelet-insecure-tls` so it can scrape metrics from the `kind` kubelet.

Also, when first writing the HPA, we made a mistake — using `averageUtilizationPercentage` as the field name. The `autoscaling/v2` API requires `averageUtilization` (without the "Percentage" suffix). The old `autoscaling/v2beta2` API used the longer name. This was caught during testing when the HPA was created but showed `<unknown>` for its target metric.

Load test verification commands:
```bash
# Terminal 1: open tunnel to the service
kubectl port-forward svc/notes-api-notes-api 8000:8000 -n notes-dev

# Terminal 2: run the load test (sends POST requests in a tight loop for 120 seconds)
./load_test.sh http://localhost:8000 120

# Terminal 3: watch the HPA respond
kubectl get hpa -n notes-dev -w
```
During the load test, the HPA target CPU climbs above 70%, and you can observe the REPLICAS column change from 1 → 2 → 3 in real time. After the load test ends and CPU drops, the HPA scales back down (with a 5-minute cooldown by default).

### 2. Monitoring Hooks — What We Did and What Would Be Better

We added `prometheus.io/scrape`, `prometheus.io/port`, and `prometheus.io/path` annotations to the pod template in `deployment.yaml`. These are legacy annotations that some Prometheus configurations use to auto-discover pods to scrape.

**However, this approach is not optimal for two reasons:**

First, it only works if Prometheus is configured in legacy scrape-annotations mode. Modern Prometheus Operator deployments do not use these annotations at all — they use `ServiceMonitor` custom resources instead.

Second, and more importantly, our FastAPI app does not actually expose a `/metrics` endpoint. For these annotations to produce real data, we would need to add `prometheus-fastapi-instrumentator` to `requirements.txt` and register it in `main.py`. Without that, Prometheus would scrape the endpoint and receive a 404.

**The correct production approach is a `ServiceMonitor`**, which is a custom resource provided by the Prometheus Operator. A `ServiceMonitor` explicitly tells Prometheus which Service to scrape, on which port, and at what path. It is namespace-aware, type-safe, and does not depend on any Prometheus internal configuration. We could add a `servicemonitor.yaml` template to the Helm chart gated behind a `metrics.enabled` flag (which already exists in `values.yaml`).

**Recommended Prometheus Alerts for this service:**

Three alerts that would actually matter in production:

Alert 1 — High error rate: fires if more than 5% of HTTP requests return 5xx errors over a 5-minute window. This directly signals that the application is failing to serve users.

Alert 2 — Readiness flapping: fires if a pod's readiness status changes more than 4 times in 10 minutes. This catches the case where the app is intermittently losing its database connection, causing it to repeatedly toggle between ready and not-ready.

Alert 3 — High 95th percentile latency: fires if the slowest 5% of requests take longer than 1 second. This catches performance degradation before users explicitly complain.

### 3. Secrets Management Proposal — External Secrets Operator

**What the current limitation is:**

The database password is safe — it is auto-generated and lives only in a Kubernetes Secret. But if this were a real production service, it would also need API keys, JWT signing keys, third-party service credentials, and so on. None of these can be committed to `values.yaml` in Git. Standard Kubernetes Secrets are only base64 encoded, not encrypted. Anyone with `kubectl get secret` access on the cluster can decode them.

**Why External Secrets Operator (ESO) backed by AWS Secrets Manager:**

The alternative tools have trade-offs. **Sealed Secrets** encrypts the secret and commits the encrypted blob to Git — the secret is now tied to a specific cluster's encryption key, making disaster recovery harder. **SOPS** encrypts files before committing — it works well but requires every developer machine to have decryption access.

**ESO is the best fit for an EKS production environment** because the cluster fetches secrets directly from AWS Secrets Manager at runtime using an IAM Role for Service Accounts (IRSA). No secret material ever exists in Git. Rotation is handled in AWS — when you rotate a secret in Secrets Manager, ESO automatically refreshes the Kubernetes Secret on a configurable interval (e.g., every hour). The Git repository remains 100% declarative and 100% free of sensitive data.

The change to ArgoCD would be zero — ArgoCD simply deploys the `ExternalSecret` custom resource like any other object. ESO handles the AWS communication independently.

### 4. Image Supply Chain — Signing and SBOM

**Cosign image signing** would be added as a CI step after pushing the image to the registry. The private key signs the image digest, and the signature is stored alongside the image in the registry. A Kyverno policy on the cluster can then enforce that only images signed with our organisation's key can be deployed — any attempt to run an unsigned image would be rejected at admission.

**SBOM generation** produces a Software Bill of Materials — a machine-readable inventory of every package and library inside the image. We would generate this using `trivy image --format cyclonedx` and upload it as a CI pipeline artifact. SBOMs are increasingly required for compliance with government and enterprise security standards (e.g., US Executive Order 14028, SLSA Level 2). When a new CVE is announced, security teams can query the SBOM to immediately know which deployed images are affected rather than scanning every image again.
