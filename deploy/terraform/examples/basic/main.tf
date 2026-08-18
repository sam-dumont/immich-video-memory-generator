# Basic deployment: CPU only, no ingress, port-forward to reach the UI.

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

  # Optional: LLM clip content analysis (any OpenAI-compatible API)
  llm_base_url = var.llm_base_url
  llm_model    = var.llm_model
  llm_api_key  = var.llm_api_key

  # Optional: NVIDIA GPU nodes (GPU Operator required)
  gpu_enabled = var.gpu_enabled

  # Optional: Override defaults
  namespace           = "immich-memories"
  output_storage_size = "100Gi"
  cache_storage_size  = "50Gi"
}

output "port_forward_command" {
  value = module.immich_memories.port_forward_command
}
