---
sidebar_position: 4
title: Terraform
---

# Terraform

The module lives in `deploy/terraform/` and drives the `hashicorp/kubernetes` provider. CPU only by
default; NVIDIA scheduling is a variable. Docker Compose is the primary self-hosting path, and what
CI pins here is the module's contract: writable state volume, `/health/live` and `/health/ready`
probes, `gpu_enabled = false` by default. Nothing runs `terraform validate` and nothing applies the
module to a live cluster, so read the plan before you apply it.

:::caution Before enabling Ingress
Authentication is disabled by default. An enabled Ingress exposes the UI to every client that can
reach it, so configure authentication first (`secret_env` with `IMMICH_MEMORIES_AUTH_USERNAME` /
`IMMICH_MEMORIES_AUTH_PASSWORD`, or [OIDC](./authentication.mdx)). The UI is
single-user, single-replica: do not scale the deployment beyond one pod.
:::

## What it creates

Namespace (optional), Secret, two `ReadWriteOnce` PVCs, Deployment, Service, Ingress (optional).
The image runs as `immich`, UID/GID 1000 (`run_as_user` / `fs_group` 1000, all capabilities
dropped, `RuntimeDefault` seccomp, `read_only_root_filesystem = true`). Three writable mounts:

| Mount | Backed by | Holds |
|-------|-----------|-------|
| `/home/immich/.immich-memories` | cache PVC | `config.yaml`, `cache/annotations.sqlite` (the editor's banks), `cache.db` (run history and automation state), video cache, projects |
| `/app/output` | output PVC | generated videos (`IMMICH_MEMORIES_OUTPUT__DIRECTORY=/app/output`) |
| `/tmp` | emptyDir (`tmp_size`, 4Gi) | FFmpeg intermediates: 8Gi for 4K |

There is no ConfigMap. `immich_url` / `immich_api_key` (plus `llm_api_key`, `musicgen_api_key` and
anything in `secret_env`) land in the Secret and reach the pod through `envFrom`; every other
setting is an `IMMICH_MEMORIES_<SECTION>__<KEY>` env var (`env`). Settings saved from the UI go to
`config.yaml` on the PVC; env vars override them. Probes are `/health/live` for liveness and
`/health/ready` for readiness, which stays `503` until config is present and Immich answers.

:::caution The module has no models claim
The Kustomize base has a third PVC (`immich-memories-models`) and a `fetch-models` init container;
this module has neither, so on any tier but `metadata_only` the first cut stops at prepare with
`public heads need the pinned DINOv2 ONNX export at ...`. Either run the fetch in the pod once,
into the cache PVC:

```bash
kubectl exec -n immich-memories deploy/immich-memories -- immich-memories models fetch
```

with `env` pointing `IMMICH_MEMORIES_TRIAGE__ENCODER`,
`IMMICH_MEMORIES_EDITORIAL__PREPARATION__MARQO_ONNX` and
`IMMICH_MEMORIES_EDITORIAL__PREPARATION__DETECTOR_CACHE_DIR` under
`/home/immich/.immich-memories/models`, or use [Kubernetes](./kubernetes.md) instead, which does
this for you
([#928](https://github.com/sam-dumont/immich-video-memory-generator/issues/928)).
:::

## Prerequisites

Terraform >= 1.0, the `hashicorp/kubernetes` provider >= 2.20, a kubeconfig pointing at a cluster
with a storage class, and Immich reachable from it (port 2283 by default). For
`gpu_enabled = true`, the NVIDIA GPU Operator and the `nvidia` RuntimeClass.

## Quick start

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

## Module usage

```hcl
module "immich_memories" {
  source = "path/to/deploy/terraform"

  # Required
  immich_url     = "https://photos.example.com"
  immich_api_key = var.immich_api_key

  # The reader, a separate deployment. It reads text only and must hold 32k of context; `llm_model`
  # is the tag that server reports at /v1/models.
  llm_base_url = "http://your-model-host:8000/v1"
  llm_model    = "gemma-4-e4b-it-6bit"

  # Optional: the in-pod daily run, NVIDIA nodes, bigger claims
  env = {
    IMMICH_MEMORIES_AUTOMATION__ENABLED  = "true"
    IMMICH_MEMORIES_AUTOMATION__DAILY_AT = "09:00"
  }
  gpu_enabled         = true
  output_storage_size = "100Gi"
  cache_storage_size  = "50Gi"
}
```

Which model to serve at `llm_base_url` is on [Readers](../better/reader.md). Preparation goes through the
same `env` map: [editorial annotation setup](../being-rewritten/editorial-preparation.md).

## Variables

`immich_url` and `immich_api_key` are required. Everything else has a default:

| Name | Description | Default |
|------|-------------|---------|
| `namespace`, `create_namespace` | Kubernetes namespace, and whether to create it | `"immich-memories"`, `true` |
| `image_repository`, `image_tag` | Container image. No `v` prefix, so release `vX.Y.Z` is tag `X.Y.Z` | `ghcr.io/sam-dumont/immich-video-memory-generator`, `"latest"` |
| `replicas` | Keep at 1; the UI is single-replica | `1` |
| `resources` | Requests/limits object (`requests.memory/cpu`, `limits.memory/cpu`) | `2Gi/1000m` to `8Gi/4000m` |
| `tmp_size` | `/tmp` emptyDir for FFmpeg intermediates (8Gi for 4K) | `"4Gi"` |
| `env`, `secret_env` | Extra env vars, the second stored in the Secret | `{}` |
| `labels` | Extra labels on every resource | `{}` |
| `gpu_enabled`, `gpu_count` | Schedule on NVIDIA GPU nodes: RuntimeClass, `nvidia.com/gpu`, node selector, toleration, `NVIDIA_*` env | `false`, `1` |
| `gpu_node_selector`, `runtime_class_name` | how GPU nodes are found | `{"nvidia.com/gpu.present": "true"}`, `"nvidia"` |
| `output_storage_size`, `cache_storage_size` | PVC sizes | `"50Gi"`, `"20Gi"` |
| `storage_class_name` | Storage class for both PVCs | `null` (cluster default) |
| `ingress_enabled`, `ingress_class_name`, `ingress_host` | Ingress, off by default | `false`, `"nginx"`, `"memories.example.com"` |
| `ingress_tls_enabled`, `ingress_tls_secret_name`, `ingress_annotations` | TLS and extras for it | `false`, `"immich-memories-tls"`, `{}` |
| `llm_base_url`, `llm_model`, `llm_api_key` | The reader (Ollama: append `/v1`). Empty leaves the editor without a model | `""` |
| `musicgen_enabled`, `musicgen_base_url`, `musicgen_api_key` | AI music through a MusicGen server | `false`, the in-cluster service, `""` |
| `output_resolution` | `720p`, `1080p` or `4k` | `"1080p"` |

`terraform output` gives the namespace, service name and endpoint, the ingress host, the deployment
and PVC names, whether GPU is on, and a ready-to-run `port_forward_command`.

## Troubleshooting

```bash
# Pod events: scheduling, PVC binding, GPU
kubectl describe pod -n immich-memories -l app.kubernetes.io/name=immich-memories
kubectl get pvc -n immich-memories

# Readiness stays 503 until Immich answers: check the payload
kubectl port-forward -n immich-memories svc/immich-memories 8080:80
curl -s localhost:8080/health/ready

# GPU: operator pods, node label, RuntimeClass
kubectl get pods -n gpu-operator
kubectl get nodes -L nvidia.com/gpu.present
kubectl get runtimeclass nvidia
```

A Pending pod is usually a storage class that does not exist, resource requests the cluster cannot
meet, or `gpu_enabled = true` without GPU nodes.
