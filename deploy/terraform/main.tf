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

  # The image runs as `immich`, UID/GID 1000, HOME=/home/immich. The app writes
  # config.yaml, cache.db, the video cache and automation history under
  # ~/.immich-memories, so that directory is a writable PVC, not a ConfigMap.
  data_dir   = "/home/immich/.immich-memories"
  output_dir = "/app/output"

  # Everything is configured through IMMICH_MEMORIES_<SECTION>__<KEY> env vars,
  # the same way docker-compose does it. Secrets live in the Secret (envFrom).
  env = merge(
    {
      IMMICH_MEMORIES_OUTPUT__DIRECTORY  = local.output_dir
      IMMICH_MEMORIES_OUTPUT__RESOLUTION = var.output_resolution
    },
    var.llm_base_url != "" ? {
      IMMICH_MEMORIES_LLM__BASE_URL             = var.llm_base_url
      IMMICH_MEMORIES_LLM__MODEL                = var.llm_model
      IMMICH_MEMORIES_CONTENT_ANALYSIS__ENABLED = "true"
    } : {},
    var.musicgen_enabled ? {
      IMMICH_MEMORIES_MUSICGEN__ENABLED  = "true"
      IMMICH_MEMORIES_MUSICGEN__BASE_URL = var.musicgen_base_url
    } : {},
    var.gpu_enabled ? {
      NVIDIA_VISIBLE_DEVICES     = "all"
      NVIDIA_DRIVER_CAPABILITIES = "compute,video,utility"
    } : {},
    var.env,
  )

  secret_data = merge(
    {
      IMMICH_URL     = var.immich_url
      IMMICH_API_KEY = var.immich_api_key
    },
    var.llm_api_key != "" ? { IMMICH_MEMORIES_LLM__API_KEY = var.llm_api_key } : {},
    var.musicgen_api_key != "" ? { IMMICH_MEMORIES_MUSICGEN__API_KEY = var.musicgen_api_key } : {},
    var.secret_env,
  )
}

# Namespace
resource "kubernetes_namespace_v1" "this" {
  count = var.create_namespace ? 1 : 0

  metadata {
    name   = var.namespace
    labels = local.labels
  }
}

# Secret
resource "kubernetes_secret_v1" "this" {
  metadata {
    name      = "immich-memories-secrets"
    namespace = var.namespace
    labels    = local.labels
  }

  data = local.secret_data
  type = "Opaque"

  depends_on = [kubernetes_namespace_v1.this]
}

# Output PVC (generated videos)
resource "kubernetes_persistent_volume_claim_v1" "output" {
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

  # WaitForFirstConsumer storage classes never bind before a pod mounts the PVC.
  wait_until_bound = false

  depends_on = [kubernetes_namespace_v1.this]
}

# Cache/state PVC (config.yaml, cache.db, video cache, automation history)
resource "kubernetes_persistent_volume_claim_v1" "cache" {
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

  wait_until_bound = false

  depends_on = [kubernetes_namespace_v1.this]
}

# Deployment
resource "kubernetes_deployment_v1" "this" {
  metadata {
    name      = "immich-memories"
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    # Single-user, single-replica: workflow state lives in-process.
    replicas = var.replicas

    strategy {
      type = "Recreate" # both PVCs are ReadWriteOnce
    }

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
        node_selector      = var.gpu_enabled ? var.gpu_node_selector : null

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

          security_context {
            allow_privilege_escalation = false
            # Sessions now live on the data volume, but ~/.cache still needs
            # a writable mount before this can flip to true (#445).
            read_only_root_filesystem = false

            capabilities {
              drop = ["ALL"]
            }
          }

          port {
            name           = "http"
            container_port = 8080
            protocol       = "TCP"
          }

          env_from {
            secret_ref {
              name = kubernetes_secret_v1.this.metadata[0].name
            }
          }

          dynamic "env" {
            for_each = local.env
            content {
              name  = env.key
              value = env.value
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
            name       = "data"
            mount_path = local.data_dir
          }

          volume_mount {
            name       = "output"
            mount_path = local.output_dir
          }

          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }

          # /health/live only says the process is up.
          liveness_probe {
            http_get {
              path = "/health/live"
              port = "http"
            }
            initial_delay_seconds = 15
            period_seconds        = 10
            timeout_seconds       = 5
            failure_threshold     = 3
          }

          # /health/ready is 503 until config is present and Immich answers.
          readiness_probe {
            http_get {
              path = "/health/ready"
              port = "http"
            }
            initial_delay_seconds = 10
            period_seconds        = 15
            timeout_seconds       = 5
            failure_threshold     = 3
          }
        }

        volume {
          name = "data"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim_v1.cache.metadata[0].name
          }
        }

        volume {
          name = "output"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim_v1.output.metadata[0].name
          }
        }

        # FFmpeg intermediates: 2Gi is enough for 1080p, use 8Gi for 4K.
        volume {
          name = "tmp"
          empty_dir {
            size_limit = var.tmp_size
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
    kubernetes_namespace_v1.this,
    kubernetes_secret_v1.this,
    kubernetes_persistent_volume_claim_v1.output,
    kubernetes_persistent_volume_claim_v1.cache,
  ]
}

# Service
resource "kubernetes_service_v1" "this" {
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

  depends_on = [kubernetes_namespace_v1.this]
}

# Ingress (optional). Authentication is disabled by default: enable it before
# turning this on.
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
              name = kubernetes_service_v1.this.metadata[0].name
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

  depends_on = [kubernetes_service_v1.this]
}
