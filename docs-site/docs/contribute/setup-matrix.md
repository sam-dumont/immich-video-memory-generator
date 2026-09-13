---
sidebar_position: 4
title: Setup matrix
---

# Running the setup matrix

The capability matrix varies what the product is asked for. The setup matrix varies the machine it
runs on: ten setups, one memory each, the same month of the same library. It answers one question,
"how do the same pictures come out under each mode, and what does each mode tax", and the answer is
a table of preparation, selection and render seconds, peak memory, the pictures each setup chose,
the overlap against the reference cut, and the film.

`scripts/setup_matrix.yaml` holds the ten cells. `scripts/setup_matrix.py` runs them.

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

## Start with the dry run

```bash
uv run python scripts/setup_matrix.py --dry-run --serve-fixture
```

That prints every command and every manifest, touches nothing, and names every cell it would skip
and why. It is also what to paste into a pull request: the transcript carries variable names, never
their values, so no host, path or key is in it.

## What it needs

Two files outside the repo, neither of them tracked:

| File | Holds |
|---|---|
| `~/.immich-memories-matrix/.env` | `MELIOUS_AI_BASE_URL`, `MELIOUS_AI_KEY`, `ZAI_BASE_URL`, `ZAI_API_KEY` |
| `~/.immich-memories-matrix/matrix.env` | `MATRIX_NAS_SSH`, `MATRIX_NAS_DOCKER`, `MATRIX_NAS_CACHE`, `MATRIX_NAS_OUT`, `MATRIX_K8S_CONTEXT`, `MATRIX_K8S_NAMESPACE`, `MATRIX_OMLX_BASE_URL` |

`MATRIX_INFERENCE_BASE_URL` is the one optional entry: see below.

Point at others with `--env-file`, repeatable. The Mac cells also want `OPENAI_API_KEY` in the
shell, which is the alias the config loader maps to `llm.api_key`.

The NAS cells that read picture facts from the inference service need an address the NAS can reach,
and `http://inference:8092` is not one: it resolves inside the cluster only. The runner handles it.
It applies [the `inference-lan` overlay](../deploy/installation/inference-service.md), waits up to
180 s for the load-balancer controller to hand out an address, writes `http://<that>:8092` into
those cells' configs, and deletes the Service at the end. Set `MATRIX_INFERENCE_BASE_URL` to pin an
address instead, which is how to point at a service the matrix did not start. A dry run prints
`<derived at run time>` rather than an address, because there is none yet and a transcript should
not carry one.

A variable a cell needs and cannot find is not a crash. The cell stays in the table with a
`skip_reason` and is listed under `unmeasured` in the published record, because a lane that could
not run is a result.

## Two ways a variable is written

`$env:NAME` in the YAML is resolved by the runner before it executes, and is for anything the app
will not expand itself: a base URL, an ssh destination, a path on the NAS.

`${NAME}` is written into the pinned config verbatim and expanded by the app when it loads.
That spelling is for credentials only: `llm.api_key` is one of the fields the loader expands, so a
key is never written to a file.

## The library

`--library demo` is the stock June 2024 fixture library, which is public and needs no anonymising.
`--serve-fixture` puts it on `0.0.0.0:8078` so the NAS and the cluster can read it over the LAN,
and prints the URL the remote cells are pointed at.

`--library february` is the owner's own month. It is private, so the runner refuses to write a
summary for it without `--anonymize`, which turns asset ids into positional handles and drops every
piece of text the reader wrote. Overlap stays computable after the strip; nothing else survives.

## Lanes

Lanes run at the same time. Inside the Mac lane cells run one at a time, and it is the only lane
allowed to call the local model server: one machine, one resident model, and it panicked under
parallel load.

```bash
uv run python scripts/setup_matrix.py --lane mac --library demo --serve-fixture
uv run python scripts/setup_matrix.py --lane nas --lane k8s --library demo
```

`--cell <id>` is repeatable and narrows further. `--image-tag` picks the published image the remote
lanes pull; it defaults to the last published tag rather than the source version, because the
version in `pyproject.toml` is often ahead of anything on a registry.

The cells that use the inference service bring
[the inference overlay](../deploy/installation/inference-service.md) up first and take it down at
the end, plus `inference-lan` when a NAS cell is in the run. `--inference-device` picks CPU or CUDA, and `auto` asks the cluster whether a node carries
the GPU operator's label. `--keep-service` leaves it running.

## What lands in the output

Everything goes under `output/setup-matrix/<library>/<timestamp>/`, which is gitignored because a
real run carries real footage. Per cell: the pinned config, every command's stdout and stderr, the
video and its `ffprobe` read, and `timing.json` values folded into the record.

Two files at the top: `summary.data.json` (schema `setup-matrix-v1`, shaped like the research data
files under `docs/research/`) and `summary.md`, the one table a person reads.

Nothing in either is estimated. A number the run did not report stays null and earns a line under
`unmeasured`. Three of those are there by construction: no provider in the matrix returns a price
with a completion, so the cost column is empty; token counts at or above 1000 are rounded to the
nearest 100 by the end-of-run summary; and a host whose annotation bank already held the month
reports a re-read rather than a first derivation, which the record says out loud.

## Tests

```bash
make test-one T=tests/test_setup_matrix_plan.py
make test-one T=tests/test_setup_matrix_capture.py
make test-one T=tests/test_setup_matrix_summary.py
```

The plan tests are the real gate: the ten cells never run in CI, so what is asserted is that the
plan they would run is the right one, that it is identical between calls, and that no value from
the environment reaches the rendered text. The capture tests feed the parsers output built by the
renderers the CLI actually prints with, so a change to either format fails there instead of quietly
publishing a plausible wrong number.
