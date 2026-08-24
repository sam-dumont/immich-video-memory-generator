---
sidebar_position: 4
title: Terraform
---

# Terraform Deployment

Deploy Immich Memories to Kubernetes using Terraform. The module lives in `deploy/terraform/` and
uses the `hashicorp/kubernetes` provider. CPU only by default; NVIDIA GPU scheduling is a variable.

:::note Less travelled than Docker Compose
Docker Compose is the primary self-hosting path. The module and both examples pass
`terraform validate` and their contracts are pinned in the test suite (writable state volume,
`/health/live` + `/health/ready` probes, `gpu_enabled = false` by default), but they are not
applied to a live cluster on every release. Read the plan before you apply it, and open an issue
if something does not boot.
:::

:::caution Before enabling Ingress
Authentication is disabled by default. An enabled Ingress exposes the UI to every client that can
reach it, so configure authentication first (`secret_env` with `IMMICH_MEMORIES_AUTH_USERNAME` /
`IMMICH_MEMORIES_AUTH_PASSWORD`, or [OIDC](../configuration/authentication.mdx)). The UI is
single-user, single-replica; do not scale the deployment beyond one pod.
:::

## What it creates

Namespace (optional), Secret, two `ReadWriteOnce` PVCs, Deployment, Service, Ingress (optional).

The image runs as user `immich`, UID/GID 1000, `HOME=/home/immich` (`run_as_user` / `fs_group`
1000, all capabilities dropped, `RuntimeDefault` seccomp):

| Mount | Backed by | Holds |
|-------|-----------|-------|
| `/home/immich/.immich-memories` | cache PVC (writable) | `config.yaml`, `cache.db` (analysis scores), video cache, projects, automation history |
| `/app/output` | output PVC | generated videos (`IMMICH_MEMORIES_OUTPUT__DIRECTORY=/app/output`) |
| `/tmp` | emptyDir (`tmp_size`, 4Gi) | FFmpeg intermediates — 8Gi for 4K |

There is no ConfigMap. `immich_url` / `immich_api_key` (plus `llm_api_key`, `musicgen_api_key` and
anything in `secret_env`) land in the Secret and reach the pod through `envFrom`; every other
setting is an `IMMICH_MEMORIES_<SECTION>__<KEY>` env var (`env`). Settings saved from the UI go to
`config.yaml` on the PVC; env vars override them.

Probes: `/health/live` (liveness) and `/health/ready` (readiness — `503` until config is present
and Immich answers, which keeps the pod out of the Service while Immich is down).

## Prerequisites

1. **Terraform** >= 1.0 and the `hashicorp/kubernetes` provider >= 2.20
2. **Kubernetes cluster** with a storage class for PVCs and Immich reachable from it (port 2283
   by default). For `gpu_enabled = true`: NVIDIA GPU Operator and the `nvidia` RuntimeClass
3. **kubeconfig** configured and pointing at your cluster

## Quick Start

```bash
cd deploy/terraform/examples/basic        # CPU, no ingress, port-forward
# or: cd deploy/terraform/examples/production   # pinned tag, basic auth, ingress + TLS, GPU optional

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
  # Tested against Qwen3.6-27B and Qwen3.6-35B-A3B; `llm_model` is the tag the server serves
  llm_base_url = "http://ollama.ollama.svc.cluster.local:11434/v1"
  llm_model    = "qwen3.6:27b"

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
| `image_tag` | Image tag (no `v` prefix: `0.59.2`, `latest`) | `string` | `"latest"` |
| `replicas` | Replica count — keep at 1, the UI is single-replica | `number` | `1` |
| `resources` | Requests/limits object (`requests.memory/cpu`, `limits.memory/cpu`) | `object` | `2Gi/1000m` – `8Gi/4000m` |
| `tmp_size` | `/tmp` emptyDir for FFmpeg intermediates (8Gi for 4K) | `string` | `"4Gi"` |
| `env` | Extra `IMMICH_MEMORIES_<SECTION>__<KEY>` env vars | `map(string)` | `{}` |
| `secret_env` | Extra env vars stored in the Secret (auth password, storage secret) | `map(string)` | `{}` |
| `labels` | Extra labels on every resource | `map(string)` | `{}` |

### GPU Configuration

| Name | Description | Type | Default |
|------|-------------|------|---------|
| `gpu_enabled` | Schedule on NVIDIA GPU nodes (RuntimeClass, `nvidia.com/gpu`, node selector, toleration, `NVIDIA_*` env) | `bool` | `false` |
| `gpu_count` | Number of GPUs to request | `number` | `1` |
| `gpu_node_selector` | Node selector for GPU nodes | `map(string)` | `{"nvidia.com/gpu.present": "true"}` |
| `runtime_class_name` | RuntimeClass for NVIDIA | `string` | `"nvidia"` |

### Storage

| Name | Description | Type | Default |
|------|-------------|------|---------|
| `output_storage_size` | Size of the output PVC | `string` | `"50Gi"` |
| `cache_storage_size` | Size of the cache/state PVC | `string` | `"20Gi"` |
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

### LLM and music

| Name | Description | Type | Default |
|------|-------------|------|---------|
| `llm_base_url` | OpenAI-compatible endpoint (Ollama: append `/v1`). Sets `llm.base_url` and turns on `content_analysis.enabled`; empty disables LLM analysis | `string` | `""` |
| `llm_model` | Vision model name served at `llm_base_url` | `string` | `""` |
| `llm_api_key` | API key for `llm_base_url` (stored in the Secret) | `string` | `""` |
| `musicgen_enabled` | Generate AI music with a MusicGen server | `bool` | `false` |
| `musicgen_base_url` | MusicGen server URL | `string` | `"http://musicgen.musicgen.svc.cluster.local:8000"` |
| `musicgen_api_key` | MusicGen API key (stored in the Secret) | `string` | `""` |
| `output_resolution` | Video resolution (`720p`, `1080p`, `4k`) | `string` | `"1080p"` |

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
| `pvc_cache` | Name of the cache/state PVC |
| `gpu_enabled` | Whether GPU support is enabled |

## Troubleshooting

```bash
# Pod events: scheduling, PVC binding, GPU
kubectl describe pod -n immich-memories -l app.kubernetes.io/name=immich-memories
kubectl get pvc -n immich-memories

# Readiness stays 503 until Immich answers — check the payload
kubectl port-forward -n immich-memories svc/immich-memories 8080:80
curl -s localhost:8080/health/ready

# GPU: operator pods, node label, RuntimeClass
kubectl get pods -n gpu-operator
kubectl get nodes -L nvidia.com/gpu.present
kubectl get runtimeclass nvidia
```

Common causes of a Pending pod: the storage class doesn't exist, resource requests exceed the
cluster, or `gpu_enabled = true` without GPU nodes.
