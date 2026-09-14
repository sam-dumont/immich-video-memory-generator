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

"Picture facts" is where the detectors and the encoder run, in process or in the inference service.
Captions are a third endpoint again, set per cell by `editorial.preparation.caption_base_url`, and
only tier `full` asks for any: a cell can read its facts in process and still send its captions out.
Only the two Mac cells caption at all.

## One cache per cell

Every cell banks in an editorial cache of its own. The runner pins `cache.directory`,
`cache.database` and the annotation bank under them per cell, so nothing one cell derived and
nothing one cell decided reaches the next.

This is not a tidiness rule. The first real Mac lane run shared the operator's own cache across
both cells, and `mac-rules` published losses in the model's words ("remembered verdict culled
birthday-candles-01") because `mac-local` had filled the bank minutes earlier. Its cold preparation
came in at one second, against fifteen for the cell that actually paid for the captions. Both rows
were the second cell reading the first one's work.

| Lane | Cache | Models |
|---|---|---|
| Mac | `<out>/<cell>/cache`, beside the cell's logs | wherever the operator's own config keeps them |
| NAS | `$MATRIX_NAS_OUT/<cell>/cache`, mounted at `/cache` | `$MATRIX_NAS_CACHE` at `/models`, shared |
| Kubernetes | subPath `cache/<cell>` of `setup-matrix-data` at `/cache` | subPath `models` of the same claim, shared |

Model files are the opposite case and stay shared, so no detector is downloaded once per cell. The
NAS cell's cache is not pulled back with its results: it is previews and thumbnails by the
gigabyte, it means nothing off the NAS, and leaving it there is what makes a re-run of that cell
warm.

`prepare_cache_primed` follows from this. It is true when the cell's own cache directory was
already there before the run, which is to say the cell has run before, and the record then says
that its cold preparation is a re-read rather than a first derivation.

## A remote cell's config names container roots only

A cell's config is a copy of the operator's own, for its Immich credentials, with the axes the
sweep varies written over the top. Every path-valued field comes along with that copy, and on a
NAS or in a pod none of them exist: the second real remote run pushed
`advanced.editorial.preparation.detector_python: /Users/…/venv-detectors/bin/python` to the NAS and
`nas-rules-local` died in `detectors: FileNotFoundError` with 133 pictures still missing their
heads, having reported no cut at all.

So every remote cell gets each of those fields re-pinned to a root its container actually has
(`/out`, `/cache`, `/models`), or blanked where blank is the field's own "work it out here"
default: `output.directory`, `audio.local_music_dir`, `triage.encoder`, `triage.bundle`,
`editorial.preparation.head_bundle`, `editorial.preparation.detector_python`,
`editorial.preparation.detector_cache_dir` and `editorial.preparation.marqo_onnx`, on top of the
cache trio every lane already gets. `~` counts as a local path here too: HOME is `/models` on the
NAS and `/home/immich` in the Job, a directory that goes away with the pod.

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
| `~/.immich-memories-matrix/.env` | `MELIOUS_AI_BASE_URL`, `MELIOUS_AI_KEY`, `ZAI_API_KEY` |
| `~/.immich-memories-matrix/matrix.env` | `MATRIX_NAS_SSH`, `MATRIX_NAS_DOCKER`, `MATRIX_NAS_CACHE`, `MATRIX_NAS_OUT`, `MATRIX_NAS_DOCKER_LIMITS`, `MATRIX_K8S_CONTEXT`, `MATRIX_K8S_NAMESPACE`, `MATRIX_OMLX_BASE_URL`, `MATRIX_CAPTION_BASE_URL` |

`MATRIX_NAS_DOCKER_LIMITS` and `MATRIX_INFERENCE_BASE_URL` are the two optional entries: see below.
There is no `ZAI_BASE_URL`: the `zai` provider preset carries the URL that serves
`/chat/completions`, and the owner's own variable named z.ai's Anthropic-compatible endpoint, which
answers 200 with a 404 body and `KeyError: 'choices'`. The runner drops `llm.base_url` out of a
hosted cell's copied config so the preset can apply at all, along with `llm.no_thinking_params`,
which is oMLX's chat-template switch and means nothing to a provider.

Point at others with `--env-file`, repeatable. The Mac cells also want `OPENAI_API_KEY` in the
shell, which is the alias the config loader maps to `llm.api_key`.

`MATRIX_NAS_SSH` is an ssh destination, not an ssh command line. The runner puts it where ssh
expects `[user@]host`, so `ssh -i key admin@nas` in there reaches ssh as a username and the first
step dies with "remote username contains invalid characters". Best is a `Host` alias in
`~/.ssh/config` that carries the key, the user, `BatchMode yes` and a `ConnectTimeout`, with only
the alias name in the variable. The runner refuses a value with whitespace in it, dry run included.

The NAS lane moves its files with tar over ssh rather than `scp`. A Synology runs an OpenSSH 8.2
server with the SFTP subsystem off, and a modern `scp` client speaks SFTP, so every copy closed
the connection. tar asks the far side for nothing but a shell.

`MATRIX_NAS_DOCKER_LIMITS` is what the NAS container is capped at, and it defaults to
`--cpus 4 --memory 4g`. `--cpus` is a CFS quota, and a kernel built without the CFS bandwidth
controller, which is what a Synology on cgroup v1 runs, answers the run with `NanoCPUs can not be
set, as your kernel does not support CPU CFS scheduler or the cgroup is not mounted`. Such a host
wants a cpuset pin instead: `--cpuset-cpus 0-3 --memory 4g` gives the cell the same four cores and
starts. The value is split the way a shell would and every flag in it has to be a resource cap
(`--cpus`, `--cpuset-cpus`, `--memory`, `--memory-swap` and their kin), because it is rendered
verbatim into a command the NAS runs as root. Whatever it comes to is written into each NAS cell's
`timing.json` as `container_limits`, so a row can say what its container actually had.

`MATRIX_CAPTION_BASE_URL` is where the caption pass sends its pictures: whichever
OpenAI-compatible server holds `smolvlm2-500m`. On the owner's Mac that is oMLX, which serves the
caption model beside the reader, so the same URL goes in both variables. Elsewhere it is
[the inference service](../deploy/installation/inference-service.md) on `localhost:8092`, or a
remote one. The Mac cells send `OPENAI_API_KEY` to that endpoint as
`editorial.preparation.caption_api_key`, and to no other.

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

Lanes run at the same time. Inside a lane cells run one at a time, because a lane is one host and
everything the matrix publishes is a timing taken on it. The Mac is the only lane allowed to call
the local model server: one machine, one resident model, and it panicked under parallel load. The
NAS pins each cell to 4 CPUs and has exactly 4, and the cluster cells share one inference service
and can land on the same node, so a second cell there would measure contention rather than the
setup.

```bash
uv run python scripts/setup_matrix.py --lane mac --library demo --serve-fixture
uv run python scripts/setup_matrix.py --lane nas --lane k8s --library demo --out <the same dir>
```

Point the second invocation at the first one's `--out` and the table covers both: every invocation
reads the cell records already there and republishes one summary over all of them. `--summarize-only`
does that and nothing else, which is how to rebuild the table after a lane was rerun by hand.

`--cell <id>` is repeatable and narrows further. `--image-tag` picks the published image the remote
lanes pull; it defaults to the last published tag rather than the source version, because the
version in `pyproject.toml` is often ahead of anything on a registry.

The cells that use the inference service bring
[the inference overlay](../deploy/installation/inference-service.md) up first and take it down at
the end, plus `inference-lan` when a NAS cell is in the run. `--inference-device` picks CPU or CUDA, and `auto` asks the cluster whether a node carries
the GPU operator's label. `--keep-service` leaves it running.

`--inference-tag` is what the service runs, and it defaults to `--image-tag` so the service under
test is the release the cells are. The committed overlays pin a release of their own, and a pin
ages: the first cluster lane ran a `0.85.0-cuda` service against a `0.86.2` app image, so those
rows measured a service two releases behind the code they were published as. The runner does not
edit the overlay. It renders it with `kubectl kustomize`, rewrites the inference image reference to
the tag it was given, and applies the result, which leaves the committed files as the thing a
reader applies by hand. The resolved image is printed by the dry run and recorded in
`summary.data.json` as `inference_image`.

A rolled-out Deployment is not yet a service that can decide a picture. The models land in its
cache on the first request that wants them, and until they are there every `/facts` call comes back
503, which the first cell to run would have measured as its own preparation time. So before any
cell starts, the runner sends the service one real facts request: a 200 px JPEG out of the fixture
library, retried with a widening wait until it answers 200, bounded at fifteen minutes. How long
the service took to answer is published as `inference_warmup_s`, and a service that never answers
stops the run with the body it replied with, which is the only thing that names the model it is
missing.

Getting to the service is its own problem. `kubectl apply` returns as soon as the API server has
the manifest, so when the tag has changed the Deployment is still pulling, and a Service with no
ready endpoint answers nothing at all: a port-forward to one never even gets a local listener. A
run once died sixty seconds after printing the overlay line, before a single facts request was
made. So the runner waits out `kubectl rollout status deployment/immich-memories-inference
--timeout=10m` first, and stops there with what the rollout said rather than spending the warm-up's
budget on an image pull. The forward itself is then disposable: if it never comes up, or comes up
and drops, it is killed and replaced on the same fifteen minutes, and the give-up says which of the
two it was (`the port-forward never answered` against `facts answered 503: ...`). NAS cells hand
the warm-up a LAN address, which is reachable from here as well, so those runs skip the forward
entirely.

A NAS cell's credentials go over in a file. `docker run -e NAME` takes the value from the
environment of the shell running docker, and a non-interactive ssh session carries none of the
runner's variables: both NAS hosted cells reached their provider with an empty key, and Melious
answered 401 while the same key worked from the cluster, where the runner makes a Secret out of its
own environment. `push-env` pipes `NAME=value` into `<remote>/env` under `umask 077`, the run uses
`--env-file`, `pull-results` excludes it and `drop-env` removes it whether or not the cell worked.
Nothing is written on this machine, the values never reach the NAS's command line, and the dry run
prints `NAME=$NAME`.

## The cluster lane

The matrix makes two claims of its own, `setup-matrix-data` and `setup-matrix-output`, before it
applies a Job, and applying them again changes nothing. It never mounts the app's claims: those are
`ReadWriteOnce` and stay attached to the running Deployment on whichever node holds it, so a Job
asking for them sits in Multi-Attach forever, and `immich-memories-models` does not exist at all in
a namespace older than `deploy/kubernetes/base/pvc.yaml`.

`setup-matrix-data` carries both halves of what a cell wants kept: the model files on subPath
`models`, mounted at `/models` and shared by every cell, and one editorial cache per cell on
subPath `cache/<cell>`, mounted at `/cache`. It is kept between cells and between runs, because a
warm bank is the difference between a cold preparation and an afternoon of them. `--purge-claims`
deletes it at the end of the run. The output claim is deleted per cell once the collector has
copied the results to this machine.

The collector mounts that claim on the same subPath and at the same path the Job wrote to, `/out`,
and `kubectl cp` is given that absolute path. `kubectl cp` runs `tar` inside the container, and the
image's WORKDIR is `/app`: a source relative to the claim root was
`tar: setup-matrix/<cell>: Cannot stat` on the second real run, and the film, the attempt and every
per-step log stayed on the volume while the cell published an empty row.

A cell waits twice: five minutes for its pod to be scheduled, then up to three hours for the Job to
finish. A pod that cannot be scheduled, for a claim that does not exist or a node with no room, is
Pending and never completes, and the single long wait used to watch one for three hours. The second
wait is a poll of the Job's own counters rather than `kubectl wait --for=condition=complete`: with
`backoffLimit: 0` a cell that fails leaves a Job in `Failed`, a state that condition never reaches,
so the runner used to sit on a dead cell for the whole three hours. It now asks for `succeeded` and
`failed` every 15 s and stops on either, under the same ceiling. When a step gives up, the runner
runs `kubectl describe pod` for that Job and puts the tail of its events in the cell's record and on
the terminal, then removes what the cell created so the next one is not blocked behind its claim.

The Job requests 2 CPU and 4 GB, because this cluster already answered a 1-CPU pod with
`Insufficient cpu` and a cell running on scraps is not a measurement. Its limit is 4 CPU and 4 GB,
which is exactly the NAS cell's default docker cap, so the two rows in the table can be read
against each other.

## What lands in the output

Everything goes under `output/setup-matrix/<library>/<timestamp>/`, which is gitignored because a
real run carries real footage. Per cell: the pinned config, every command's stdout and stderr, the
video and its `ffprobe` read, and `timing.json`, which is that cell's own record. A cell dir
holding a `timing.json` is a cell that ran, which is what lets separate lane invocations add up.

Two files at the top: `summary.data.json` (schema `setup-matrix-v1`, shaped like the research data
files under `docs/research/`) and `summary.md`, the one table a person reads.

Nothing in either is estimated. A number the run did not report stays null and earns a line under
`unmeasured`. Three of those are there by construction: no provider in the matrix returns a price
with a completion, so the cost column is empty; token counts at or above 1000 are rounded to the
nearest 100 by the end-of-run summary; and a cell re-run over its own cache reports a re-read
rather than a first derivation, which the record says out loud.

A remote cell that cut a film and failed to copy it back still reports its numbers. The container
tees every phase into its output volume, but the run's own stdout came back with the step that ran
it (`kubectl logs` for a cluster cell, the ssh session for a NAS one), and the end-of-run block is
read from there when the volume's copy never arrived. What did not come back is named under
`unmeasured`: the film, and whichever per-phase timings were only ever written to the volume.

Peak memory is measured per step, by running each local step under `/usr/bin/time` and taking the
largest of the three. The kernel's own counter is the maximum over every child the runner has
reaped, which is the lane and not the cell, and it handed both Mac cells the same figure to the
decimal. A host carrying neither `/usr/bin/time -l` nor `-v` leaves the field null with that
sentence under `unmeasured`. Remote cells keep their cgroup readings, which are already per
container.

## Tests

```bash
make test-one T=tests/test_setup_matrix_plan.py
make test-one T=tests/test_setup_matrix_capture.py
make test-one T=tests/test_setup_matrix_summary.py
make test-one T=tests/test_setup_matrix_readiness.py
```

The plan tests are the real gate: the ten cells never run in CI, so what is asserted is that the
plan they would run is the right one, that it is identical between calls, and that no value from
the environment reaches the rendered text. The readiness tests stand in for the two things that
cannot be reproduced without a cluster: a fake `kubectl` for a Job that fails, and a fake service
that answers 503 before it answers facts. The capture tests feed the parsers output built by the
renderers the CLI actually prints with, so a change to either format fails there instead of quietly
publishing a plausible wrong number.
