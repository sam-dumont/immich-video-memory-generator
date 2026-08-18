variable "kubeconfig_path" {
  description = "Path to kubeconfig file"
  type        = string
  default     = "~/.kube/config"
}

variable "kubeconfig_context" {
  description = "Kubeconfig context to use"
  type        = string
  default     = null
}

variable "namespace" {
  description = "Kubernetes namespace"
  type        = string
  default     = "immich-memories"
}

variable "environment" {
  description = "Environment name (prod, staging, etc)"
  type        = string
  default     = "production"
}

variable "image_tag" {
  description = "Container image tag (no `v` prefix)"
  type        = string
  default     = "latest"
}

variable "timezone" {
  description = "Timezone for the in-pod daily automation"
  type        = string
  default     = "UTC"
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

# UI authentication
variable "auth_username" {
  description = "Basic auth username for the UI"
  type        = string
}

variable "auth_password" {
  description = "Basic auth password for the UI"
  type        = string
  sensitive   = true
}

# LLM
variable "llm_base_url" {
  description = "LLM endpoint for clip content analysis (empty disables it)"
  type        = string
  default     = ""
}

variable "llm_model" {
  description = "Vision model name served at llm_base_url"
  type        = string
  default     = ""
}

variable "llm_api_key" {
  description = "API key for llm_base_url"
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
  description = "Schedule on NVIDIA GPU nodes"
  type        = bool
  default     = false
}

variable "gpu_count" {
  description = "Number of GPUs to request"
  type        = number
  default     = 1
}

# Storage
variable "storage_class_name" {
  description = "Storage class for PVCs"
  type        = string
  default     = null
}

# Ingress
variable "ingress_host" {
  description = "Ingress hostname"
  type        = string
}
