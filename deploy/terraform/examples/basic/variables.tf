variable "immich_url" {
  description = "URL of your Immich instance"
  type        = string
}

variable "immich_api_key" {
  description = "Immich API key"
  type        = string
  sensitive   = true
}
