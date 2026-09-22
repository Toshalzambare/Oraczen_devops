# DevOps Internship Assignment Write-up

This write-up covers the end-to-end containerization, Helm charting, CI/CD pipeline creation, and GitOps rollout of the Notes API as per the assignment requirements.

## Part 1 — Containerization

**What was built:**
A multi-stage Dockerfile was created (`app/Dockerfile`) based on the slim `python:3.12-slim` image. 
- **Builder Stage**: Installs all build dependencies and Python packages into a virtual environment or directly into system paths, keeping the heavy build tools (like `gcc`) out of the final image.
- **Runtime Stage**: Copies only the built dependencies and the application code. It runs as a non-root user (`appuser` with UID 1000) for security.
- **Healthcheck**: Kubernetes liveness and readiness probes are configured in the Helm chart instead of relying on Docker's `HEALTHCHECK`. In a Kubernetes environment, native probes (`/healthz` and `/readyz`) provide better integration with the scheduler (e.g. stopping traffic to unready pods) compared to Docker's internal mechanism.

**Commands run to verify:**
```bash
docker build -t notes-api:local -f app/Dockerfile app
# Verified image size is minimal
docker images | grep notes-api
# Verified app runs locally and connects to DB
docker run --rm -p 8000:8000 -e POSTGRES_HOST=host.docker.internal -e POSTGRES_PASSWORD=notes notes-api:local
```

**Trade-offs/Future Improvements:**
If more time permitted, I would implement image signing (`cosign`) or generate SBOMs (`cyclonedx`) during the build process to strengthen the software supply chain.

## Part 2 — Helm Chart & Database Dependency

**What was built:**
A complete Helm chart (`helm/notes-api`) was created. It manages the `Deployment`, `Service`, `ConfigMap`, `ServiceAccount`, `HorizontalPodAutoscaler`, and `PostgreSQL` subchart.

**Database Dependency & Secret Wiring:**
*Question: How does the Helm chart handle the database dependency and secret wiring?*

The chart declares `bitnami/postgresql` (v16.x) as a dependency in `Chart.yaml`. During deployment, Helm provisions both the API and the DB. We avoid hardcoding secrets by referencing the automatically generated PostgreSQL secret (`notes-api-postgresql`) inside our API `Deployment` using `secretKeyRef`:
```yaml
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Release.Name }}-postgresql
      key: postgres-password
```
Other non-sensitive configuration values (`POSTGRES_HOST`, `POSTGRES_USER`, etc.) are securely passed via a `ConfigMap` using `envFrom`. This keeps sensitive credentials in Kubernetes Secrets and non-sensitive configuration in ConfigMaps, adhering to security best practices.

**Commands run to verify:**
```bash
helm dependency build helm/notes-api
helm lint helm/notes-api
helm template notes-dev helm/notes-api -f helm/notes-api/values-dev.yaml
helm install notes-dev helm/notes-api -f helm/notes-api/values-dev.yaml --namespace notes-dev --create-namespace
# Verification of pod health
kubectl get pods -n notes-dev
```
Linting and templating were completely clean.

**Environment Overrides:**
`values-dev.yaml` and `values-prod.yaml` were created with meaningful differences:
- **Dev**: Single replica, minimal resources, disabled persistence for PostgreSQL (to save disk space locally), HPA enabled for testing.
- **Prod**: 3 replicas for high availability, PostgreSQL persistence enabled (8Gi), larger resource requests/limits, higher HPA bounds.

## Part 3 — CI Pipeline (GitHub Actions)

**What was built:**
A GitHub Actions workflow (`.github/workflows/ci.yaml`) that triggers on PRs and pushes to `main`.
1. **Linting and Testing**: Uses `flake8` to catch syntax errors and `pytest` for unit testing.
2. **Build and Scan**: Builds the Docker image and tags it with the Git SHA. It then runs Trivy (`aquasecurity/trivy-action`) to scan for vulnerabilities, failing the build on `HIGH` or `CRITICAL` findings. 
3. **IaC Scan**: Runs a Trivy configuration scan against the Helm chart's rendered manifests to catch misconfigurations (e.g. running as root, missing limits) before they reach the cluster.

**Trade-offs/Future Improvements:**
Currently, we only build the image in CI. With a real registry (e.g. AWS ECR), we would add steps to authenticate using OIDC and push the image. The severity gate of `HIGH,CRITICAL` is a standard baseline that prevents major known exploits without blocking development on every low-severity issue in transitive dependencies.

## Part 4 — GitOps with ArgoCD

**What was built:**
An ArgoCD Application (`argocd/application.yaml`) that tracks the `add-helm-chart` branch of the Git repository. The application points to `helm/notes-api` and uses `values-dev.yaml`.

**Sync Policy:**
For the development environment, the sync policy is set to `Automated` with both `prune: true` and `selfHeal: true`. This ensures the cluster strictly matches the Git repository, reverting any manual `kubectl` changes (self-heal) and deleting resources removed from Git (prune). For a production environment, `selfHeal` is great, but `prune` might be configured manually or handled cautiously to prevent accidental deletion of critical stateful resources.

*Question: If someone edits `values-prod.yaml` and merges it to `main`, what's the sequence of events from that merge to the new pod running? Where would you look if it didn't roll out?*

1. **Sequence of Events**: The developer merges the PR into `main`. The CI pipeline runs, tests the code, builds the image, and pushes it. If the image tag changes, the repository is updated. ArgoCD's repository server polls GitHub (typically every 3 minutes) or receives a webhook. ArgoCD detects a divergence between the cluster state and the Git state. The Application controller then executes a sync, applying the new manifests to the cluster. Kubernetes then performs a rolling update of the Deployment to instantiate the new pods.
2. **Troubleshooting**: If it didn't roll out, I would first check the ArgoCD UI or run `kubectl get application -n argocd` to check the `SYNC STATUS` and `HEALTH STATUS`. If the sync failed, I would describe the Application (`kubectl describe application notes-api -n argocd`) to view error messages (e.g., manifest syntax error, RBAC issue). If it synced but pods aren't running, I would check the Kubernetes events (`kubectl get events`) and the pod logs (`kubectl logs -l app.kubernetes.io/name=notes-api`) for CrashLoopBackOffs or readiness probe failures.

## Part 5 — Stretch Goals Implemented

**Horizontal Pod Autoscaler (HPA):**
An HPA template was added to the Helm chart (`templates/hpa.yaml`) using the `autoscaling/v2` API, which monitors CPU utilization (targeting 70%). It dynamically scales the Notes API replicas. To get this working on the local `kind` cluster, `metrics-server` was installed and patched with the `--kubelet-insecure-tls` argument.

**Secrets Management Proposal (GitOps):**
Currently, PostgreSQL generates the secret dynamically. However, if we needed to inject external secrets (like API keys) via GitOps, storing them in `values-prod.yaml` in plain text is insecure. 
**Proposal:** Use **External Secrets Operator (ESO)** backed by AWS Secrets Manager. 
1. Store the secret in AWS Secrets Manager.
2. Configure an `ExternalSecret` custom resource in the Helm chart instead of a standard `Secret`.
3. Give the cluster an IAM Role (IRSA on EKS) allowing it to read the secret.
4. The ESO will automatically fetch the secret from AWS and materialize it as a native Kubernetes `Secret` in the cluster.
This ensures no secrets are stored in Git, keeping the GitOps pipeline fully declarative and secure.
