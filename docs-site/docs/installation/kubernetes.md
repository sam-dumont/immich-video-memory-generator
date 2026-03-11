---
sidebar_position: 4
title: Kubernetes
---

# Kubernetes Deployment

Deploy Immich Memories to Kubernetes with NVIDIA GPU support. The manifests live in `deploy/kubernetes/`.

## Prerequisites

1. **NVIDIA GPU Operator** installed in your cluster:

   ```bash
   helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
   helm repo update
   helm install gpu-operator nvidia/gpu-operator \
     --namespace gpu-operator \
     --create-namespace
   ```

2. **RuntimeClass** for NVIDIA (usually created by GPU Operator automatically):

   ```yaml
   apiVersion: node.k8s.io/v1
   kind: RuntimeClass
   metadata:
     name: nvidia
   handler: nvidia
   ```

3. **Storage Class** available for PVCs

## Quick Start

```bash
cd deploy/kubernetes

# Create the secret with your Immich credentials
cp secret.yaml.example secret.yaml
# Edit with your actual values
vim secret.yaml

# Deploy everything
kubectl apply -k .
```

Or deploy resources individually if you prefer:

```bash
kubectl apply -f namespace.yaml
kubectl apply -f secret.yaml
kubectl apply -f configmap.yaml
kubectl apply -f pvc.yaml
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
```

## Access the UI

```bash
kubectl port-forward -n immich-memories svc/immich-memories 8080:80
# Open http://localhost:8080
```

## Batch Jobs

Run one-off video generation via CLI instead of the UI:

```bash
# Edit job.yaml with your parameters
kubectl apply -f job.yaml

# Watch the logs
kubectl logs -n immich-memories -f job/immich-memories-generate

# Check output
kubectl exec -n immich-memories deployment/immich-memories — ls -la /output/
```

## Configuration

### GPU Resources

The deployment requests 1 NVIDIA GPU by default. Adjust in `deployment.yaml`:

```yaml
resources:
  requests:
    nvidia.com/gpu: "1"
  limits:
    nvidia.com/gpu: "1"
```

### Node Selection

Pods schedule on nodes labeled `nvidia.com/gpu.present=true`. Change the `nodeSelector` if your cluster uses different labels:

```yaml
nodeSelector:
  nvidia.com/gpu.present: "true"
  # Or your custom label
  # gpu-node: "true"
```

### Storage

Default PVC sizes:

| PVC | Size | Purpose |
|-----|------|---------|
| Output | 50Gi | Generated videos |
| Cache | 20Gi | Downloaded assets and analysis cache |

Adjust in `pvc.yaml` based on how many videos you plan to generate.

## Sealed Secrets

For production, don't commit plain secrets. Use [sealed-secrets](https://github.com/bitnami-labs/sealed-secrets):

```bash
# Install kubeseal
brew install kubeseal

# Create and seal the secret
cp secret.yaml.example secret.yaml
# Fill in your values, then seal
kubeseal --format=yaml < secret.yaml > sealed-secret.yaml

# Apply
kubectl apply -f sealed-secret.yaml
```

## Monitoring

The deployment includes liveness and readiness probes. For Prometheus scraping, add annotations to `deployment.yaml`:

```yaml
annotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "8080"
  prometheus.io/path: "/metrics"
```
