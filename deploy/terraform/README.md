# Terraform Module for Immich Memories

Deploys Immich Memories to Kubernetes with the `hashicorp/kubernetes` provider. CPU only by
default; NVIDIA GPU scheduling is a variable.

Authentication is disabled by default. Configure it before enabling Ingress. The UI is
single-user, single-replica because active workflow state is in-process; do not scale past one pod.

This module gets less exercise than Docker Compose: it is `terraform validate`d in the repo, not
applied to a live cluster on every release. Read the plan before you apply it.

## What it creates

Namespace (optional), Secret, two `ReadWriteOnce` PVCs, Deployment, Service, Ingress (optional).

The image runs as user `immich`, UID/GID 1000, `HOME=/home/immich`:

| Mount | Backed by | Holds |
|-------|-----------|-------|
| `/home/immich/.immich-memories` | cache PVC (writable) | `config.yaml`, `cache.db`, video cache, projects, automation history |
| `/app/output` | output PVC | generated videos (`IMMICH_MEMORIES_OUTPUT__DIRECTORY=/app/output`) |
| `/tmp` | emptyDir (`tmp_size`, 4Gi) | FFmpeg intermediates |

There is no ConfigMap. `immich_url` / `immich_api_key` (and `llm_api_key`, `musicgen_api_key`,
`secret_env`) land in the Secret and reach the pod through `envFrom`; every other setting is an
`IMMICH_MEMORIES_<SECTION>__<KEY>` env var (`env`). Probes: `/health/live` (liveness) and
`/health/ready` (readiness, `503` until config is present and Immich answers).

## Prerequisites

1. **Terraform** >= 1.0, `hashicorp/kubernetes` provider >= 2.20
2. **Kubernetes cluster** with a storage class for PVCs and Immich reachable from it
   (port 2283 by default). GPU only: NVIDIA GPU Operator + RuntimeClass `nvidia`
3. **kubeconfig** configured

## Quick Start

```bash
cd examples/basic            # CPU, no ingress, port-forward
# or: cd examples/production # pinned tag, basic auth, ingress + TLS, GPU optional

cp terraform.tfvars.example terraform.tfvars
vim terraform.tfvars

terraform init
terraform plan
terraform apply
$(terraform output -raw port_forward_command)   # http://localhost:8080
```

## Module Usage

```hcl
module "immich_memories" {
  source = "path/to/deploy/terraform"

  # Required
  immich_url     = "https://photos.example.com"
  immich_api_key = var.immich_api_key

  # Optional: LLM clip content analysis (any OpenAI-compatible API)
  llm_base_url = "http://ollama.ollama.svc.cluster.local:11434/v1"
  llm_model    = "qwen2.5-vl"

  # Optional: anything else, e.g. the in-pod daily automation
  env = {
    IMMICH_MEMORIES_AUTOMATION__ENABLED  = "true"
    IMMICH_MEMORIES_AUTOMATION__DAILY_AT = "09:00"
  }

  # Optional: NVIDIA GPU nodes
  gpu_enabled = true

  # Storage
  output_storage_size = "100Gi"
  cache_storage_size  = "50Gi"

  # Ingress — enable auth first (secret_env = { IMMICH_MEMORIES_AUTH_USERNAME = ..., IMMICH_MEMORIES_AUTH_PASSWORD = ... })
  ingress_enabled = false
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
| `image_tag` | Image tag (no `v` prefix: `vX.Y.Z` ships as `X.Y.Z`, plus `latest`) | `string` | `"latest"` |
| `replicas` | Keep at 1 | `number` | `1` |
| `resources` | Requests/limits object | `object` | `2Gi/1000m` – `8Gi/4000m` |
| `tmp_size` | `/tmp` emptyDir (8Gi for 4K) | `string` | `"4Gi"` |
| `env` | Extra `IMMICH_MEMORIES_*` env vars | `map(string)` | `{}` |
| `secret_env` | Extra env vars stored in the Secret | `map(string)` | `{}` |
| `labels` | Extra labels on every resource | `map(string)` | `{}` |

### LLM and music

| Name | Description | Type | Default |
|------|-------------|------|---------|
| `llm_base_url` | OpenAI-compatible endpoint; empty disables LLM analysis | `string` | `""` |
| `llm_model` | Vision model name | `string` | `""` |
| `llm_api_key` | API key (Secret) | `string` | `""` |
| `musicgen_enabled` | AI music via a MusicGen server | `bool` | `false` |
| `musicgen_base_url` | MusicGen server URL | `string` | in-cluster URL |
| `musicgen_api_key` | MusicGen API key (Secret) | `string` | `""` |
| `output_resolution` | `720p`, `1080p`, `4k` | `string` | `"1080p"` |

### GPU

| Name | Description | Type | Default |
|------|-------------|------|---------|
| `gpu_enabled` | Schedule on NVIDIA GPU nodes | `bool` | `false` |
| `gpu_count` | GPUs to request | `number` | `1` |
| `gpu_node_selector` | Node selector | `map(string)` | `{"nvidia.com/gpu.present": "true"}` |
| `runtime_class_name` | RuntimeClass | `string` | `"nvidia"` |

### Storage

| Name | Description | Type | Default |
|------|-------------|------|---------|
| `output_storage_size` | Output PVC | `string` | `"50Gi"` |
| `cache_storage_size` | Cache/state PVC | `string` | `"20Gi"` |
| `storage_class_name` | Storage class (`null` = cluster default) | `string` | `null` |

### Ingress

| Name | Description | Type | Default |
|------|-------------|------|---------|
| `ingress_enabled` | Enable ingress | `bool` | `false` |
| `ingress_class_name` | Ingress class | `string` | `"nginx"` |
| `ingress_host` | Hostname | `string` | `"memories.example.com"` |
| `ingress_tls_enabled` | TLS | `bool` | `false` |
| `ingress_tls_secret_name` | TLS secret | `string` | `"immich-memories-tls"` |
| `ingress_annotations` | Annotations | `map(string)` | `{}` |

## Outputs

`namespace`, `service_name`, `service_endpoint`, `ingress_host`, `deployment_name`,
`pvc_output`, `pvc_cache`, `port_forward_command`, `gpu_enabled`.

## Troubleshooting

```bash
# Pod events (scheduling, PVC binding, GPU)
kubectl describe pod -n immich-memories -l app.kubernetes.io/name=immich-memories
kubectl get pvc -n immich-memories

# Readiness: 503 until Immich answers
kubectl port-forward -n immich-memories svc/immich-memories 8080:80
curl -s localhost:8080/health/ready

# GPU: operator pods, node label, RuntimeClass
kubectl get pods -n gpu-operator
kubectl get nodes -L nvidia.com/gpu.present
kubectl get runtimeclass nvidia
```
