# Production deployment: pinned image, ingress with TLS, GPU optional.
# Enable authentication (secret_env below) before enabling the ingress.

terraform {
  required_version = ">= 1.0"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.20"
    }
  }

  # Example backend configuration
  # backend "s3" {
  #   bucket = "terraform-state"
  #   key    = "immich-memories/terraform.tfstate"
  #   region = "us-west-2"
  # }
}

provider "kubernetes" {
  config_path    = var.kubeconfig_path
  config_context = var.kubeconfig_context
}

module "immich_memories" {
  source = "../../"

  # Namespace
  namespace        = var.namespace
  create_namespace = true

  # Image
  image_repository = "ghcr.io/sam-dumont/immich-video-memory-generator"
  image_tag        = var.image_tag

  # Immich credentials
  immich_url     = var.immich_url
  immich_api_key = var.immich_api_key

  # LLM clip content analysis
  llm_base_url = var.llm_base_url
  llm_model    = var.llm_model
  llm_api_key  = var.llm_api_key

  # MusicGen AI music generation
  musicgen_enabled  = var.musicgen_enabled
  musicgen_base_url = var.musicgen_base_url
  musicgen_api_key  = var.musicgen_api_key

  # Basic auth for the UI (required before exposing it through the ingress)
  secret_env = {
    IMMICH_MEMORIES_AUTH_USERNAME = var.auth_username
    IMMICH_MEMORIES_AUTH_PASSWORD = var.auth_password
  }

  # Daily automation inside the pod
  env = {
    IMMICH_MEMORIES_AUTOMATION__ENABLED  = "true"
    IMMICH_MEMORIES_AUTOMATION__DAILY_AT = "09:00"
    TZ                                   = var.timezone
  }

  # GPU (optional)
  gpu_enabled = var.gpu_enabled
  gpu_count   = var.gpu_count
  gpu_node_selector = {
    "nvidia.com/gpu.present" = "true"
  }

  # Resources
  resources = {
    requests = {
      memory = "4Gi"
      cpu    = "2000m"
    }
    limits = {
      memory = "16Gi"
      cpu    = "8000m"
    }
  }
  tmp_size = "8Gi"

  # Storage
  output_storage_size = "200Gi"
  cache_storage_size  = "100Gi"
  storage_class_name  = var.storage_class_name

  # Ingress with TLS
  ingress_enabled         = true
  ingress_class_name      = "nginx"
  ingress_host            = var.ingress_host
  ingress_tls_enabled     = true
  ingress_tls_secret_name = "immich-memories-tls"
  ingress_annotations = {
    "cert-manager.io/cluster-issuer"              = "letsencrypt-prod"
    "nginx.ingress.kubernetes.io/proxy-body-size" = "0"
  }

  # Application settings
  output_resolution = "1080p"

  labels = {
    "environment" = var.environment
    "team"        = "media"
  }
}

output "ingress_url" {
  value = "https://${var.ingress_host}"
}

output "port_forward_command" {
  value = module.immich_memories.port_forward_command
}
