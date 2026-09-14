# GPU integration tests

The public repository's [mirror workflow](../.github/workflows/mirror.yml) pushes code to the
private `sam-dumont/immich-memories-ci` repository and dispatches integration tests there.
It runs after pushes to `main`, or through a manual dispatch. Fork pull requests do not trigger
it. Public CI runs on GitHub-hosted runners.

To request a run for a branch you have reviewed:

```bash
gh workflow run mirror.yml -f branch=feat/my-feature
```

The workflow uses an SSH deploy key for mirroring and a separate token for dispatch and commit
status. Its `Integration (GPU)` status reports the private runner's result on the public commit.
Access to the private repository is required to inspect that workflow and its logs.

Runner configuration, secrets, volumes and resource limits are maintained with the private
runner deployment. They cannot be verified from this checkout. Check that deployment before
changing storage or assuming a particular GPU is available.

For local tests, use the [testing guide](https://sam-dumont.github.io/immich-video-memory-generator/docs/contribute/testing).
Do not copy a personal config into CI without reviewing its credentials and paths.
