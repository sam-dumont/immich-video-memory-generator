---
sidebar_position: 4
title: Setup matrix
---

# Running the setup matrix

The capability matrix varies what the product is asked for. The setup matrix varies the machine it
runs on: fourteen setups, one memory each, the same month of the same library. It answers one
question, "how do the same pictures come out under each mode, and what does each mode tax", and the
answer is a table of preparation, selection and render seconds, peak memory, the pictures each setup
chose, the overlap against the reference cut, and the film.

`scripts/setup_matrix.yaml` holds the cells. `scripts/setup_matrix.py` runs them.

| Cell | Lane | Reader | Picture facts | Tier | What it answers |
|---|---|---|---|---|---|
| `mac-local` | Mac | local model | in process | full | The reference cut. |
| `mac-rules` | Mac | rules | in process | full | What the editorial model is worth. |
| `nas-rules-local` | NAS | rules | in process | no_captions | The shipped NAS default. |
| `nas-rules-service` | NAS | rules | inference service | no_captions | What the classifiers cost a NAS. |
| `nas-hosted-melious` | NAS | hosted | inference service | no_captions | A NAS that buys judgement. |
| `nas-hosted-zai` | NAS | hosted | inference service | no_captions | Provider result or hosted result. |
| `k8s-rules-service` | Kubernetes | rules | inference service | no_captions | The cluster with no model bill. |
| `k8s-hosted-melious` | Kubernetes | hosted | inference service | no_captions | The same job, hosted reader. |
| `k8s-hosted-zai` | Kubernetes | hosted | inference service | no_captions | The cheapest published setup. |
| `k8s-rules-local` | Kubernetes | rules | in process | no_captions | The inference service against itself. |
| `k8s-gpu-t1000` | Kubernetes | rules | inference service | no_captions | When do you need a GPU. |
| `k8s-gpu-1070` | Kubernetes | rules | inference service | no_captions | T1000 or GTX 1070. |
| `k8s-full-rules` | Kubernetes | rules | inference service | full | What captioning a whole month costs a cluster. |
| `k8s-full-melious` | Kubernetes | hosted | inference service | full | `mac-local` without the Mac. |

"Picture facts" is where the detectors and the encoder run, in process or in the inference service.
Captions are a third endpoint again, set per cell by `editorial.preparation.caption_base_url`, and
only tier `full` asks for any: a cell can read its facts in process and still send its captions out.
Only the two Mac cells caption at all.

## The two GPU cells

Every one of the ten cells above encodes on a CPU, so after the first run the table had no answer to
either question the owner asked next: when do you need a GPU, and did you try the GTX 1070 against
the T1000. `k8s-gpu-t1000` and `k8s-gpu-1070` are `k8s-rules-service` with the render moved onto a
named card. Same reader, same picture facts, same tier, same 2 CPU request and 4 GB limit, so the
only thing that differs between those three rows is what encoded the film.

The Job gets `runtimeClassName: nvidia`, `resources.limits.nvidia.com/gpu: 1`, the
`nvidia.com/gpu` toleration and a `nodeSelector` on `nvidia.com/gpu.product`. That last one is the
whole trick, and it comes out of the cell:

```yaml
  - id: k8s-gpu-t1000
    k8s:
      gpu_product: NVIDIA-T1000-8GB-SHARED
```

A node label and never a hostname. A hostname names the same machine today and the wrong card the
day a GPU moves between boxes, and it would put somebody's host into a transcript meant for a pull
request. The label is written by the GPU operator, `kubectl get nodes -L nvidia.com/gpu.product`
lists what a cluster has, and a cell naming a label no node carries sits Pending until its schedule
wait gives up, which is a clearer failure than a render that silently went somewhere else.

The container also gets `NVIDIA_VISIBLE_DEVICES=all` and
`NVIDIA_DRIVER_CAPABILITIES=compute,video,utility`, the same pair the app's GPU overlay sets. The
second one is what puts the encoder in the pod: without `video` there is a CUDA device and no NVENC,
which is exactly what the first cluster run hit.

And the cell pins `hardware.backend: nvidia` in its config. Detection takes the first backend that
can encode and treats software as none, so a pod that came up short on driver capabilities would
have encoded on the CPU and published it as a GPU row. Named, the miss is a warning in the log and
the row can be thrown out instead of believed.

Each of those cells records its card in its own `timing.json` as `gpu_product`, and `summary.md`
grows a `## Render device` section naming it. The value is the label the Job selected on, which is
also the label the node carries: a node without it does not match the selector, so the pod could not
have run anywhere else.

## The render device column

The first cluster run is why that column exists. Those Jobs are plain `base/job.yaml` with no GPU
request at all, and all three of them logged
`Title kernels: quadrants 1.3.0 on the CUDA backend`: the device plugin hands out a shared card and
the kernel library takes what it finds, so the title screens, which are the phase a GPU helps most,
were already accelerated. The encode was not. Every NVENC probe in those same logs died on
`Terminating thread with return code -22 (Invalid argument)`, because the NVIDIA runtime exposed
`compute,utility` to the pod and the encoder was never there to find. Reading those rows as CPU rows
or as GPU rows would both have been wrong.

So every cell on every lane now records two things off its own log, and the table shows them as one
column:

| Column value | What it means |
|---|---|
| `PIL titles / software` | No GPU anywhere. The title screens went through the Pillow renderer. |
| `CUDA titles / software` | A shared card drew the titles and nothing encoded on it. |
| `CUDA titles / h264_nvenc` | The whole render is on the card. |
| `Metal titles / h264_videotoolbox` | The Mac lane. |

`title_backend` comes from the one line `titles/kernels.py` prints per process, and `encoder` from
the line the assembly prints on its way in. Neither is inferred from the lane: a cell that printed
neither shows a dash, because "it is a NAS, so it must have been software" is a guess and this table
does not publish those.

## The service gets a card too

Pinning the render says nothing about the classifiers. The inference service has its own Deployment
and lands on whichever GPU node the scheduler picks, so two service cells can be answered by two
different cards with nothing in the table saying so.
`--inference-node-product NVIDIA-T1000-8GB-SHARED` pins it for a run. The selector is written into
the rendered overlay at apply time, the way the image tag already is, so nothing in `deploy/`
changes and no card is committed. Pinned or not, the run reads the label off the node the service
pod actually landed on and publishes it as `inference_gpu_product` in `summary.data.json`.

## The full tier in the cluster

`k8s-full-rules` and `k8s-full-melious` ask for tier `full`, which wants a caption per picture. What
serves them in-cluster is the [captioner overlay](../deploy/installation/caption-server.md):
llama.cpp behind a `captioner` Service on 8092, which is where their endpoint comes from.

```yaml
    requires_overlay: deploy/kubernetes/overlays/captioner
    config:
      editorial.preparation.caption_base_url: http://captioner:8092/v1
```

Cluster DNS, so no address is derived and none is written down. The runner applies that overlay
before the cells that named it and deletes it afterwards, along with the inference overlay and under
the same `--keep-service`.

Applying it is not the same as having it, and the gap is big enough to lose a run in. The init
container fetches 546 MB of GGUF onto a claim that is empty the first time a cluster runs the full
tier, and llama.cpp maps the weights before it answers anything, so `--cell k8s-full-rules` on its
own would have asked for a caption before the server existed and died on its first picture. The
runner waits the way it waits for the inference service: `kubectl rollout status` on the captioner
Deployment with a fifteen minute budget, then one real request through a port-forward it throws away
after, on the warm-up's own fifteen minutes. Both halves of what a `tier: full` cell checks have to
pass. `/v1/models` has to advertise `smolvlm2-500m-base-public`, and one 400 px control tile has to
come back a compact-v3 envelope, which is the failure a server started without its projector gives:
it serves, it is blind, and the cell would find that out one picture in. The wait is published as
`captioner_warmup_s` in `summary.data.json`, beside `inference_warmup_s`, because it is a cost of
the setup rather than of whichever cell happened to go first. `--dry-run` prints both steps,
`wait-captioner` and `warm-captioner`, under the overlay block.

The cell names an overlay, never a device. Which of the two it gets is the answer `probe-gpu`
already gave the inference service: `captioner-cuda` on a cluster with a card, `captioner` without
one, and `--inference-device` pins both together. It matters more than it sounds: on the CPU image
in a 2-CPU pod a picture costs 3.5 s, so one month of the fixture library is 8 minutes of the row
before anything is cut. The run publishes which one answered as `captioner_device` in
`summary.data.json`, and a dry run prints the overlay it would apply, or the rule when the device is
still `auto`.

Both cells were declared before that overlay existed, and stood in the table as skipped rows reading
`captioner overlay not in this tree yet`, because a setup nobody can run yet is still a setup the
table should name. The gate stays now that they run: a checkout can carry less than the manifest
names, and the row with a reason in it beats a row quietly missing.

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

`prepare_cache_primed` follows from this. It is true when the cell's own cache already held a run,
which is to say the cell has run before, and the record then says that its cold preparation is a
re-read rather than a first derivation.

## Cold numbers need `--fresh-cache`

A cell's bank survives the run that filled it, so the second time a cell runs, its cold preparation
is a replay of what the first one derived and its selection is answers read back rather than asked
for. Both hosted Melious cells made zero completions on the run that found this, and published
`selection 2s` and `selection 7s` next to `prep cold 0s`. `--fresh-cache` empties each cell's own
cache before it prepares, so `prepare_cache_primed` is false by construction and the record carries
`fresh_cache: true`. Each lane does it where that directory lives: the Mac's is removed outright,
the NAS's is emptied over ssh before docker binds it, and the cluster's is a subPath only the
container can reach, so the Job carries `MATRIX_FRESH_CACHE=1` and the script empties `/cache` on
its way in. `/models` is never touched on any of them, because re-fetching the model files measures
a network rather than a setup. It is off by default: warm numbers are the point of the second
prepare of the same run, and a fresh cache pays for every caption and every verdict again.

Each lane answers that from somewhere different, because on two of the three the directory being
there proves nothing. The Mac cell's cache is created by the run itself, so the runner looks before
it starts. The NAS cell's is created by `make-remote-dir` a moment before the container starts, so
that step looks first and prints `primed` or `cold` before its own `mkdir -p`. The cluster's is a
subPath the kubelet creates before the container is even scheduled, so the container leaves a
marker file of its own (`/cache/.setup-matrix-cell`) and reports on the way in. Remote cells used to
leave the field null: `k8s-hosted-melious` published a 0 s cold preparation over an already-warm
bank with nothing under `unmeasured` to say it was a re-read.

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

## A cluster cell's config is the pins and nothing else

The NAS gets the whole file. The cluster does not: a Job's ConfigMap is built from `baseline_config`
and the cell's own pins alone, because the operator's config never leaves the laptop. So every key
the manifest does not name is the operator's value on two lanes and the schema's default on the
third, and the three lanes stop being comparable with nothing in the table saying so.

`title_screens.ending_duration` is how that surfaced. The operator's config carried 4.0 s and the
schema's own default is 7.0 s, so the cluster planned three more seconds of ending screen than
anything else did, and every cluster cell came out at 14 shots against 15 everywhere else. The row
was not measuring the cluster. It was measuring an unpinned key.

`baseline_config` therefore pins every field the timeline plan reads: the whole `title_screens`
block, `defaults.transition` and `defaults.transition_duration`, `photos.enabled` and
`photos.duration`, and `analysis.optimal_clip_duration`. There is no `defaults.transition_buffer` on
the schema: the overlap the plan takes back off the content budget is worked out from the transition
mode and its duration. A test holds the line by loading the cluster ConfigMap and the Mac cell's
pinned config out of the same plan and asserting those blocks are identical. Anything new that
changes a timeline belongs in that list on the day it lands.

The values are run 1's, read off `mac-local`'s own written config, and not the schema's defaults.
`mac-local` is the reference cut every other row's overlap is measured against, and it ran at
`locale: fr` with a 4.0 s ending screen, so pinning the schema's 7.0 s would have put every future
cell at 14 shots against the reference's 15 and broken the reader comparison the table exists for.
The baseline is the timeline the matrix was first measured with: changing any value in it invalidates
every comparison against a cell measured before the change, and the older rows have to be re-run.

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
| `~/.immich-memories-matrix/.env` | `MELIOUS_AI_BASE_URL`, `MELIOUS_AI_KEY`, `ZAI_API_KEY`, `ZAI_BASE_URL` |
| `~/.immich-memories-matrix/matrix.env` | `MATRIX_NAS_SSH`, `MATRIX_NAS_DOCKER`, `MATRIX_NAS_CACHE`, `MATRIX_NAS_OUT`, `MATRIX_NAS_DOCKER_LIMITS`, `MATRIX_K8S_CONTEXT`, `MATRIX_K8S_NAMESPACE`, `MATRIX_OMLX_BASE_URL`, `MATRIX_CAPTION_BASE_URL` |

`MATRIX_NAS_DOCKER_LIMITS` and `MATRIX_INFERENCE_BASE_URL` are the two optional entries: see below.
`ZAI_BASE_URL` names z.ai's Anthropic-compatible endpoint, and both zai cells pin it. The account
behind `ZAI_API_KEY` is a coding plan, which is served there and nowhere else: the other route,
`/api/paas/v4`, answers a coding plan `429 {"code":"1113","msg":"Insufficient balance"}` whatever
the request says. The `zai` preset defaults to the Anthropic route as well, so a cell that names no
base URL lands on the right one. `provider: zai` picks its adapter from the base URL's path, so a
`/api/anthropic` base gets `/v1/messages`. The runner drops `llm.base_url` and `llm.no_thinking_params` out of a hosted
cell's copied config, because the operator's own values would otherwise outrank the provider preset,
and a cell that names either field gets the one it named.

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

`--inference-node-product` pins the service to one card by node label, for a run that wants to know
what the classifiers cost on each. It is rendered into the overlay at apply time and never
committed, and whether it is set or not the run records the card the service pod landed on.

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

The NAS container runs as `--user 0:0`, because the share is mounted with an ownership the image's
uid 1000 cannot write to, and the ssh user who tars the results back is not root. `cp -a` carried
the app's own 0700 across onto the copied attempt directory, `pull-results` exited 2 on
`tar: ./attempts/nas-rules-local: Cannot open: Permission denied`, and the cell published an empty
`selected_asset_ids` beside a film that had come back intact. So the container's last act is
`chmod -R a+rX` over what the pull reads: the attempts, the logs, the counter files and the film
directory. The two credential files and the cell's own cache sit in that same directory on the NAS
and are named nowhere in it.

A NAS cell's credentials go over in a file. `docker run -e NAME` takes the value from the
environment of the shell running docker, and a non-interactive ssh session carries none of the
runner's variables: both NAS hosted cells reached their provider with an empty key, and Melious
answered 401 while the same key worked from the cluster, where the runner makes a Secret out of its
own environment. `push-env` pipes `NAME=value` into `<remote>/env` under `umask 077`, the run uses
`--env-file`, `pull-results` excludes it and `drop-credentials` removes it whether or not the cell
worked. Nothing is written on this machine, the values never reach the NAS's command line, and the
dry run prints `NAME=$NAME`.

The config that goes with it is a credential too: it is a copy of the operator's own file, Immich
key included, and it lands on a NAS whose shares other people mount. It is written 0600 here,
`push-config` extracts it under `umask 077` on the far side rather than trusting whichever tar the
NAS has to restore modes, `pull-results` excludes it, and `drop-credentials` takes it away with the
env file. It is not pulled back either: this machine wrote it, and a local tar would extract it
under the operator's umask rather than the 0600 it was written at.

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
and the copy is `kubectl exec <collector> -- tar -C /out -cf - . | tar -C <cell dir> -xf -`, the
same tar over a pipe the NAS lane pulls with. `kubectl cp` used to do it and ended its stream early:
`k8s-rules-service` lost its film, its attempt and every per-phase log to `error: unexpected EOF`,
twice in one run with three retries spent on it. Rooting the archive at `/out` also settles where
the source is. `kubectl cp` runs `tar` inside the container and the image's WORKDIR is `/app`, so a
source relative to the claim root was `tar: setup-matrix/<cell>: Cannot stat` on the second real
run, with everything the cell produced left on the volume.

A zero exit is still not proof the copy finished, so it is tried up to three times with a pause
between, and what it is judged on is the file the run named: the loop stops as soon as that file is
on this machine, and if three tries do not bring it back the cell records
`the film. The copy-out failed after 3 attempts` under `unmeasured` rather than a blank column.

A cell waits twice: five minutes for its pod to be scheduled, then up to three hours for the Job to
finish. A pod that cannot be scheduled, for a claim that does not exist or a node with no room, is
Pending and never completes, and the single long wait used to watch one for three hours. The second
wait is a poll of the Job's own counters rather than `kubectl wait --for=condition=complete`: with
`backoffLimit: 0` a cell that fails leaves a Job in `Failed`, a state that condition never reaches,
so the runner used to sit on a dead cell for the whole three hours. It now asks for `succeeded` and
`failed` every 15 s and stops on either, under the same ceiling. When a step gives up, the runner
runs `kubectl describe pod` for that Job and puts the tail of its events in the cell's record and on
the terminal, then removes what the cell created so the next one is not blocked behind its claim.

The cluster is the one lane that gets no copy of the operator's config: its Job reads a ConfigMap
built from the pins alone, so the file never lands in one. A real library pins no Immich of its own.
Only `demo` does, naming the fixture server and a fake key. That left the Job with nothing to
connect to, and run 2's four k8s cells died together on `Immich not configured. Run 'immich-memories
config' first.` The URL is now written into the ConfigMap, because a server address is not a secret,
and the key goes into the cell's Secret under `IMMICH_MEMORIES_IMMICH__API_KEY`, because a ConfigMap
is readable by anything that can read the namespace. Neither value is in the plan: the dry run, the
manifests it writes and every log carry `<from operator config>` and the runner substitutes the real
one in the argv of the `create secret` call. A `--config` that names no `immich.url` and
`immich.api_key` stops the run before the cluster does.

The Job requests 2 CPU and 4 GB, because this cluster already answered a 1-CPU pod with
`Insufficient cpu` and a cell running on scraps is not a measurement. Its limit is 4 CPU and 4 GB,
which is exactly the NAS cell's default docker cap, so the two rows in the table can be read
against each other.

## What lands in the output

Everything goes under `output/setup-matrix/<library>/<timestamp>/`, which is gitignored because a
real run carries real footage. Per cell: the pinned config, every command's stdout and stderr, the
video and its `ffprobe` read, and `timing.json`, which is that cell's own record. That record also
carries `title_backend`, `encoder` and, for the two cells that pinned one, `gpu_product`. A cell dir
holding a `timing.json` is a cell that ran, which is what lets separate lane invocations add up.

Two files at the top: `summary.data.json` (schema `setup-matrix-v1`, shaped like the research data
files under `docs/research/`) and `summary.md`, the one table a person reads.

Nothing in either is estimated. A number the run did not report stays null and earns a line under
`unmeasured`. Three of those are there by construction: no provider in the matrix returns a price
with a completion, so the cost column is empty; token counts at or above 1000 are rounded to the
nearest 100 by the end-of-run summary; and a cell re-run over its own cache reports a re-read
rather than a first derivation, which the record says out loud.

The cut itself comes back the same way on every lane. The editorial cache holds the attempt (the
plan, the projection, the selection trace) and that cache is never pulled off a NAS or a cluster,
so a remote cell's container copies its own memory's `editorial-runs/<cell>` into `/out/attempts/`
on the way out and the copy-out brings that back with everything else. Only that memory's directory:
the bank beside it is every fact the library ever derived. The capture then reads the attempt with
the same reader the Mac lane uses, off a different root. Before this, both cluster cells that
finished published `selected_asset_ids: []` and `#kept 0` next to a film that plainly had pictures
in it.

A remote cell that cut a film and failed to copy it back still reports its numbers. The container
tees every phase into its output volume, but the run's own stdout came back with the step that ran
it (`kubectl logs` for a cluster cell, the ssh session for a NAS one), and the end-of-run block and
both prepare tables are read from there when the volume's copy never arrived. Cold and warm are
split apart before either is read: the rate table has the same shape in both, so reading the pair as
one stream returns the cell's producers twice. What did not come back is named under `unmeasured`:
the film, and whichever per-phase timings were only ever written to the volume.

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

The plan tests are the real gate: the cells never run in CI, so what is asserted is that the
plan they would run is the right one, that it is identical between calls, and that no value from
the environment reaches the rendered text. The readiness tests stand in for the two things that
cannot be reproduced without a cluster: a fake `kubectl` for a Job that fails, and a fake service
that answers 503 before it answers facts. The capture tests feed the parsers output built by the
renderers the CLI actually prints with, so a change to either format fails there instead of quietly
publishing a plausible wrong number.
