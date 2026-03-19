terraform {
  required_version = ">= 1.0"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.20"
    }
  }
}

locals {
  labels = merge({
    "app.kubernetes.io/name"       = "immich-memories"
    "app.kubernetes.io/component"  = "video-compiler"
    "app.kubernetes.io/managed-by" = "terraform"
  }, var.labels)

  config_yaml = yamlencode({
    immich = {
      url     = "$${IMMICH_URL}"
      api_key = "$${IMMICH_API_KEY}"
    }
    defaults = {
      target_duration_seconds = var.target_duration_seconds
      output_orientation      = var.output_orientation
      scale_mode              = "smart_crop"
      transition              = "crossfade"
      transition_duration     = 0.5
    }
    analysis = {
      scene_threshold          = 27.0
      min_scene_duration       = 1.0
      duplicate_hash_threshold = 8
      keyframe_interval        = 1.0
    }
    output = {
      directory  = "/output"
      format     = "mp4"
      resolution = var.output_resolution
      codec      = "h264"
      crf        = 18
    }
    hardware = {
      enabled        = var.gpu_enabled
      backend        = var.hardware_backend
      encoder_preset = "balanced"
      gpu_decode     = true
      gpu_analysis   = true
    }
    audio = {
      auto_music        = false
      music_source      = "musicgen"
      ollama_url        = var.ollama_url
      ollama_model      = var.ollama_model
      ducking_threshold = 0.02
      ducking_ratio     = 6.0
      music_volume_db   = -6.0
    }
    musicgen = {
      enabled  = var.musicgen_enabled
      base_url = var.musicgen_base_url
      # API key passed via env var
    }
  })
}

# Namespace
resource "kubernetes_namespace" "this" {
  count = var.create_namespace ? 1 : 0

  metadata {
    name   = var.namespace
    labels = local.labels
  }
}

# Secret
resource "kubernetes_secret" "this" {
  metadata {
    name      = "immich-memories-secrets"
    namespace = var.namespace
    labels    = local.labels
  }

  data = {
    IMMICH_URL      = var.immich_url
    IMMICH_API_KEY  = var.immich_api_key
    OPENAI_API_KEY  = var.openai_api_key
    MUSICGEN_API_KEY = var.musicgen_api_key
  }

  type = "Opaque"

  depends_on = [kubernetes_namespace.this]
}

# ConfigMap
resource "kubernetes_config_map" "this" {
  metadata {
    name      = "immich-memories-config"
    namespace = var.namespace
    labels    = local.labels
  }

  data = {
    "config.yaml" = local.config_yaml
  }

  depends_on = [kubernetes_namespace.this]
}

# Output PVC
resource "kubernetes_persistent_volume_claim" "output" {
  metadata {
    name      = "immich-memories-output"
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = var.storage_class_name

    resources {
      requests = {
        storage = var.output_storage_size
      }
    }
  }

  depends_on = [kubernetes_namespace.this]
}

# Cache PVC
resource "kubernetes_persistent_volume_claim" "cache" {
  metadata {
    name      = "immich-memories-cache"
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = var.storage_class_name

    resources {
      requests = {
        storage = var.cache_storage_size
      }
    }
  }

  depends_on = [kubernetes_namespace.this]
}

# Deployment
resource "kubernetes_deployment" "this" {
  metadata {
    name      = "immich-memories"
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    replicas = var.replicas

    selector {
      match_labels = {
        "app.kubernetes.io/name" = "immich-memories"
      }
    }

    template {
      metadata {
        labels = local.labels
      }

      spec {
        runtime_class_name = var.gpu_enabled ? var.runtime_class_name : null

        # Pod-level security context
        security_context {
          run_as_non_root = true
          run_as_user     = 1000
          run_as_group    = 1000
          fs_group        = 1000

          seccomp_profile {
            type = "RuntimeDefault"
          }
        }

        container {
          name              = "immich-memories"
          image             = "${var.image_repository}:${var.image_tag}"
          image_pull_policy = "Always"

          # Container-level security context
          security_context {
            allow_privilege_escalation = false
            read_only_root_filesystem  = false # NiceGUI needs write access

            capabilities {
              drop = ["ALL"]
            }
          }

          port {
            name           = "http"
            container_port = 8080
            protocol       = "TCP"
          }

          env {
            name = "IMMICH_URL"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.this.metadata[0].name
                key  = "IMMICH_URL"
              }
            }
          }

          env {
            name = "IMMICH_API_KEY"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.this.metadata[0].name
                key  = "IMMICH_API_KEY"
              }
            }
          }

          env {
            name = "OPENAI_API_KEY"
            value_from {
              secret_key_ref {
                name     = kubernetes_secret.this.metadata[0].name
                key      = "OPENAI_API_KEY"
                optional = true
              }
            }
          }

          # MusicGen AI music generation
          dynamic "env" {
            for_each = var.musicgen_enabled ? [1] : []
            content {
              name  = "IMMICH_MEMORIES_MUSICGEN__ENABLED"
              value = "true"
            }
          }

          dynamic "env" {
            for_each = var.musicgen_enabled ? [1] : []
            content {
              name  = "IMMICH_MEMORIES_MUSICGEN__BASE_URL"
              value = var.musicgen_base_url
            }
          }

          dynamic "env" {
            for_each = var.musicgen_enabled ? [1] : []
            content {
              name = "IMMICH_MEMORIES_MUSICGEN__API_KEY"
              value_from {
                secret_key_ref {
                  name     = kubernetes_secret.this.metadata[0].name
                  key      = "MUSICGEN_API_KEY"
                  optional = true
                }
              }
            }
          }

          dynamic "env" {
            for_each = var.gpu_enabled ? [1] : []
            content {
              name  = "NVIDIA_VISIBLE_DEVICES"
              value = "all"
            }
          }

          dynamic "env" {
            for_each = var.gpu_enabled ? [1] : []
            content {
              name  = "NVIDIA_DRIVER_CAPABILITIES"
              value = "compute,video,utility"
            }
          }

          resources {
            requests = merge(
              {
                memory = var.resources.requests.memory
                cpu    = var.resources.requests.cpu
              },
              var.gpu_enabled ? { "nvidia.com/gpu" = tostring(var.gpu_count) } : {}
            )
            limits = merge(
              {
                memory = var.resources.limits.memory
                cpu    = var.resources.limits.cpu
              },
              var.gpu_enabled ? { "nvidia.com/gpu" = tostring(var.gpu_count) } : {}
            )
          }

          volume_mount {
            name       = "config"
            mount_path = "/home/immich/.immich-memories"
            read_only  = true
          }

          volume_mount {
            name       = "output"
            mount_path = "/output"
          }

          volume_mount {
            name       = "cache"
            mount_path = "/home/immich/.cache/immich-memories"
          }

          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }

          liveness_probe {
            http_get {
              path = "/"
              port = "http"
            }
            initial_delay_seconds = 30
            period_seconds        = 10
            timeout_seconds       = 5
            failure_threshold     = 3
          }

          readiness_probe {
            http_get {
              path = "/"
              port = "http"
            }
            initial_delay_seconds = 10
            period_seconds        = 5
            timeout_seconds       = 3
            failure_threshold     = 3
          }
        }

        volume {
          name = "config"
          config_map {
            name = kubernetes_config_map.this.metadata[0].name
            items {
              key  = "config.yaml"
              path = "config.yaml"
            }
          }
        }

        volume {
          name = "output"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim.output.metadata[0].name
          }
        }

        volume {
          name = "cache"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim.cache.metadata[0].name
          }
        }

        volume {
          name = "tmp"
          empty_dir {
            size_limit = "2Gi"
          }
        }

        dynamic "node_selector" {
          for_each = var.gpu_enabled ? [var.gpu_node_selector] : []
          content {
            # Node selector is a map, but Terraform requires dynamic block workaround
          }
        }

        dynamic "toleration" {
          for_each = var.gpu_enabled ? [1] : []
          content {
            key      = "nvidia.com/gpu"
            operator = "Exists"
            effect   = "NoSchedule"
          }
        }
      }
    }
  }

  depends_on = [
    kubernetes_namespace.this,
    kubernetes_config_map.this,
    kubernetes_secret.this,
    kubernetes_persistent_volume_claim.output,
    kubernetes_persistent_volume_claim.cache,
  ]
}

# Service
resource "kubernetes_service" "this" {
  metadata {
    name      = "immich-memories"
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    type = "ClusterIP"

    port {
      name        = "http"
      port        = 80
      target_port = "http"
      protocol    = "TCP"
    }

    selector = {
      "app.kubernetes.io/name" = "immich-memories"
    }
  }

  depends_on = [kubernetes_namespace.this]
}

# Ingress (optional)
resource "kubernetes_ingress_v1" "this" {
  count = var.ingress_enabled ? 1 : 0

  metadata {
    name        = "immich-memories"
    namespace   = var.namespace
    labels      = local.labels
    annotations = var.ingress_annotations
  }

  spec {
    ingress_class_name = var.ingress_class_name

    rule {
      host = var.ingress_host

      http {
        path {
          path      = "/"
          path_type = "Prefix"

          backend {
            service {
              name = kubernetes_service.this.metadata[0].name
              port {
                name = "http"
              }
            }
          }
        }
      }
    }

    dynamic "tls" {
      for_each = var.ingress_tls_enabled ? [1] : []
      content {
        hosts       = [var.ingress_host]
        secret_name = var.ingress_tls_secret_name
      }
    }
  }

  depends_on = [kubernetes_service.this]
}
