# Kubernetes Deployment

Deploy Immich Memories to Kubernetes with NVIDIA GPU support.

## Prerequisites

1. **NVIDIA GPU Operator** installed in your cluster:
   ```bash
   helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
   helm repo update
   helm install gpu-operator nvidia/gpu-operator \
     --namespace gpu-operator \
     --create-namespace
   ```

2. **RuntimeClass** for NVIDIA (usually created by GPU Operator):
   ```yaml
   apiVersion: node.k8s.io/v1
   kind: RuntimeClass
   metadata:
     name: nvidia
   handler: nvidia
   ```

3. **Storage Class** available for PVCs

## Quick Start

1. **Create the secret** with your Immich credentials:
   ```bash
   cp secret.yaml.example secret.yaml
   # Edit with your values
   vim secret.yaml
   ```

2. **Deploy with kubectl**:
   ```bash
   kubectl apply -k .
   ```

3. **Or deploy individually**:
   ```bash
   kubectl apply -f namespace.yaml
   kubectl apply -f secret.yaml
   kubectl apply -f configmap.yaml
   kubectl apply -f pvc.yaml
   kubectl apply -f deployment.yaml
   kubectl apply -f service.yaml
   ```

4. **Access the UI**:
   ```bash
   kubectl port-forward -n immich-memories svc/immich-memories 8080:80
   # Open http://localhost:8080
   ```

## Running Batch Jobs

Generate memories via CLI:

```bash
# Edit job.yaml with your parameters
kubectl apply -f job.yaml

# Watch the job
kubectl logs -n immich-memories -f job/immich-memories-generate

# Check output
kubectl exec -n immich-memories deployment/immich-memories -- ls -la /output/
```

## Configuration

### GPU Resources

The deployment requests 1 NVIDIA GPU. Adjust in `deployment.yaml`:

```yaml
resources:
  requests:
    nvidia.com/gpu: "1"
  limits:
    nvidia.com/gpu: "1"
```

### Node Selection

Pods are scheduled on nodes with `nvidia.com/gpu.present=true` label.
Modify `nodeSelector` if your cluster uses different labels:

```yaml
nodeSelector:
  nvidia.com/gpu.present: "true"
  # Or use your custom label
  # gpu-node: "true"
```

### Storage

Default PVC sizes:
- Output: 50Gi (for generated videos)
- Cache: 20Gi (for downloaded assets and analysis cache)

Adjust in `pvc.yaml` based on your needs.

## Using with Sealed Secrets

For production, use sealed-secrets instead of plain secrets:

```bash
# Install kubeseal
brew install kubeseal

# Seal the secret
cp secret.yaml.example secret.yaml
# Fill in your values, then seal
kubeseal --format=yaml < secret.yaml > sealed-secret.yaml

# Apply sealed secret
kubectl apply -f sealed-secret.yaml
```

## Monitoring

The deployment includes health probes. For metrics:

```yaml
# Add to deployment.yaml
annotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "8080"
  prometheus.io/path: "/metrics"
```
