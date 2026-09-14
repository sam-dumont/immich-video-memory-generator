# Terraform deployment

This module deploys the app to Kubernetes with `hashicorp/kubernetes`. It creates a Secret,
three persistent volumes, a single-replica Deployment and Service, plus optional namespace and
Ingress. CPU by default; `gpu_enabled` adds NVIDIA scheduling. It does not deploy model servers.

```bash
cd deploy/terraform/examples/basic
cp terraform.tfvars.example terraform.tfvars
# Fill in terraform.tfvars.
terraform init
terraform plan
terraform apply
$(terraform output -raw port_forward_command)
```

The production example adds authentication and Ingress. Keep authentication enabled before
exposing the Service. The image runs as UID/GID 1000 with a read-only root filesystem.

| Variable | Default | Holds |
|---|---|---|
| `cache_storage_size` | 50Gi | state at `~/.immich-memories`, `~/.cache` through `library-cache`, `/tmp` through `scratch` |
| `output_storage_size` | 50Gi | generated videos at `/app/output` |
| `models_storage_size` | 5Gi | preparation artifacts and Hugging Face caches at `/models` |

The state defaults allow 10 GB of thumbnails, 10 GB of videos and 2 GB of previews. Banks,
active downloads, optional local music models and FFmpeg scratch need additional room. The
storage driver must grant write access to `fsGroup: 1000`. Scratch persists across restarts;
clean it only while app and batch writers are stopped. Check storage expansion support before
changing existing claim sizes.

Run `immich-memories models fetch` inside the pod before using the `full` or `no_captions` tier.
Set `IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER=no_captions` through `env` to omit the caption
server; leave `llm_model` blank for the rules reader. The module sets writable encoder, detector
and `HF_HOME` paths on `/models`; keep overrides on a mounted path.

The contract tests check mounts, probes, defaults and example variables. They do not apply the
module to a cluster or run `terraform validate`. Read the plan before applying it.

[Full guide and variable reference](../../docs-site/docs/deploy/installation/terraform.md)
