# Release and ARC Storage Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the PyPI release incompatibility and prevent the self-hosted GPU job from consuming unrequested node ephemeral storage, using repository changes only.

**Architecture:** Keep the application fix small and test it as workflow configuration: upgrade the publisher action and install the existing CI-specific dependency set. In the cluster repo, keep the reusable uv cache persistent but move runner work and temporary data to dynamically provisioned, per-pod PVCs on a dedicated `Delete` storage class; declare honest container ephemeral-storage resources for whatever remains on the node.

**Tech Stack:** GitHub Actions YAML, pytest/PyYAML, Terraform HCL, ARC Helm values, Kubernetes generic ephemeral volumes, Python `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-15-release-and-arc-storage-hardening-design.md`

## Global Constraints

- [ ] Change only `/Users/sam/Code/perso/immich-video-memory-generator` and `/Users/sam/Code/perso/rancher-cluster` source files.
- [ ] Do not mutate live Kubernetes resources, workers, RKE2 agent configuration, Terraform state, GitHub workflows, releases, or branches.
- [ ] Do not run `kubectl` mutations, SSH commands, `terraform apply`, workflow reruns, publishing, pushes, or service restarts.
- [ ] Preserve unrelated untracked files in both repositories.
- [ ] Stage and commit only the files named in each task.

---

### Task 1: Pin the metadata-2.5-compatible PyPI publisher

**Files:**
- Modify: `tests/test_docker_contract.py`
- Modify: `.github/workflows/release.yml:341`

- [ ] Add this workflow contract beside the existing release workflow test:

```python
def test_release_publisher_supports_core_metadata_2_5() -> None:
    """The PyPI publisher must understand metadata emitted by current build tooling."""
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "release.yml").read_text())
    steps = workflow["jobs"]["pypi-publish"]["steps"]
    publish_step = next(step for step in steps if step.get("name") == "Publish to PyPI")

    assert publish_step["uses"] == (
        "pypa/gh-action-pypi-publish@"
        "dc37677b2e1c63e2034f94d8a5b11f265b73ba33"
    )
```

- [ ] Run `uv run pytest tests/test_docker_contract.py::test_release_publisher_supports_core_metadata_2_5 -q` and confirm it fails because the workflow still pins `cef2210...`.
- [ ] Change the action pin to `pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # release/v1.14`.
- [ ] Run the same targeted test and confirm it passes.

### Task 2: Stop the GPU integration job from downloading unused CUDA/audio packages

**Files:**
- Modify: `tests/test_docker_contract.py`
- Modify: `.github/workflows/integration.yml:108`

- [ ] Add this contract test:

```python
def test_gpu_integration_uses_ci_dependency_set() -> None:
    """GPU integration needs Taichi, not the full Torch and audio-ML stack."""
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "integration.yml").read_text()
    )
    run_commands = [
        step.get("run")
        for step in workflow["jobs"]["integration"]["steps"]
        if "run" in step
    ]

    assert "make dev-test" in run_commands
    assert "make dev" not in run_commands
```

- [ ] Confirm the job key from the parsed workflow, then run `uv run pytest tests/test_docker_contract.py::test_gpu_integration_uses_ci_dependency_set -q`; expected failure is missing `make dev-test`.
- [ ] Change only the dependency-install command from `make dev` to the existing `make dev-test` target.
- [ ] Run the same targeted test and confirm it passes.
- [ ] Run `uv run ruff check tests/test_docker_contract.py` and `uv run pytest tests/test_docker_contract.py -q`.
- [ ] Commit only `.github/workflows/release.yml`, `.github/workflows/integration.yml`, and `tests/test_docker_contract.py` with `fix(ci): update publisher and trim GPU dependencies`.

### Task 3: Specify the ARC storage contract before changing Terraform

**Files:**
- Create: `/Users/sam/Code/perso/rancher-cluster/55-github-arc/tests/test_storage_contract.py`

- [ ] Add a dependency-free `unittest.TestCase` that reads `arc.tf`, `cache.tf`, `../04-storage/storage_class.tf`, and checks all of these exact contracts:
  - the `proxmox-ephemeral-xfs` resource uses the Proxmox CSI provisioner, XFS/local/SSD/no-cache parameters, expansion, `Delete`, and `WaitForFirstConsumer`;
  - `arc.tf` defines generic ephemeral `work` and `tmp` volumes with 40Gi and 30Gi claims on that class;
  - the runner mounts them at `/home/runner/_work` and `/tmp`;
  - the runner requests 8Gi and limits 16Gi of `ephemeral-storage`;
  - `cache.tf` requests 50Gi for the persistent uv cache;
  - `Dockerfile.runner` does not exist.
- [ ] Prefer narrow multiline snippet assertions over generic word-count assertions so the test proves the values are attached to the intended resources.
- [ ] Run `python3 -m unittest 55-github-arc/tests/test_storage_contract.py -v` from the cluster repo and confirm failures cover the absent storage class/volumes/resources, old 30Gi cache, and dead Dockerfile.

### Task 4: Add disposable runner storage and expand the persistent cache

**Files:**
- Modify: `/Users/sam/Code/perso/rancher-cluster/04-storage/storage_class.tf`
- Modify: `/Users/sam/Code/perso/rancher-cluster/55-github-arc/arc.tf`
- Modify: `/Users/sam/Code/perso/rancher-cluster/55-github-arc/cache.tf`
- Delete: `/Users/sam/Code/perso/rancher-cluster/55-github-arc/Dockerfile.runner`

- [ ] Add `kubernetes_storage_class_v1.proxmox_ephemeral_xfs` named `proxmox-ephemeral-xfs`, copying the existing Proxmox XFS parameters but omitting the default-class annotation and setting `reclaim_policy = "Delete"`.
- [ ] In `local.arc_gpu_values`, replace the node-backed `tmp` `emptyDir` with these Kubernetes generic ephemeral volumes:
  - `work`: 40Gi RWO claim on `proxmox-ephemeral-xfs`;
  - `tmp`: 30Gi RWO claim on `proxmox-ephemeral-xfs`.
- [ ] Mount `work` at `/home/runner/_work` and keep `tmp` at `/tmp`.
- [ ] Add `ephemeral-storage: "8Gi"` to runner requests and `ephemeral-storage: "16Gi"` to limits.
- [ ] Expand the shared `arc-uv-cache` PVC request from 30Gi to 50Gi; the existing class already has `allow_volume_expansion = true`.
- [ ] Delete the unused `Dockerfile.runner`; no build or Helm value references it.
- [ ] Run `python3 -m unittest 55-github-arc/tests/test_storage_contract.py -v` and confirm all contract tests pass.
- [ ] Run `terraform fmt -check 04-storage/storage_class.tf 55-github-arc/arc.tf 55-github-arc/cache.tf` from the cluster repo.
- [ ] Run `terraform -chdir=04-storage validate` and `terraform -chdir=55-github-arc validate` using only the existing initialized provider directories. Do not initialize providers or access the cluster; if either command requires that, report it instead of widening scope.
- [ ] Commit only the four Terraform/Dockerfile paths plus the new contract test with `fix(arc): provision runner work storage`.

### Task 5: Final local verification and scope audit

**Files:**
- Verify only; no new files expected.

- [ ] In the app repo, run:

```bash
uv run pytest \
  tests/test_docker_contract.py::test_release_publisher_supports_core_metadata_2_5 \
  tests/test_docker_contract.py::test_gpu_integration_uses_ci_dependency_set -q
uv run ruff check tests/test_docker_contract.py
git status --short --branch
git diff HEAD~1 --check
```

- [ ] In the cluster repo, run:

```bash
python3 -m unittest 55-github-arc/tests/test_storage_contract.py -v
terraform fmt -check 04-storage/storage_class.tf 55-github-arc/arc.tf 55-github-arc/cache.tf
git status --short --branch
git diff HEAD~1 --check
```

- [ ] Inspect the two commits and confirm no live-operation script, worker/RKE2 file, Terraform state, generated provider directory, or unrelated user file was changed.
- [ ] Report the exact passing commands, any validation that was safely skipped, both commit IDs, and the required deployment order (`04-storage` before `55-github-arc`) without applying or pushing anything.
