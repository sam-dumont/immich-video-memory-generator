# Terraform Module for Immich Memories

Deploy Immich Memories to Kubernetes using Terraform with NVIDIA GPU support.

## Features

- Full Kubernetes deployment with GPU support
- Configurable resources and storage
- Optional ingress with TLS
- Integration with Ollama for AI-powered mood analysis
- Flexible configuration via variables

## Prerequisites

1. **Terraform** >= 1.0
2. **Kubernetes cluster** with:
   - NVIDIA GPU Operator installed
   - NVIDIA RuntimeClass configured
   - Storage class for PVCs
3. **kubeconfig** configured

## Quick Start

### Basic Deployment

```bash
cd examples/basic

# Copy and edit the example tfvars
cp terraform.tfvars.example terraform.tfvars
vim terraform.tfvars

# Initialize and apply
terraform init
terraform plan
terraform apply
```

### Production Deployment

```bash
cd examples/production

# Copy and edit the example tfvars
cp terraform.tfvars.example terraform.tfvars
vim terraform.tfvars

# Initialize and apply
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
| `storage_class_name` | Storage class for PVCs | `string` | `null` |

### Ingress

| Name | Description | Type | Default |
|------|-------------|------|---------|
| `ingress_enabled` | Enable ingress | `bool` | `false` |
| `ingress_class_name` | Ingress class | `string` | `"nginx"` |
| `ingress_host` | Ingress hostname | `string` | `"memories.example.com"` |
| `ingress_tls_enabled` | Enable TLS | `bool` | `false` |
| `ingress_annotations` | Ingress annotations | `map(string)` | `{}` |

### Application

| Name | Description | Type | Default |
|------|-------------|------|---------|
| `target_duration_seconds` | Default video duration in seconds | `number` | `600` |
| `output_orientation` | Video orientation | `string` | `"landscape"` |
| `output_resolution` | Video resolution | `string` | `"1080p"` |
| `hardware_backend` | HW acceleration backend | `string` | `"nvidia"` |

## Outputs

| Name | Description |
|------|-------------|
| `namespace` | Kubernetes namespace |
| `service_name` | Service name |
| `service_endpoint` | Internal service endpoint |
| `ingress_host` | Ingress hostname (if enabled) |
| `port_forward_command` | kubectl port-forward command |

## Accessing the UI

After deployment:

```bash
# Use the output command
$(terraform output -raw port_forward_command)

# Or manually
kubectl port-forward -n immich-memories svc/immich-memories 8080:80

# Open http://localhost:8080
```

## Integration with Other Modules

### With Ollama

```hcl
module "ollama" {
  source = "your-ollama-module"
  # ... configuration
}

module "immich_memories" {
  source = "path/to/deploy/terraform"

  ollama_url = "http://${module.ollama.service_name}.${module.ollama.namespace}.svc.cluster.local:11434"
  # ... other configuration
}
```

### With External Secrets

```hcl
# Use external-secrets operator for production secrets
resource "kubernetes_manifest" "external_secret" {
  manifest = {
    apiVersion = "external-secrets.io/v1beta1"
    kind       = "ExternalSecret"
    metadata = {
      name      = "immich-memories-secrets"
      namespace = var.namespace
    }
    spec = {
      secretStoreRef = {
        name = "vault-backend"
        kind = "ClusterSecretStore"
      }
      target = {
        name = "immich-memories-secrets"
      }
      data = [
        {
          secretKey = "IMMICH_API_KEY"
          remoteRef = {
            key      = "immich/api-key"
            property = "value"
          }
        }
      ]
    }
  }
}
```

## Troubleshooting

### GPU Not Detected

1. Verify GPU Operator is running:
   ```bash
   kubectl get pods -n gpu-operator
   ```

2. Check node labels:
   ```bash
   kubectl get nodes -L nvidia.com/gpu.present
   ```

3. Verify RuntimeClass:
   ```bash
   kubectl get runtimeclass nvidia
   ```

### Pod Stuck in Pending

1. Check events:
   ```bash
   kubectl describe pod -n immich-memories -l app.kubernetes.io/name=immich-memories
   ```

2. Verify PVCs are bound:
   ```bash
   kubectl get pvc -n immich-memories
   ```
