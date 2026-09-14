---
sidebar_position: 4
title: Setup matrix
---

# Running the setup matrix

`scripts/setup_matrix.py` compares one monthly memory across ten setups defined in
`scripts/setup_matrix.yaml`. Each cell prepares the same scope twice, then selects and renders
it. The report includes timings, peak memory, selected pictures and overlap with `mac-local`.
These are single observations, not a quality benchmark.

| Cell | Lane | Reader | Picture facts | Tier |
|---|---|---|---|---|
| `mac-local` | Mac | local model | in process | full |
| `mac-rules` | Mac | rules | in process | full |
| `nas-rules-local` | NAS | rules | in process | no_captions |
| `nas-rules-service` | NAS | rules | inference service | no_captions |
| `nas-hosted-melious` | NAS | hosted | inference service | no_captions |
| `nas-hosted-zai` | NAS | hosted | inference service | no_captions |
| `k8s-rules-service` | Kubernetes | rules | inference service | no_captions |
| `k8s-hosted-melious` | Kubernetes | hosted | inference service | no_captions |
| `k8s-hosted-zai` | Kubernetes | hosted | inference service | no_captions |
| `k8s-rules-local` | Kubernetes | rules | in process | no_captions |

Picture facts are the encoder, context heads and detector results. The two `full` cells also
need a caption server. The inference service serves picture facts; it is not a caption server.

## Preview the plan

From the repository root, after `make dev`:

```bash
uv run python scripts/setup_matrix.py --dry-run --serve-fixture
```

This prints commands, pinned settings, manifests and skipped cells without running them or
starting the fixture server. Environment values stay as variable references, but local output
paths can appear. Review a transcript before sharing it.

## Configure the machines

The runner reads these files by default, outside the repository:

| File | Variables |
|---|---|
| `~/.immich-memories-matrix/.env` | `MELIOUS_AI_BASE_URL`, `MELIOUS_AI_KEY`, `ZAI_API_KEY`, `ZAI_BASE_URL` |
| `~/.immich-memories-matrix/matrix.env` | `MATRIX_NAS_SSH`, `MATRIX_NAS_DOCKER`, `MATRIX_NAS_CACHE`, `MATRIX_NAS_OUT`, `MATRIX_K8S_CONTEXT`, `MATRIX_K8S_NAMESPACE`, `MATRIX_OMLX_BASE_URL`, `MATRIX_CAPTION_BASE_URL` |

Use repeatable `--env-file PATH` options to read different files. Cells missing a required
variable are reported as skipped. The local cells also need `OPENAI_API_KEY`, which the manifest
uses for the reader or caption endpoint.

The local and NAS configs start from your saved config; `--config PATH` selects another file.
The current Kubernetes lane does not carry those Immich credentials into a real-library Job;
use the demo fixture or another lane until the
[credential fix](https://github.com/sam-dumont/immich-video-memory-generator/pull/927) is merged.
That pending change also restricts and removes the copied NAS config.
Remote paths are replaced with container paths under `/out`, `/cache` and `/models`.
Hosted reader cells replace the local reader's URL and thinking parameters. Set `ZAI_BASE_URL`
to the endpoint supported by the account behind the key; the manifest's ZAI cells use the
`zai` adapter, which selects the API protocol from that URL.

- **NAS connection:** `MATRIX_NAS_SSH` must be an SSH destination such as `admin@nas` or a
  `Host` alias from `~/.ssh/config`. Put key paths and SSH options in that config, not in the
  variable. Files travel over tar and SSH; SFTP is not required.
- **NAS limits:** `MATRIX_NAS_DOCKER_LIMITS` defaults to `--cpus 4 --memory 4g`. On a host that
  rejects CFS quotas, use a supported cpuset such as `--cpuset-cpus 0-3 --memory 4g`. Only
  resource-limit flags are accepted. The NAS containers run as root to write the mounted share.
- **Caption server:** `MATRIX_CAPTION_BASE_URL` points to an OpenAI-compatible server serving
  the configured caption model. It receives pictures and the configured caption key.
- **Inference service:** `MATRIX_INFERENCE_BASE_URL` can point NAS cells at a reachable service.
  Otherwise the runner creates the `inference-lan` Service and waits for a LoadBalancer address.
  A cluster-internal address such as `http://inference:8092` cannot serve a NAS outside it.

In the manifest, `$env:NAME` is resolved by the runner. `${NAME}` remains in the pinned config
for credential fields that the app expands when loading. NAS credentials are sent in a temporary
mode-0600 environment file, excluded from result collection and removed afterwards; cluster
hosted-provider credentials use a Secret. The copied NAS `config.yaml` can also contain
credentials from the operator config and remains after the run; keep it private and remove
that copy when finished.

## Run and compare

```bash
uv run python scripts/setup_matrix.py --library demo --serve-fixture \
  --out output/setup-matrix/review --fresh-cache --image-tag YOUR_TEST_TAG
```

Replace `YOUR_TEST_TAG` with the image you intend to test. The script's default is a fixed tag,
currently `0.84.1`; it does not discover the latest release. `--inference-tag` defaults to the
same tag. Record matching app and inference versions when comparing results.

`--serve-fixture` serves the public June 2024 stock library on `0.0.0.0:8078` for the duration of
this invocation. Remote hosts must be able to reach it. Repeat the flag on later invocations,
or supply `MATRIX_FIXTURE_BASE_URL` for a fixture server you keep running separately.

Use `--lane mac`, `--lane nas`, or `--lane k8s` to limit the run; `--lane` and `--cell ID` are
repeatable. Lanes run concurrently, with one cell at a time inside each lane. Reuse `--out` to
combine their saved records. Rebuild the table without running cells with:

```bash
uv run python scripts/setup_matrix.py --summarize-only --out output/setup-matrix/review
```

`--library february` uses the configured private library and requires `--anonymize`. That option
strips identifiers and selected text fields from the summary. It does not anonymize the videos,
pinned configs, logs or raw cell records; keep the output directory private.

## Cold and warm runs

Every cell has its own annotation database, previews and selection records. Model files are
shared within the remote lane. Cells running local classifiers fetch missing pinned models
before preparation, and report that time separately as `models_fetch_s`.

| Lane | Cell cache | Shared models |
|---|---|---|
| Mac | `<out>/<cell>/cache` | Paths from the local config |
| NAS | `$MATRIX_NAS_OUT/<cell>/cache`, mounted at `/cache` | `$MATRIX_NAS_CACHE`, mounted at `/models` |
| Kubernetes | `cache/<cell>` subPath of `setup-matrix-data`, mounted at `/cache` | `models` subPath of that claim, mounted at `/models` |

`--fresh-cache` clears each cell's cache before its first preparation, preserving shared model
files. Without it, a repeated cell can reuse prior work; `prepare_cache_primed` marks that its
first preparation was already warm. The second preparation in each run measures cache reuse.
Remote annotation caches stay on their host; only this run's attempt records are copied back.

## Service and cluster lifecycle

For service cells, the runner applies the inference overlay, waits for rollout, then sends a
real fixture picture until `/facts` succeeds or the 15-minute warmup limit is reached. Warmup
is reported separately as `inference_warmup_s`. `--inference-device` accepts `cpu`, `cuda` or
`auto`; auto checks the cluster's GPU label. `--keep-service` leaves the deployed service running.

The cluster lane uses its own `setup-matrix-data` and `setup-matrix-output` claims. The data
claim retains models and cell caches across runs; **`--purge-claims` deletes them**. The output
claim is removed after each cell's collection. Jobs request 2 CPUs and 4 GiB, with limits of
4 CPUs and 4 GiB.

A failed Job stops its cell instead of waiting for success indefinitely. The runner records
pod diagnostics, copies results through a collector using tar, and retries missing-film
collection up to three times. A failed copy is listed under `unmeasured`; available timings can
still be recovered from stdout.

## Read the output

The default directory is `output/setup-matrix/<library>/<timestamp>/`. Each cell gets its pinned
config, logs, film, attempt files, probe results and `timing.json`. At the top:

- `summary.data.json`: machine-readable data, schema `setup-matrix-v1`.
- `summary.md`: the comparison table and `unmeasured` list.

Missing measurements stay null. Provider prices are not supplied, so the cost column is empty.
Token counts from the CLI summary are rounded at 1,000 and above. Local peak memory comes from
`/usr/bin/time`; remote cells use cgroup counters. Selection overlap measures shared source
pictures, not whether viewers prefer the resulting film.

## Tests

```bash
make test-one T=tests/test_setup_matrix_plan.py
make test-one T=tests/test_setup_matrix_capture.py
make test-one T=tests/test_setup_matrix_summary.py
make test-one T=tests/test_setup_matrix_readiness.py
```

These check generated commands, credential handling, result parsing and failure handling with
fake services. CI does not run the ten real machine setups.
