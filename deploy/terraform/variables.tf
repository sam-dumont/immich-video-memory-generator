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
  description = "Container image tag"
  type        = string
  default     = "latest"
}

variable "replicas" {
  description = "Number of replicas for the deployment"
  type        = number
  default     = 1
}

# Immich Configuration
variable "immich_url" {
  description = "URL of your Immich instance"
  type        = string
}

variable "immich_api_key" {
  description = "Immich API key"
  type        = string
  sensitive   = true
}

# Optional API Keys
variable "openai_api_key" {
  description = "OpenAI API key for mood analysis fallback"
  type        = string
  default     = ""
  sensitive   = true
}

# MusicGen Configuration
variable "musicgen_enabled" {
  description = "Enable AI music generation using MusicGen API"
  type        = bool
  default     = false
}

variable "musicgen_base_url" {
  description = "MusicGen API server URL"
  type        = string
  default     = "http://musicgen.musicgen.svc.cluster.local:8000"
}

variable "musicgen_api_key" {
  description = "MusicGen API key for authentication"
  type        = string
  default     = ""
  sensitive   = true
}

# GPU Configuration
variable "gpu_enabled" {
  description = "Enable NVIDIA GPU support"
  type        = bool
  default     = true
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

# Resource Limits
variable "resources" {
  description = "Resource requests and limits"
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

# Storage Configuration
variable "output_storage_size" {
  description = "Size of output PVC"
  type        = string
  default     = "50Gi"
}

variable "cache_storage_size" {
  description = "Size of cache PVC"
  type        = string
  default     = "20Gi"
}

variable "storage_class_name" {
  description = "Storage class for PVCs (null for default)"
  type        = string
  default     = null
}

# Ingress Configuration
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

# Ollama Configuration (for mood analysis)
variable "ollama_url" {
  description = "URL of Ollama service for mood analysis"
  type        = string
  default     = "http://ollama.ollama.svc.cluster.local:11434"
}

variable "ollama_model" {
  description = "Ollama model for vision analysis"
  type        = string
  default     = "llava"
}

# Application Configuration
variable "target_duration_seconds" {
  description = "Default video duration in seconds"
  type        = number
  default     = 600
}

variable "output_orientation" {
  description = "Output video orientation (landscape, portrait, square)"
  type        = string
  default     = "landscape"
}

variable "output_resolution" {
  description = "Output video resolution (720p, 1080p, 4k)"
  type        = string
  default     = "1080p"
}

variable "hardware_backend" {
  description = "Hardware acceleration backend (nvidia, auto, none)"
  type        = string
  default     = "nvidia"
}

variable "labels" {
  description = "Additional labels to apply to all resources"
  type        = map(string)
  default     = {}
}
