---
sidebar_position: 3
title: Kubernetes
---

# Kubernetes Deployment

Deploy Immich Memories to Kubernetes with NVIDIA GPU support. The manifests live in `deploy/kubernetes/`.

:::warning Known gaps in the shipped manifests
The manifests in `deploy/kubernetes/` were written before the current image and config schema and
are being fixed. As of v0.40.1 they do **not** boot as-is. What you need to change by hand:

1. **The Secret is not applied by `kubectl apply -k .`** — `kustomization.yaml` has `- secret.yaml`
   commented out, so the Deployment fails with `CreateContainerConfigError`. Apply
   `secret.yaml` yourself (or uncomment the line) before `-k`.
2. **Read-only config mount.** The image runs as `immich`, UID/GID 1000 (matching the manifests'
   `runAsUser: 1000`; `HOME` is overridden to `/home/appuser`, which is fine). The ConfigMap is
   mounted read-only at `/home/appuser/.immich-memories`, but the app creates `cache/`,
   `projects/`, `cache.db` and `.storage_secret` in exactly that directory at startup, so it
   fails on the read-only mount. Fix: mount the **cache PVC** at `/home/appuser/.immich-memories`
   (writable; `fsGroup: 1000` makes it group-writable) and project the ConfigMap into it as a
   single file with `subPath: config.yaml`; set
   `IMMICH_MEMORIES_STORAGE_SECRET` from the Secret so sessions survive restarts. The
   `~/.cache/immich-memories` mount is never written to and can go.
3. **Stale config keys.** `audio.ollama_*`, `content_analysis.provider|ollama_*|openai_*`,
   `hardware.backend` and the env var `IMMICH_MEMORIES_CONTENT_ANALYSIS__OLLAMA_URL` are not part
   of the schema and are silently ignored, so LLM analysis is never configured. Use an `llm:` block
   (`provider: ollama`, `base_url`, `model`) plus `content_analysis.enabled: true`, or the env vars
   `IMMICH_MEMORIES_LLM__PROVIDER=ollama`, `IMMICH_MEMORIES_LLM__BASE_URL=…`,
   `IMMICH_MEMORIES_LLM__MODEL=…`, `IMMICH_MEMORIES_CONTENT_ANALYSIS__ENABLED=true`.
   `OPENAI_API_KEY` sets `llm.api_key` only; `PIXABAY_API_KEY` is read by nothing.
4. **Probes hit `/health`**, which always returns 200. Use `/health/live` for liveness and
   `/health/ready` for readiness.
5. **`job.yaml` durations are seconds**: `--duration 10` / `--duration 5` produce 5–10 second
   videos. Use `600` / `300`.
6. **`service.yaml` contains an Ingress** (`memories.example.com`, nginx) that `-k` creates
   even though auth is off by default. Delete or comment it out until you have enabled
   [authentication](../configuration/authentication.mdx).
7. **NetworkPolicy egress allows Immich on port 3001 only**; current Immich listens on 2283. Add it.
8. **`newTag: v1.0.0`** in the kustomization example — published tags carry no `v` prefix (`0.40.1`, `latest`).
:::

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

# The secret is NOT in kustomization.yaml — apply it first (or uncomment `- secret.yaml`)
kubectl apply -f namespace.yaml
kubectl apply -f secret.yaml

# Deploy the rest (after the fixes in the box above)
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

:::caution One private replica
Authentication is disabled by default. Do not add an Ingress or otherwise expose the Service until
authentication is enabled. The UI is single-user, single-replica because active workflow state is
kept in-process; leave `replicas: 1` even when using shared storage.
:::

## Batch Jobs

Run one-off video generation via CLI instead of the UI:

```bash
# Edit job.yaml with your parameters
kubectl apply -f job.yaml

# Watch the logs
kubectl logs -n immich-memories -f job/immich-memories-generate

# Check output
kubectl exec -n immich-memories deployment/immich-memories -- ls -la /output/
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
| Output | 50Gi | Generated videos (`output.directory: /output` in the ConfigMap) |
| Cache | 20Gi | Meant for `cache.db` + the video cache — see below |

Adjust in `pvc.yaml` based on how many videos you plan to generate. Config itself is a ConfigMap
(`immich-memories-config`), not a PVC.

The **Cache** PVC is meant to hold `cache.db` (analysis scores, the most valuable data: losing it
means re-analyzing your entire library) and the downloaded-video cache. The app writes both under
`~/.immich-memories/` (`cache.db`, `cache/`), so the PVC has to be mounted there — the shipped
manifest mounts it at `~/.cache/immich-memories`, which nothing writes to (gap 2 above).

```bash
# Backup cache from the running pod
kubectl exec -n immich-memories deployment/immich-memories -- \
  immich-memories cache backup /output/cache-backup.db

# Or export as portable JSON
kubectl exec -n immich-memories deployment/immich-memories -- \
  immich-memories cache export /output/scores.json
```

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

Three endpoints on port 8080:

- `/health/live` — process is up; always `200`. Use for the liveness probe.
- `/health/ready` — `200` only when config is present and Immich is reachable, else `503`. Use for the readiness probe.
- `/health` — the same JSON as `/health/ready` (`status`, `immich_reachable`, `last_successful_run`, `version`, automation state) but always HTTP `200`. Compatibility endpoint; do not use it as a probe.

The shipped manifest points both probes at `/health` (gap 4 above). For monitoring tools like Uptime Kuma, use `/health/ready`.
