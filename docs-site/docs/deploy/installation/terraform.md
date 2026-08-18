---
sidebar_position: 4
title: Terraform
---

# Terraform Deployment

Deploy Immich Memories to Kubernetes using Terraform. The module lives in `deploy/terraform/`.

:::warning Known gaps in the shipped module
The module predates the current image and config schema and is being fixed. As of v0.40.1 the
pod it creates does not run cleanly. What to change by hand:

1. **UID / read-only config.** The image runs as the system user `immich` (UID below 1000,
   `HOME=/home/immich`); the module sets `run_as_user = 1000` and mounts the ConfigMap read-only at
   `/home/immich/.immich-memories`. The app creates `cache/`, `projects/`, `cache.db` and
   `.storage_secret` in that directory at startup and fails on the read-only mount; UID 1000 also
   cannot write `/app/.nicegui`. Fix: mount the cache PVC at `/home/immich/.immich-memories`
   (`fs_group = 1000` makes it writable), project `config.yaml` into it with `sub_path`, add an
   `empty_dir` at `/app/.nicegui`. The `/home/immich/.cache/immich-memories` mount is never used.
2. **Probes hit `/`.** Use `/health/live` (liveness) and `/health/ready` (readiness).
3. **Stale config keys.** `hardware.backend`, `audio.auto_music`, `audio.music_source`,
   `audio.ollama_url`, `audio.ollama_model`, `audio.ducking_*`, `audio.music_volume_db`,
   `defaults.target_duration_seconds`, `defaults.output_orientation`, `analysis.keyframe_interval`
   are written into `config.yaml` but ignored by the app. In particular the `ollama_url` /
   `ollama_model` variables configure nothing: to get LLM analysis you need an `llm:` block plus
   `content_analysis.enabled: true` (see [Config Reference](../../reference/config-reference.md#llm-vision-model)).
   `openai_api_key` only fills `llm.api_key`.
4. **Example tfvars.** `examples/production/terraform.tfvars.example` pins `image_tag = "v1.0.0"`
   — published tags have no `v` prefix (`0.40.1`, `latest`). Both example tfvars also set
   variables the module does not declare (`use_scene_detection`, `enable_downscaling`,
   `content_analysis_*`), which Terraform warns about and ignores.
:::

:::caution Before enabling Ingress
Authentication is disabled by default. An enabled Ingress exposes the UI to every client that can
reach it, so configure authentication first. The UI is single-user, single-replica; do not scale
the deployment beyond one pod.
:::

## Prerequisites

1. **Terraform** >= 1.0 and the `hashicorp/kubernetes` provider >= 2.20
2. **Kubernetes cluster** with:
   - NVIDIA GPU Operator installed
   - NVIDIA RuntimeClass configured
   - Storage class for PVCs
3. **kubeconfig** configured and pointing at your cluster

## Quick Start

### Basic Deployment

```bash
cd deploy/terraform/examples/basic

cp terraform.tfvars.example terraform.tfvars
vim terraform.tfvars

terraform init
terraform plan
terraform apply
```

### Production Deployment

```bash
cd deploy/terraform/examples/production

cp terraform.tfvars.example terraform.tfvars
vim terraform.tfvars

terraform init
terraform plan
terraform apply
```

## Module Usage

```hcl
module "immich_memories" {
  source = "path/to/deploy/terraform"

  # Required
  immich_url     = "https://photos.example.com"
  immich_api_key = var.immich_api_key

  # GPU Configuration
  gpu_enabled = true
  gpu_count   = 1

  # Storage
  output_storage_size = "100Gi"
  cache_storage_size  = "50Gi"

  # Ingress
  ingress_enabled = true
  ingress_host    = "memories.example.com"
}
```

## Variables

### Required

| Name | Description | Type |
|------|-------------|------|
| `immich_url` | URL of your Immich instance | `string` |
| `immich_api_key` | Immich API key | `string` |

### Deployment

| Name | Description | Type | Default |
|------|-------------|------|---------|
| `namespace` | Kubernetes namespace | `string` | `"immich-memories"` |
| `create_namespace` | Create the namespace | `bool` | `true` |
| `image_repository` | Container image | `string` | `"ghcr.io/sam-dumont/immich-video-memory-generator"` |
| `image_tag` | Image tag (no `v` prefix: `0.40.1`, `latest`) | `string` | `"latest"` |
| `replicas` | Replica count — keep at 1, the UI is single-replica | `number` | `1` |
| `resources` | Requests/limits object (`requests.memory/cpu`, `limits.memory/cpu`) | `object` | `2Gi/1000m` – `8Gi/4000m` |
| `labels` | Extra labels on every resource | `map(string)` | `{}` |

### GPU Configuration

| Name | Description | Type | Default |
|------|-------------|------|---------|
| `gpu_enabled` | Enable NVIDIA GPU support | `bool` | `true` |
| `gpu_count` | Number of GPUs to request | `number` | `1` |
| `gpu_node_selector` | Node selector for GPU nodes | `map(string)` | `{"nvidia.com/gpu.present": "true"}` |
| `runtime_class_name` | RuntimeClass for NVIDIA | `string` | `"nvidia"` |

### Storage

| Name | Description | Type | Default |
|------|-------------|------|---------|
| `output_storage_size` | Size of output PVC | `string` | `"50Gi"` |
| `cache_storage_size` | Size of cache PVC | `string` | `"20Gi"` |
| `storage_class_name` | Storage class for PVCs | `string` | `null` (cluster default) |

### Ingress

| Name | Description | Type | Default |
|------|-------------|------|---------|
| `ingress_enabled` | Enable ingress | `bool` | `false` |
| `ingress_class_name` | Ingress class | `string` | `"nginx"` |
| `ingress_host` | Ingress hostname | `string` | `"memories.example.com"` |
| `ingress_tls_enabled` | Enable TLS | `bool` | `false` |
| `ingress_tls_secret_name` | TLS secret name | `string` | `"immich-memories-tls"` |
| `ingress_annotations` | Extra ingress annotations | `map(string)` | `{}` |

### Music and LLM

| Name | Description | Type | Default |
|------|-------------|------|---------|
| `musicgen_enabled` | Generate AI music with a MusicGen server | `bool` | `false` |
| `musicgen_base_url` | MusicGen server URL | `string` | `"http://musicgen.musicgen.svc.cluster.local:8000"` |
| `musicgen_api_key` | MusicGen API key (stored in the Secret) | `string` | `""` |
| `openai_api_key` | Sets `llm.api_key` via `OPENAI_API_KEY`. Only useful together with an `llm:` block, which the module does not write yet | `string` | `""` |
| `ollama_url`, `ollama_model` | Written to `audio.ollama_*`, which the app ignores — no effect (gap 3) | `string` | Ollama in-cluster URL / `"llava"` |

### Application

| Name | Description | Type | Default |
|------|-------------|------|---------|
| `output_resolution` | Video resolution (720p, 1080p, 4k) | `string` | `"1080p"` |
| `target_duration_seconds` | Written to `defaults.target_duration_seconds`, which the app does not read (duration is per run) | `number` | `600` |
| `output_orientation` | Written to `defaults.output_orientation`, which the app does not read (orientation is per run) | `string` | `"landscape"` |
| `hardware_backend` | Written to `hardware.backend`, which the app does not read — the backend is auto-detected; `gpu_enabled` is the real switch | `string` | `"nvidia"` |

## Outputs

| Name | Description |
|------|-------------|
| `namespace` | Kubernetes namespace |
| `service_name` | Service name for internal access |
| `service_endpoint` | Internal service endpoint (FQDN) |
| `ingress_host` | Ingress hostname (if enabled) |
| `port_forward_command` | Ready-to-run kubectl port-forward command |
| `deployment_name` | Deployment name |
| `pvc_output` | Name of the output PVC |
| `pvc_cache` | Name of the cache PVC |
| `gpu_enabled` | Whether GPU support is enabled |

## Accessing the UI

After `terraform apply`:

```bash
# Use the output directly
$(terraform output -raw port_forward_command)

# Or manually
kubectl port-forward -n immich-memories svc/immich-memories 8080:80

# Open http://localhost:8080
```

## Troubleshooting

### GPU Not Detected

```bash
# Check GPU Operator pods are running
kubectl get pods -n gpu-operator

# Verify nodes have GPU labels
kubectl get nodes -L nvidia.com/gpu.present

# Verify RuntimeClass exists
kubectl get runtimeclass nvidia
```

### Pod Stuck in Pending

```bash
# Check pod events for scheduling errors
kubectl describe pod -n immich-memories -l app.kubernetes.io/name=immich-memories

# Verify PVCs are bound
kubectl get pvc -n immich-memories
```

Common causes: no GPU nodes available, PVC storage class doesn't exist, or resource requests exceed what the cluster can provide.
