# Release and ARC Storage Hardening Design

## Goal

Fix the failed PyPI publication and stop GPU integration runners from being evicted when a job installs or generates more data than the Kubernetes node can safely hold.

## Scope

Only checked-in files in these repositories may change:

- `immich-video-memory-generator`
- `rancher-cluster`

No command may mutate the live Kubernetes cluster. Do not apply Terraform, run mutating `kubectl` commands, SSH to workers, edit RKE2 configuration, restart `rke2-agent`, rerun GitHub workflows, publish packages, or push branches as part of this work.

## Observed Failures

The PyPI job rejected the `0.38.0` wheel because it uses core metadata 2.5 while the pinned `pypa/gh-action-pypi-publish` revision contains Twine 6.1, which supports metadata through 2.4.

The GPU integration runner was evicted after using roughly 9.6 GiB of ephemeral storage. The current ARC values mount a persistent volume only at `/home/runner/.cache/uv`; the checkout and `.venv` under `/home/runner/_work` remain on nodefs. `/tmp` is also node-backed through an `emptyDir`.

## Application Repository Changes

Update `.github/workflows/release.yml` to pin `pypa/gh-action-pypi-publish` to commit `dc37677b2e1c63e2034f94d8a5b11f265b73ba33`, the `release/v1.14` revision that includes Twine 7 and metadata 2.5 support.

Update `.github/workflows/integration.yml` to install dependencies with `make dev-test` instead of `make dev`. The integration workflow does not run the optional Demucs/audio-ML suite, so installing every extra only pulls several gigabytes of unused PyTorch and CUDA packages.

Add repository-level regression checks that parse the workflows and assert both pins. The checks must fail against the old workflow text and pass after the edits.

## Cluster Repository Changes

### Ephemeral StorageClass

Add a non-default `proxmox-ephemeral-xfs` StorageClass beside `proxmox-data-xfs` in `04-storage/storage_class.tf`. It uses the same Proxmox CSI provisioner, XFS filesystem, local storage parameters, `WaitForFirstConsumer`, and volume expansion support. Its reclaim policy is `Delete`, so generic ephemeral runner volumes are removed when their owning pods disappear.

The existing default `proxmox-data-xfs` StorageClass remains unchanged with `Retain` semantics for durable application data.

### Per-runner volumes

Replace the runner's node-backed `tmp` `emptyDir` in `55-github-arc/arc.tf` with two generic ephemeral volumes:

- `work`: a 40 GiB claim using `proxmox-ephemeral-xfs`, mounted at `/home/runner/_work`.
- `tmp`: a 30 GiB claim using `proxmox-ephemeral-xfs`, mounted at `/tmp`.

Each runner receives separate claims through `volumeClaimTemplate`; concurrent runners cannot share or corrupt a workspace.

Declare an 8 GiB `ephemeral-storage` request and a 16 GiB limit for the remaining writable image layer and logs. The large, variable job data belongs on the two CSI volumes rather than in this allowance.

Expand `arc-uv-cache` in `55-github-arc/cache.tf` from 30 GiB to 50 GiB. The existing StorageClass supports online expansion. This is a declarative change only; applying it is outside this task.

Delete `55-github-arc/Dockerfile.runner`. Nothing builds or references it, and introducing a private runner-image publishing and authentication path is unrelated to the storage failure.

## Deployment Ordering

When the owner later deploys these changes, `04-storage` must be applied before `55-github-arc` because the runner claims reference the new StorageClass by name. Deployment itself is explicitly outside this task.

## Validation

Application validation:

- Run the targeted regression checks for the two workflow changes.
- Parse all GitHub workflow YAML files.
- Run the repository's relevant CI/workflow validation target if one exists.

Cluster validation:

- Run `terraform fmt -check -recursive` in the affected Terraform modules.
- Run `terraform validate` in `04-storage` and `55-github-arc` when their existing initialized provider caches permit offline validation.
- Render or inspect the Helm values and assert the two ephemeral claims, mount paths, sizes, and container storage resources.
- Confirm the default durable StorageClass still uses `Retain` and the new runner StorageClass uses `Delete`.

Validation must not contact or mutate the live cluster. A local validation blocked by missing provider initialization or backend credentials must be reported rather than bypassed with a live apply.

## Success Criteria

- The publisher action supports metadata 2.5.
- GPU integration no longer installs unused audio-ML dependencies.
- Runner checkout, virtual environment, and temporary media files no longer consume nodefs.
- Per-job volumes are automatically reclaimed.
- The uv cache has 50 GiB capacity in source configuration.
- No live worker or cluster state is changed.
