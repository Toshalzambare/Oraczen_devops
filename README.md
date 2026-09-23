# Notes API — DevOps Complete Setup Guide

Welcome! This repository contains a fully containerized FastAPI + PostgreSQL backend deployed via Helm, GitOps (ArgoCD), and secured with Trivy in a CI pipeline.

This guide provides a seamless, step-by-step walkthrough to get the entire Kubernetes stack running on your local machine.

## Prerequisites
Before you start, ensure you have the following installed:
- **Docker**
- **Kubernetes CLI (`kubectl`)**
- **Helm** (v3)
- **Kind** (Kubernetes in Docker)

*(Note: If you are using the local binaries provided in this repository on Windows, you can replace `kind` and `helm` with `./.bin/kind` and `./.bin/helm` in the commands below).*

---

## Step-by-Step Execution Guide

### **Terminal 1: Infrastructure Setup**
Open your primary terminal and run these commands sequentially. Do not close this terminal.

**1. Clone the repository and enter the directory:**
```bash
git clone https://github.com/Toshalzambare/Oraczen_devops.git
cd Oraczen_devops
```

**2. Create a local Kubernetes cluster using Kind:**
```bash
kind create cluster --name notes
```

**3. Install the Metrics Server (Required for Autoscaling):**
*(We patch this specifically for local Kind clusters to bypass TLS verification).*
```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl patch deployment metrics-server -n kube-system --type='json' -p='[{"op": "add", "path": "/spec/template/spec/containers/0/args/-", "value": "--kubelet-insecure-tls"}]'
```

**4. Build the Docker Image and Load it into Kind:**
```bash
docker build -t notes-api:local -f app/Dockerfile app/
kind load docker-image notes-api:local --name notes
```

**5. Fetch Helm Dependencies (PostgreSQL):**
```bash
helm dependency build helm/notes-api
```

**6. Deploy the Application via Helm:**
```bash
helm install notes-api helm/notes-api -f helm/notes-api/env/dev/values.yaml --namespace notes-dev --create-namespace
```

**7. Wait for the Application to Start:**
PostgreSQL takes about a minute to boot up. Watch the pods until both `notes-api-postgresql-0` and the `notes-api` pod are marked as `Running` and `1/1` (Ready).
```bash
kubectl get pods -n notes-dev -w
```
*(Once both are 1/1 Running, press `Ctrl+C` to stop watching).*

---

### **Terminal 2: Port-Forwarding (Keep Running)**
Open a **new terminal window** to route traffic from your machine into the Kubernetes cluster.

**8. Start the Port-Forward:**
```bash
kubectl port-forward svc/notes-api-notes-api 8000:8000 -n notes-dev
```
*(Leave this terminal completely open and running. Do not press Ctrl+C).*

---

### **Terminal 3: Testing & Autoscaling**
Open a **third terminal window** to interact with the API and test the Horizontal Pod Autoscaler (HPA).

**9. Test the API Endpoints:**
Check the readiness of the database connection:
```bash
curl -s http://localhost:8000/readyz
```

Create a secure note:
```bash
curl -s -X POST http://localhost:8000/notes -H "Content-Type: application/json" -d '{"title":"Secure Notes", "content":"The system is fully operational!"}'
```

Retrieve the notes:
```bash
curl -s http://localhost:8000/notes
```

**10. Test Horizontal Pod Autoscaling (HPA):**
Run the included load-testing script to simulate heavy traffic:
```bash
chmod +x scripts/load_test.sh
./scripts/load_test.sh http://localhost:8000 120
```

While the load test is running, open a **fourth terminal window** to watch the cluster dynamically scale up the application pods in real-time:
```bash
kubectl get hpa -n notes-dev -w
```

---

## 🧹 Cleanup
When you are completely finished experimenting, you can destroy the local cluster to free up Docker resources. Return to Terminal 1 and run:
```bash
kind delete cluster --name notes
```
