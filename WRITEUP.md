# DevOps Internship Assignment Write-up

This write-up covers the end-to-end containerization, Helm charting, CI/CD pipeline creation, and GitOps rollout of the Notes API as per the assignment requirements, including all stretch goals.

## Part 1 — Containerization

**What was built:**
A multi-stage Dockerfile (`app/Dockerfile`) was constructed using `python:3.12-slim` as the base image.
- **Builder Stage**: Installs all python dependencies into a temporary directory `/install`, keeping heavy build tools like `gcc` out of the final runtime image.
- **Runtime Stage**: Copies the built dependencies from the builder stage. It creates a dedicated non-root user (`appuser` with UID 1000) and runs the application as this user, enforcing security best practices.
- **Healthcheck**: We omitted Docker's `HEALTHCHECK` inside the Dockerfile because we rely natively on Kubernetes liveness and readiness probes configured in the Helm chart. Kubernetes probes are superior because they integrate directly with the cluster scheduler, automatically removing failing pods from service endpoints.

**Verification Commands:**
```bash
docker build -t notes-api:local -f app/Dockerfile app
docker run --rm -p 8000:8000 -e POSTGRES_HOST=host.docker.internal -e POSTGRES_PASSWORD=notes notes-api:local
```

## Part 2 — Helm Chart & Database Dependency

**What was built:**
A Helm chart (`helm/notes-api`) that manages the complete lifecycle of the `Deployment`, `Service`, `ConfigMap`, `ServiceAccount`, and `HorizontalPodAutoscaler`, alongside a `PostgreSQL` subchart.

**Database Dependency & Secret Wiring:**
The chart explicitly declares the Bitnami PostgreSQL chart (`oci://registry-1.docker.io/bitnamicharts/postgresql`) as a dependency. By pulling this as a subchart, Helm provisions both the API and the Database simultaneously.
**Crucially, we do not duplicate or hardcode the database password.** Instead, we leverage `secretKeyRef` in the `deployment.yaml` to dynamically read the auto-generated password created by the PostgreSQL subchart:
```yaml
env:
  - name: POSTGRES_PASSWORD
    valueFrom:
      secretKeyRef:
        name: {{ .Release.Name }}-postgresql
        key: password
```
Non-sensitive configurations (`POSTGRES_HOST`, `POSTGRES_USER`, etc.) are injected via `envFrom` pointing to our ConfigMap.

**Environment Overrides:**
We created meaningful differences between environments:
- **`values-dev.yaml`**: Configured with 1 replica, HPA enabled for testing scaling, and PostgreSQL persistence disabled to save local disk space on the `kind` cluster.
- **`values-prod.yaml`**: Configured with 3 replicas for HA, larger resource requests/limits, higher HPA bounds, and enabled PostgreSQL persistence (8Gi).

**Verification Commands:**
```bash
helm dependency build helm/notes-api
helm lint helm/notes-api
helm template notes-dev helm/notes-api -f helm/notes-api/values-dev.yaml
helm install notes-dev helm/notes-api -f helm/notes-api/values-dev.yaml --namespace notes-dev --create-namespace
```

## Part 3 — CI Pipeline (GitHub Actions)

**What was built:**
A GitHub Actions workflow (`.github/workflows/ci.yaml`) that triggers on pull requests and pushes to `main`.
1. **Linting and Testing**: Enforces code hygiene with `ruff` and executes unit tests via `pytest`.
2. **Build and Scan**: Builds the Docker image and tags it with the Git SHA. It then runs Trivy (`aquasecurity/trivy-action`) to scan the built image. We set the severity gate to `HIGH,CRITICAL` to catch actively exploitable vulnerabilities without failing the build on unactionable low-severity issues.
3. **IaC Scan**: It renders the Helm chart into raw manifests and runs a Trivy config scan to detect misconfigurations (e.g. running as root, missing CPU limits) before deployment.

## Part 4 — GitOps with ArgoCD

**What was built:**
We defined two distinct ArgoCD Application manifests for GitOps: `argocd/notes-api-dev.yaml` (tracking the `add-helm-chart` branch for dev) and `argocd/notes-api-prod.yaml` (tracking `main`).

**Sync Policy Reasoning:**
- **Dev (`notes-api-dev.yaml`)**: Uses automated sync with `prune: true` and `selfHeal: true`. In dev, a fast feedback loop is prioritized. If a resource is removed from Git, we want it deleted from the cluster immediately.
- **Prod (`notes-api-prod.yaml`)**: Uses manual sync. Automated sync with pruning in production is highly risky; an accidental bad merge to `main` could automatically delete running StatefulSets or databases. Human review is required before triggering a sync in production.

**Rollout Sequence:**
If a developer edits `values-prod.yaml` and merges to `main`:
1. The CI pipeline triggers, testing the code, building the image, and scanning it.
2. ArgoCD periodically polls GitHub (or receives a webhook). It detects the commit on `main`.
3. Because production is set to manual sync, ArgoCD marks the application as `OutOfSync`.
4. An engineer reviews the diff in the ArgoCD UI and clicks "Sync".
5. ArgoCD applies the new manifests to the cluster.
6. Kubernetes performs a rolling update of the Deployment to the new pod specification.
If the rollout fails (e.g., ImagePullBackOff), I would look at the ArgoCD UI for the `Health Status` of the Deployment, or run `kubectl describe pod -l app.kubernetes.io/name=notes-api -n notes-prod` to see the exact event errors.

## Part 5 — Stretch Goals Implemented

### 1. Monitoring Hooks
We added `prometheus.io/scrape: "true"` annotations to the Pod template in the Helm chart. In a production environment using Prometheus Operator, we would define a `ServiceMonitor`:
```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: notes-api-monitor
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: notes-api
  endpoints:
  - port: http
    path: /metrics
```
**Recommended Alerts:**
1. **High Error Rate**: `rate(http_requests_total{status=~"5.."}[5m]) > 0.05` (Alert if >5% of requests are 500s).
2. **Readiness Probe Flapping**: `changes(kube_pod_status_ready{condition="true"}[10m]) > 4` (Alert if pod readiness is unstable).
3. **High Latency**: `histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) > 1.0` (Alert if 95p latency exceeds 1 second).

### 2. Autoscaling Under Load (HPA)
The Helm chart includes an `autoscaling/v2` `HorizontalPodAutoscaler` which scales based on CPU utilization (targeting 70%).
We verified this in `kind` by ensuring `metrics-server` was deployed and patched with `--kubelet-insecure-tls`. 
A `load_test.sh` script is included in the repository that spams the `/notes` endpoint with POST requests in a loop. When executed (`./load_test.sh http://localhost:8000 120`), the CPU metric spikes, and running `kubectl get hpa -n notes-dev` confirms the replica count scaling dynamically from 1 to 3 pods.

### 3. Secrets Management Proposal (External Secrets Operator)
While the Postgres password is secure, injecting external API keys into Git via `values-prod.yaml` in plain text breaks GitOps security.
**Proposal:** 
1. Store plain-text secrets in **AWS Secrets Manager**.
2. Install the **External Secrets Operator (ESO)** in the EKS cluster.
3. Configure the cluster with an IAM Role (IRSA) allowing it to read the specific secret.
4. Replace the standard Kubernetes `Secret` manifest with an `ExternalSecret` custom resource in the Helm chart.
5. ESO automatically fetches the secret from AWS and materializes it as a native Kubernetes `Secret` on the cluster, keeping the Git repository 100% declarative and clear of sensitive data.

### 4. Image Supply Chain
To enhance software supply chain security, the CI pipeline can be extended:
1. **Cosign**: After pushing the image, run `cosign sign --key cosign.key notes-api:${{ github.sha }}` to cryptographically sign the image. The cluster (via tools like Kyverno) can enforce that only signed images are deployed.
2. **SBOM Generation**: Add a step `trivy image --format cyclonedx -o sbom.json notes-api:${{ github.sha }}` and upload `sbom.json` as a pipeline artifact for compliance tracking.
