output "namespace" {
  description = "Kubernetes namespace"
  value       = var.namespace
}

output "service_name" {
  description = "Service name for internal access"
  value       = kubernetes_service.this.metadata[0].name
}

output "service_endpoint" {
  description = "Internal service endpoint"
  value       = "${kubernetes_service.this.metadata[0].name}.${var.namespace}.svc.cluster.local"
}

output "ingress_host" {
  description = "Ingress hostname (if enabled)"
  value       = var.ingress_enabled ? var.ingress_host : null
}

output "deployment_name" {
  description = "Deployment name"
  value       = kubernetes_deployment.this.metadata[0].name
}

output "pvc_output" {
  description = "Output PVC name"
  value       = kubernetes_persistent_volume_claim.output.metadata[0].name
}

output "pvc_cache" {
  description = "Cache PVC name"
  value       = kubernetes_persistent_volume_claim.cache.metadata[0].name
}

output "port_forward_command" {
  description = "Command to port-forward the UI"
  value       = "kubectl port-forward -n ${var.namespace} svc/${kubernetes_service.this.metadata[0].name} 8080:80"
}

output "gpu_enabled" {
  description = "Whether GPU support is enabled"
  value       = var.gpu_enabled
}
