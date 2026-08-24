variable "namespace" {
  description = "Kubernetes namespace for immich-memories"
  type        = string
  default     = "immich-memories"
}

variable "create_namespace" {
  description = "Whether to create the namespace"
  type        = bool
  default     = true
}

variable "image_repository" {
  description = "Container image repository"
  type        = string
  default     = "ghcr.io/sam-dumont/immich-video-memory-generator"
}

variable "image_tag" {
  description = "Container image tag. Published tags carry no `v` prefix: 0.59.2, latest"
  type        = string
  default     = "latest"
}

variable "replicas" {
  description = "Replica count. Keep at 1: the UI is single-user and keeps workflow state in-process"
  type        = number
  default     = 1
}

# Immich
variable "immich_url" {
  description = "URL of your Immich instance (in-cluster: http://immich-server.<ns>.svc.cluster.local:2283)"
  type        = string
}

variable "immich_api_key" {
  description = "Immich API key"
  type        = string
  sensitive   = true
}

# LLM clip content analysis (any OpenAI-compatible API; optional)
variable "llm_base_url" {
  description = "LLM endpoint, e.g. http://ollama.ollama.svc.cluster.local:11434/v1. Empty disables LLM analysis"
  type        = string
  default     = ""
}

variable "llm_model" {
  description = "Vision model name served at llm_base_url"
  type        = string
  default     = ""
}

variable "llm_api_key" {
  description = "API key for llm_base_url (stored in the Secret)"
  type        = string
  default     = ""
  sensitive   = true
}

# MusicGen (optional)
variable "musicgen_enabled" {
  description = "Enable AI music generation using a MusicGen API server"
  type        = bool
  default     = false
}

variable "musicgen_base_url" {
  description = "MusicGen API server URL"
  type        = string
  default     = "http://musicgen.musicgen.svc.cluster.local:8000"
}

variable "musicgen_api_key" {
  description = "MusicGen API key (stored in the Secret)"
  type        = string
  default     = ""
  sensitive   = true
}

# Any other setting: IMMICH_MEMORIES_<SECTION>__<KEY>
variable "env" {
  description = "Extra environment variables, e.g. { IMMICH_MEMORIES_AUTOMATION__ENABLED = \"true\" }"
  type        = map(string)
  default     = {}
}

variable "secret_env" {
  description = "Extra environment variables stored in the Secret, e.g. IMMICH_MEMORIES_AUTH_PASSWORD"
  type        = map(string)
  default     = {}
  sensitive   = true
}

# GPU (optional; the base deployment runs on CPU-only clusters)
variable "gpu_enabled" {
  description = "Schedule on NVIDIA GPU nodes (GPU Operator required)"
  type        = bool
  default     = false
}

variable "gpu_count" {
  description = "Number of GPUs to request"
  type        = number
  default     = 1
}

variable "gpu_node_selector" {
  description = "Node selector for GPU nodes"
  type        = map(string)
  default = {
    "nvidia.com/gpu.present" = "true"
  }
}

variable "runtime_class_name" {
  description = "RuntimeClass for NVIDIA GPU"
  type        = string
  default     = "nvidia"
}

# Resources
variable "resources" {
  description = "Resource requests and limits (idle ~100 MB; analysis 2-4 GB; FFmpeg assembly 4-8 GB)"
  type = object({
    requests = object({
      memory = string
      cpu    = string
    })
    limits = object({
      memory = string
      cpu    = string
    })
  })
  default = {
    requests = {
      memory = "2Gi"
      cpu    = "1000m"
    }
    limits = {
      memory = "8Gi"
      cpu    = "4000m"
    }
  }
}

variable "tmp_size" {
  description = "emptyDir size for /tmp (FFmpeg intermediates): 2Gi is enough for 1080p, 8Gi for 4K"
  type        = string
  default     = "4Gi"
}

# Storage
variable "output_storage_size" {
  description = "Size of the output PVC (generated videos, /app/output)"
  type        = string
  default     = "50Gi"
}

variable "cache_storage_size" {
  description = "Size of the cache/state PVC (/home/immich/.immich-memories: config, cache.db, video cache)"
  type        = string
  default     = "20Gi"
}

variable "storage_class_name" {
  description = "Storage class for PVCs (null for the cluster default)"
  type        = string
  default     = null
}

# Ingress. Authentication is disabled by default: enable it before turning this on.
variable "ingress_enabled" {
  description = "Enable ingress"
  type        = bool
  default     = false
}

variable "ingress_class_name" {
  description = "Ingress class name"
  type        = string
  default     = "nginx"
}

variable "ingress_host" {
  description = "Ingress hostname"
  type        = string
  default     = "memories.example.com"
}

variable "ingress_tls_enabled" {
  description = "Enable TLS for ingress"
  type        = bool
  default     = false
}

variable "ingress_tls_secret_name" {
  description = "TLS secret name for ingress"
  type        = string
  default     = "immich-memories-tls"
}

variable "ingress_annotations" {
  description = "Additional ingress annotations"
  type        = map(string)
  default     = {}
}

# Application
variable "output_resolution" {
  description = "Output video resolution (720p, 1080p, 4k)"
  type        = string
  default     = "1080p"
}

variable "labels" {
  description = "Additional labels to apply to all resources"
  type        = map(string)
  default     = {}
}
