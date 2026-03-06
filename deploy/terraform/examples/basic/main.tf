# Basic deployment with NVIDIA GPU support

terraform {
  required_version = ">= 1.0"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.20"
    }
  }
}

# Configure Kubernetes provider
provider "kubernetes" {
  # Uses kubeconfig by default
  # Or configure explicitly:
  # config_path = "~/.kube/config"
  # config_context = "my-cluster"
}

module "immich_memories" {
  source = "../../"

  # Required: Immich credentials
  immich_url     = var.immich_url
  immich_api_key = var.immich_api_key

  # GPU Configuration (defaults to enabled)
  gpu_enabled = true
  gpu_count   = 1

  # Optional: Override defaults
  namespace           = "immich-memories"
  output_storage_size = "100Gi"
  cache_storage_size  = "50Gi"

  # Optional: Enable ingress
  ingress_enabled = false
  # ingress_host    = "memories.example.com"
}

output "port_forward_command" {
  value = module.immich_memories.port_forward_command
}
