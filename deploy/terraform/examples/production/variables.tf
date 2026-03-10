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
  description = "Container image tag"
  type        = string
  default     = "latest"
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

# Ollama
variable "ollama_url" {
  description = "URL of Ollama service"
  type        = string
  default     = "http://ollama.ollama.svc.cluster.local:11434"
}
