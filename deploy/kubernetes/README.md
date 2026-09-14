# Kubernetes deployment

The app manifests use one replica, a read-only root filesystem and UID/GID 1000. The base
requests no GPU; `overlays/gpu` adds NVIDIA scheduling. Enable authentication before exposing
the Service beyond a private port-forward.

```bash
cd deploy/kubernetes
cp base/secret.yaml.example base/secret.yaml
# Fill in the Immich URL and API key in base/secret.yaml.
kubectl kustomize base
kubectl apply -k base   # or overlays/gpu
kubectl exec -n immich-memories deployment/immich-memories -- immich-memories models fetch
kubectl port-forward -n immich-memories svc/immich-memories 8080:80
```

Set `editorial.preparation.tier` to `no_captions` for a standalone app; `full` requires a caption
server. Leave `llm.model` blank for the rules reader. The image tag is pinned in
`base/kustomization.yaml`; check the release notes when upgrading.

| Claim | Default | Mounts |
|---|---|---|
| `immich-memories-cache` | 50Gi | `~/.immich-memories`, `~/.cache` (`library-cache`), `/tmp` (`scratch`) |
| `immich-memories-output` | 50Gi | `/app/output` |
| `immich-memories-models` | 5Gi | `/models` |

The state budget includes 10 GB of thumbnails, 10 GB of videos and 2 GB of clip previews at
configured defaults, plus banks, active downloads and scratch. Increase it for larger scopes or
local music models. `fsGroup: 1000` must grant write access through your storage driver. Scratch
persists: clean it only while app and batch pods are stopped. Back up the state and outputs;
`cache backup` covers only `cache.db`, not `cache/annotations.sqlite`.

`base/job.yaml` contains optional CLI jobs sharing these claims. With `ReadWriteOnce` they need
the same node or a stopped Deployment. The daily automation timer avoids a separate CronJob.

The optional inference service has its own 10Gi PVC, shared by `/cache` and `/tmp` (`scratch`).
Keep that claim separate from the app so the service can run on another node. Apply
`overlays/inference` for CPU or `overlays/inference-cuda` for NVIDIA; create the namespace first
if running the service alone. `ALLOW_MODEL_DOWNLOADS=true` seeds the pinned exports and detector
snapshot on first use. Set the app's `IMMICH_MEMORIES_INFERENCE__FACTS_BASE_URL` to
`http://inference:8092` to use it. `overlays/inference-lan` adds an optional private LoadBalancer
Service; the inference endpoint has no authentication.

The tests render Kustomize and check deployment contracts. They do not apply these manifests to
a live cluster. Full instructions, storage, probes and network policy:
[Kubernetes guide](../../docs-site/docs/deploy/installation/kubernetes.md).
