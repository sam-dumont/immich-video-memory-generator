### Immich test instance for immich-memories' public E2E households
#
# DRAFT for rancher-cluster: copy to 50-internal-services/immich-test.tf. It references
# the family instance's `kubernetes_namespace.immich` and `kubernetes_service_v1.immich_ml`
# from immich.tf in the same folder.
#
# A second Immich, separate from the family one: seven users (one per public test
# household, built from openly licensed pictures), never anyone's own photos. It keeps
# its own Postgres, Valkey and library, and borrows the family instance's machine
# learning service (stateless) for faces, CLIP and OCR instead of running its own.
#
# Internal only: a MetalLB address on the couronne pool, no ingress, no certificate, no
# public DNS. Users and API keys are created through the Immich API, not here:
# `make public-e2e-provision` in immich-memories does it.
#
# Server, Postgres and Valkey are pinned to the same digests as immich-memories' Immich
# Gate. The family ML runs var.immich_version (v3.1.0 today); the ML API is the same
# across v3 minors, and both use the default face and CLIP models.

locals {
  immich_test_server_image   = "ghcr.io/immich-app/immich-server:v3.2.2@sha256:79cc1623323d5894922686d8743b4780181428f98eecbfb58ce12c41ef02d1ea"
  immich_test_postgres_image = "ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0@sha256:bcf63357191b76a916ae5eb93464d65c07511da41e3bf7a8416db519b40b1c23"
  immich_test_valkey_image   = "docker.io/valkey/valkey:9@sha256:70739f85ad2ee01a726a965584a0f94895f01b0c60b3cc8b0aeef11eaa6888cf"
  # The family instance's ML service, reached across namespaces.
  immich_test_ml_url = "http://${kubernetes_service_v1.immich_ml.metadata.0.name}.${kubernetes_namespace.immich.metadata.0.name}.svc.cluster.local:3003"
}

resource "kubernetes_namespace" "immich_test" {
  metadata {
    name = "immich-test"
  }
}

resource "kubernetes_service_account_v1" "immich_test" {
  metadata {
    name      = "immich-test"
    namespace = kubernetes_namespace.immich_test.metadata.0.name
  }
}

resource "kubernetes_deployment_v1" "immich_test_postgres" {
  metadata {
    name      = "immich-test-postgres"
    namespace = kubernetes_namespace.immich_test.metadata.0.name
    labels = {
      name = "immich-test-postgres"
    }
  }

  spec {
    replicas = 1

    selector {
      match_labels = {
        name = "immich-test-postgres"
      }
    }

    strategy {
      type = "Recreate"
    }

    template {
      metadata {
        labels = {
          name = "immich-test-postgres"
        }
      }

      spec {
        service_account_name = kubernetes_service_account_v1.immich_test.metadata.0.name
        node_selector = {
          "topology.kubernetes.io/region" = "couronne"
        }
        container {
          image             = local.immich_test_postgres_image
          image_pull_policy = "IfNotPresent"
          name              = "postgres"
          args              = ["-c", "shared_preload_libraries=vchord.so"]

          volume_mount {
            name       = "pgdata"
            mount_path = "/var/lib/postgresql/data"
            sub_path   = "data"
          }

          resources {
            limits = {
              cpu    = "1"
              memory = "1Gi"
            }
            requests = {
              cpu    = "250m"
              memory = "512Mi"
            }
          }

          # ClusterIP only, like the family instance's database.
          env {
            name  = "POSTGRES_PASSWORD"
            value = "postgres"
          }
          env {
            name  = "POSTGRES_USER"
            value = "postgres"
          }
          env {
            name  = "POSTGRES_DB"
            value = "immich"
          }
          env {
            name  = "POSTGRES_INITDB_ARGS"
            value = "--data-checksums"
          }
          port {
            container_port = 5432
          }
        }
        volume {
          name = "pgdata"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim_v1.immich_test_postgres.metadata[0].name
          }
        }
      }
    }
  }
}

resource "kubernetes_deployment_v1" "immich_test_redis" {
  metadata {
    name      = "immich-test-redis"
    namespace = kubernetes_namespace.immich_test.metadata.0.name
    labels = {
      name = "immich-test-redis"
    }
  }

  spec {
    replicas = 1

    strategy {
      type = "Recreate"
    }

    selector {
      match_labels = {
        name = "immich-test-redis"
      }
    }

    template {
      metadata {
        labels = {
          name = "immich-test-redis"
        }
      }

      spec {
        service_account_name = kubernetes_service_account_v1.immich_test.metadata.0.name
        node_selector = {
          "topology.kubernetes.io/region" = "couronne"
        }
        container {
          image             = local.immich_test_valkey_image
          image_pull_policy = "IfNotPresent"
          name              = "redis"
          readiness_probe {
            exec {
              command = ["redis-cli", "ping"]
            }
            initial_delay_seconds = 5
            period_seconds        = 10
          }
          port {
            container_port = 6379
          }
        }
      }
    }
  }
}

resource "kubernetes_deployment_v1" "immich_test" {
  metadata {
    name      = "immich-test"
    namespace = kubernetes_namespace.immich_test.metadata.0.name
    labels = {
      name = "immich-test"
    }
  }

  spec {
    replicas = 1

    strategy {
      type = "Recreate"
    }

    selector {
      match_labels = {
        name = "immich-test"
      }
    }

    template {
      metadata {
        labels = {
          name = "immich-test"
        }
      }

      spec {
        service_account_name = kubernetes_service_account_v1.immich_test.metadata.0.name
        node_selector = {
          "topology.kubernetes.io/region" = "couronne"
        }
        container {
          image             = local.immich_test_server_image
          image_pull_policy = "IfNotPresent"
          name              = "immich"

          volume_mount {
            name       = "library"
            mount_path = "/data"
          }

          resources {
            limits = {
              cpu    = 4
              memory = "4Gi"
            }
            requests = {
              cpu    = 1
              memory = "1Gi"
            }
          }

          env {
            name  = "IMMICH_PORT"
            value = "2283"
          }
          env {
            name  = "DB_PASSWORD"
            value = "postgres"
          }
          env {
            name  = "DB_HOSTNAME"
            value = "immich-test-postgres"
          }
          env {
            name  = "DB_DATABASE_NAME"
            value = "immich"
          }
          env {
            name  = "REDIS_HOSTNAME"
            value = "immich-test-redis"
          }
          env {
            name  = "LOG_LEVEL"
            value = "log"
          }
          # Machine learning stays on; it runs in the family instance's ML pod.
          env {
            name  = "IMMICH_MACHINE_LEARNING_ENABLED"
            value = "true"
          }
          env {
            name  = "IMMICH_MACHINE_LEARNING_URL"
            value = local.immich_test_ml_url
          }
          # The households' capture dates are written in UTC.
          env {
            name  = "TZ"
            value = "UTC"
          }

          port {
            container_port = 2283
          }

          readiness_probe {
            http_get {
              path = "/api/server/ping"
              port = 2283
            }
            initial_delay_seconds = 10
            period_seconds        = 10
          }
        }

        volume {
          name = "library"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim_v1.immich_test_library.metadata[0].name
          }
        }
      }
    }
  }
}

# Internal only: a fixed MetalLB address on the couronne pool (10.2.254.50-73), no
# ingress. .58 was free on 2026-09-25; check before applying:
#   kubectl get svc -A | grep LoadBalancer
resource "kubernetes_service_v1" "immich_test" {
  metadata {
    name      = "immich-test"
    namespace = kubernetes_namespace.immich_test.metadata.0.name
    labels = {
      name = "immich-test"
    }
    annotations = {
      "metallb.io/loadBalancerIPs" = "10.2.254.58,2a02:a03f:6235:4300:15::58"
    }
  }

  spec {
    internal_traffic_policy = "Cluster"
    ip_families             = ["IPv6", "IPv4"]
    ip_family_policy        = "PreferDualStack"
    selector = {
      name = kubernetes_deployment_v1.immich_test.metadata.0.name
    }
    port {
      name        = "api"
      port        = 2283
      target_port = 2283
    }

    type = "LoadBalancer"
  }
}

resource "kubernetes_service_v1" "immich_test_postgres" {
  metadata {
    name      = "immich-test-postgres"
    namespace = kubernetes_namespace.immich_test.metadata.0.name
    labels = {
      name = "immich-test-postgres"
    }
  }
  spec {
    selector = {
      name = kubernetes_deployment_v1.immich_test_postgres.metadata.0.name
    }
    port {
      port        = 5432
      target_port = 5432
    }

    type = "ClusterIP"
  }
}

resource "kubernetes_service_v1" "immich_test_redis" {
  metadata {
    name      = "immich-test-redis"
    namespace = kubernetes_namespace.immich_test.metadata.0.name
    labels = {
      name = "immich-test-redis"
    }
  }
  spec {
    selector = {
      name = kubernetes_deployment_v1.immich_test_redis.metadata.0.name
    }
    port {
      port        = 6379
      target_port = 6379
    }

    type = "ClusterIP"
  }
}

# Seven households of 0.5 to 2.5 GB each, originals plus Immich's thumbnails.
resource "kubernetes_persistent_volume_claim_v1" "immich_test_library" {
  metadata {
    name      = "immich-test-library"
    namespace = kubernetes_namespace.immich_test.metadata[0].name
  }

  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = "proxmox-data-xfs"
    resources {
      requests = {
        storage = "60Gi"
      }
    }
  }
  wait_until_bound = false
}

resource "kubernetes_persistent_volume_claim_v1" "immich_test_postgres" {
  metadata {
    name      = "immich-test-postgres"
    namespace = kubernetes_namespace.immich_test.metadata[0].name
  }

  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = "proxmox-data-xfs"
    resources {
      requests = {
        storage = "10Gi"
      }
    }
  }
  wait_until_bound = false
}
