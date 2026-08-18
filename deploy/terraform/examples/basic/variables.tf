variable "immich_url" {
  description = "URL of your Immich instance"
  type        = string
}

variable "immich_api_key" {
  description = "Immich API key"
  type        = string
  sensitive   = true
}

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

variable "gpu_enabled" {
  description = "Schedule on NVIDIA GPU nodes"
  type        = bool
  default     = false
}
