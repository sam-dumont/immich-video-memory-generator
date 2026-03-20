# GPU Integration Tests — Architecture & Security

## Why a private mirror?

Self-hosted runners (with GPU access) **cannot safely run on public repos**. Anyone can
fork the repo, submit a PR with a modified test file (crypto miner, data exfiltration),
and the runner would execute it. GitHub's own docs warn against this.

## Architecture

```
PUBLIC REPO (sam-dumont/immich-video-memory-generator)
├── ci.yml ──► unit tests on GitHub-hosted runners (free, safe)
│              harden-runner monitors network egress
└── mirror.yml ──► git push to private mirror (SSH deploy key)
                   triggers: push to main (automatic)
                             workflow_dispatch (manual, for testing branches)
                   NEVER triggers on: pull_request (fork abuse vector)
                              │
                              ▼
PRIVATE REPO (sam-dumont/immich-memories-ci)
└── integration.yml ──► GPU integration tests
                        triggered by: repository_dispatch from mirror.yml
                        runs-on: gpu (ARC K8s runner with NVIDIA GPU)
                        posts status back to public repo: "Integration (GPU)" ✅/❌
                              │
                              ▼
K8S CLUSTER (rancher-cluster/55-github-arc)
└── ARC runner pods
    ├── NVIDIA runtimeClass + time-sliced GPU
    ├── PVC cache for uv packages (10Gi, survives pod restarts)
    ├── emptyDir /tmp (20Gi, ephemeral per job)
    ├── Scoped to private repo ONLY (not org-level)
    └── Scale 0→2 on demand, ephemeral (pod dies after each job)
```

## Security layers

| Layer | Protection |
|-------|-----------|
| No `pull_request` trigger | Fork PRs never reach GPU runner |
| `workflow_dispatch` only for branches | Only repo owner can trigger manually |
| `github.repository` guard | Extra check against fork execution |
| ARC scoped to private repo | Runner only accepts jobs from `immich-memories-ci` |
| Ephemeral pods | Each job gets a fresh container, no persistence |
| SSH deploy key (not PAT) | Narrowly scoped: write access to one repo only |
| `harden-runner` on public CI | Monitors network egress, detects supply chain attacks |

## Running GPU tests

**Automatic (post-merge):** Every push to `main` triggers integration tests automatically.

**Manual (pre-merge):** Test a specific branch before merging:
```bash
gh workflow run mirror.yml -f branch=feat/my-feature
```

**Direct (on private repo):**
```bash
gh workflow run integration.yml -R sam-dumont/immich-memories-ci -f suite=assembly
```

## Config

The runner gets `~/.immich-memories/config.yaml` from the `IMMICH_MEMORIES_CONFIG` secret
(base64-encoded). Paths are overridden via env vars (pydantic-settings):

- `IMMICH_MEMORIES_CACHE__DIRECTORY=/tmp/immich-cache`
- `IMMICH_MEMORIES_CACHE__DATABASE=/tmp/immich-cache/cache.db`
- `IMMICH_MEMORIES_OUTPUT__DIRECTORY=/tmp/immich-output`
- `UV_CACHE_DIR=/home/runner/.cache/uv` (PVC-backed, persistent)

## Updating config

When your local config changes:
```bash
cat ~/.immich-memories/config.yaml | base64 | gh secret set IMMICH_MEMORIES_CONFIG -R sam-dumont/immich-memories-ci
```
