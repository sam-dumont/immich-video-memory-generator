---
sidebar_position: 4
title: Terraform
---

# Terraform Deployment

Deploy Immich Memories to Kubernetes using Terraform. The module lives in `deploy/terraform/`.

## Prerequisites

1. **Terraform** >= 1.0
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
| `ingress_annotations` | Extra ingress annotations | `map(string)` | `{}` |

### Application

| Name | Description | Type | Default |
|------|-------------|------|---------|
| `target_duration_seconds` | Default video duration in seconds | `number` | `600` |
| `output_orientation` | Video orientation (landscape, portrait, square) | `string` | `"landscape"` |
| `output_resolution` | Video resolution (720p, 1080p, 4k) | `string` | `"1080p"` |
| `hardware_backend` | HW acceleration backend (nvidia, auto, none) | `string` | `"nvidia"` |

## Outputs

| Name | Description |
|------|-------------|
| `namespace` | Kubernetes namespace |
| `service_name` | Service name for internal access |
| `service_endpoint` | Internal service endpoint (FQDN) |
| `ingress_host` | Ingress hostname (if enabled) |
| `port_forward_command` | Ready-to-run kubectl port-forward command |
| `deployment_name` | Deployment name |
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
