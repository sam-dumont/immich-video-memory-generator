---
sidebar_position: 4
title: Setup matrix
---

# Running the setup matrix

The capability matrix varies what the product is asked for. The setup matrix varies the machine it
runs on: twenty-one setups, one memory each, the same month of the same library. It answers one
question, "how do the same pictures come out under each mode, and what does each mode tax", and the
answer is a table of preparation, selection and render seconds, peak memory, the pictures each setup
chose, the overlap against the reference cut, and the film.

`scripts/setup_matrix.yaml` holds the cells. `scripts/setup_matrix.py` runs them.

| Cell | Lane | Reader | Picture facts | Tier | What it answers |
|---|---|---|---|---|---|
| `mac-local` | Mac | local model | in process | full | The reference cut. |
| `mac-rules` | Mac | rules | in process | full | What the editorial model is worth. |
| `mac-hosted-melious-deepseek-v4.1-flash` | Mac | hosted | in process | full | A very large model at the cheap end. |
| `mac-hosted-melious-gemma-4-31b` | Mac | hosted | in process | full | Weights a workstation can hold. |
| `mac-hosted-melious-muse-glimmer-30b` | Mac | hosted | in process | full | The other self-hostable candidate. |
| `mac-hosted-melious-glm-5.3-flash` | Mac | hosted | in process | full | The zai model from the other shop. |
| `mac-hosted-openai-luna` | Mac | hosted | in process | full | The same price at a name everybody knows. |
| `mac-hosted-openai-terra` | Mac | hosted | in process | full | What paying ten times more buys. |
| `mac-local-alt-<model>` | Mac | local model | in process | full | One per id in `MATRIX_MAC_ALT_MODELS`. |
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
Only the Mac cells and the two full-tier cluster cells caption at all.

## The reader bake-off

These Mac cells answer a question the rest of the matrix cannot: does a cheaper reader cut a worse
memory. Each one is `mac-local` with a single line moved. Same lane, same tier `full`, the same
caption endpoint, the same picture facts derived in process, so the reader is the only variable and
the overlap column is a straight comparison against the reference cut.

| Cell | Reader | API model id | Vision | Context | Per 1M in | Per 1M out | Weights |
|---|---|---|---|---|---|---|---|
| `mac-local` | oMLX, resident | what the operator's config names | | | | | on the desk |
| `mac-local-alt-<model>` | oMLX, resident | each id in `MATRIX_MAC_ALT_MODELS` | | | | | on the desk |
| `mac-hosted-melious-gemma-4-31b` | Melious | `gemma-4-31b` | yes | 256K | EUR 0.10 | EUR 0.30 | Apache 2.0 |
| `mac-hosted-melious-glm-5.3-flash` | Melious | `glm-5.3-flash` | yes | 1M | EUR 0.10 | EUR 0.40 | MIT |
| `mac-hosted-melious-deepseek-v4.1-flash` | Melious | `deepseek-v4.1-flash` | yes | 1M | EUR 0.20 | EUR 1.00 | MIT |
| `mac-hosted-melious-muse-glimmer-30b` | Melious | `muse-glimmer` | yes | 128K | EUR 0.20 | EUR 1.00 | Apache 2.0 |
| `mac-hosted-openai-luna` | OpenAI | `gpt-5.6-luna` | yes | 1.05M | USD 0.20 | USD 1.20 | closed |
| `mac-hosted-openai-terra` | OpenAI | `gpt-5.6-terra` | yes | 1.05M | USD 2.00 | USD 12.00 | closed |

Prices and licences are what the model pages carried on 2026-09-14, and the same figures sit in
`pricing:` in the manifest with the page each one came from. Nothing converts between the two
currencies: a rate is a number nobody measured.

The question behind the whole table is what somebody who self-hosts gets out of a cheap model, so
the cheap ones are the ones in it. `qwen3-30b-a3b-instruct` was in an earlier draft and is retired
everywhere, hosted cells on the other lanes included, because Melious lists it as text only and this
reader looks at pictures. The hosted cells on the NAS and the cluster read with `gemma-4-31b` now.

The OpenAI pair is the ballpark check. `gpt-5.6-luna` at USD 0.20 / 1.20 is the Melious picks' price
with a dollar sign on it, which is the row worth having. `gpt-5.6-terra` at USD 2.00 / 12.00 is ten
times Luna and it is in the table to answer "what does paying more buy" rather than as a cheap
option. `gpt-5.6-sol` (USD 4.00 / 20.00) and `gpt-6-astra` (USD 10.00 / 50.00) are not in the
comparison at all. Both cells need `OPENAI_KEY`, the real platform key in the matrix env file,
which is not the `OPENAI_API_KEY` the other Mac cells use: that one is the bearer token the
operator's own oMLX wants, and it still pays for the captions in these cells too. That account holds
single-digit dollars, so run Luna first and read `est_cost` off its row before pointing Terra at a
real month. The flagship pricing table says the `gpt-5.6` family takes no
image input and the model pages say it takes text and image; the model pages are what the manifest
was written from.

The reader needs a vision-capable model with a 32k context. Most of what it is handed is annotation
lines, and then the structure pass demands 800 px tiles for the few dozen pictures a month it cannot
settle on paper: 36 tiles on the demo month, 40 on a month holding 13,552 pictures, recorded as
`images_sent` in the attempt's `plan.private.json` and carried into the record as
`hosted_usage.images_sent`. The pair confirmer and the story-motion check ask for more. So the
context window is what decides whether a model can do this job at all, and the vision head is used,
sparingly. Every picture has already been described once by the caption model `smolvlm2-500m`, at the
same endpoint in all of these cells.

`MATRIX_MAC_ALT_MODELS` is a list because a build that is strong on text may well beat a
vision-language one at a job that is mostly reading. Set it to comma-separated model ids the local
server has resident and the runner makes one cell per id, named
`mac-local-alt-<the id, lowercased, non-alphanumerics turned into dashes>`. No id is in this repo:
unset, the template stays in the table as a single skipped row. A dry run does print the ids, because
a cell named after a model carries that model's name in its own id.

`muse-glimmer` is the API id. Its model page is served at `/hub/models/muse-glimmer-30b`, which is a
different string, so every id in the manifest was confirmed against
`GET $MELIOUS_AI_BASE_URL/models` before it was written down.

Two of the six hosted models ship weights anybody can download and serve: `gemma-4-31b` (Apache
2.0, 31B dense) and `muse-glimmer` (Apache 2.0, 29.6B dense with a 1.8B perception encoder). Put
either of those on the local server and name it in `MATRIX_MAC_ALT_MODELS`, and the pair of rows
prices the hosted convenience against the electricity.

These cells are worth running over `--library february` as well as the demo month. The reader calls
are text, so a real month of a real library is cheap to read and is the only thing that says what a
model costs in practice. February is private: `--anonymize` is mandatory, and the record then carries
aggregates and positional handles only, as it does for every other February cell.

## A seeded cell prepares nothing

Preparation depends on the host, the preparation tier and where the picture facts come from. It does
not depend on who reads afterwards. Mac cells that vary only the reader would each caption the same
pictures again and publish one measurement under every one of their names, so they carry
`seed_cache_from: mac-local` and copy that cell's bank instead.

What crosses is preparation: the captions, the head facts, the pixel facts, the motion. What does not
is anything a reader decided. The copy is followed by a delete over every table in the annotation
store that holds a model's answer (`judgments`, the two completion-failure tables, the visual
judgments, the banked episode readings, the period insights, the cull verdicts) and the separate
`judgments.db` beside it is removed outright. The per-cell cache rule that produced this matrix is
intact: it exists because the first real Mac run shared one cache and `mac-rules` published
`mac-local`'s verdicts as its own losses, and seeding a reader's answers into cells that exist to
compare readers would be that failure with extra steps.

The seed is resolved per library, and nothing has to name a path. `--out` is one run of one library,
so its parent directory holds that library's other runs and nothing else: a run that prepares
`mac-local` itself seeds from its own copy, and one that does not takes the newest run under that
library that has it. February's thirteen thousand captioned pictures are already banked under
`output/setup-matrix/february/`, which is what makes running the reader cells over a real month a
matter of minutes rather than an afternoon. A cell whose seed is not there stops with a message
naming the directory it looked in rather than silently preparing from scratch.

A seeded cell's `prep cold` and `prep warm` columns hold `= mac-local` rather than a number, its
`prepare_cache_primed` says `seeded from mac-local`, and `unmeasured` names the cell that measured
the preparation for this host, tier and facts source.

## Cost is the price list times the tokens

`hosted_usage.tokens_in` and `tokens_out` are read off the end-of-run block the CLI prints, which
rounds anything at or above 1000. `pricing:` in the manifest holds a list price per reader and model
id, with the page it came from and the date it was read. Where a row has both halves, the summary
publishes `est_cost_eur` and a `## Cost` section under the table shows the arithmetic: the euro
figure, the model, `hosted_usage.calls`, the tokens in and out, the tiles the reader was sent, and
the page the price came from.
Where either half is missing the column stays empty and the cell earns a line under `unmeasured`,
because a price with no token count is a price list and a token count with no price is not money.

The table is keyed by reader as well as by model id, because `glm-5.3-flash` is sold by two shops at
two prices. Only the Melious one has a page in the manifest, so the two zai cells stay unpriced and
say so. Each shop names its own currency beside its models and no figure is ever converted, so a
euro row and a dollar row stay two numbers.

The estimate carries the run summary's rounding with it. A run that reported 125.4k prompt tokens is
125,400 in the arithmetic and somewhere between 125,350 and 125,449 in fact, so read the euro figure
at two digits rather than four.

## The contract column

Overlap says which pictures a reader chose. It says nothing about how hard the run had to work to get
an answer out of it in the shape the contract asked for, and that is the other half of whether a
model is any good. Each cell counts two things, and the table carries them as `rejections/repairs`:

- **repairs**: how many times a reading contract refused an answer and asked the same question again
  with the rejection spelled out. Every one of these is a call the row paid for twice.
- **rejections**: every answer a contract refused. The repairs, plus the ones nothing recovered (the
  bounded-failure and unreadable-JSON transcripts the judge writes beside the attempt), plus the
  episode reader's own warnings in the run log, counted once per distinct reason the way the reader
  itself reports them.

Both are read off what the run left behind: the private call transcripts under the attempt's
`calls/` directory, and the `text episode provider failed (...)` warnings. A cell with no `calls/`
directory reports null rather than a zero it never measured, and a `rules` cell reports nothing at
all, never having asked a model anything.

The column exists because a hosted `qwen3-30b` answered `story-pick-K02` with the whole offered row
instead of the label it named, produced the same shape again under repair, and killed a run four
minutes in with nothing rendered. Nothing in the published table said so.

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
| `~/.immich-memories-matrix/.env` | `MELIOUS_AI_BASE_URL`, `MELIOUS_AI_KEY`, `ZAI_API_KEY`, `ZAI_BASE_URL`, `OPENAI_KEY` |
| `~/.immich-memories-matrix/matrix.env` | `MATRIX_NAS_SSH`, `MATRIX_NAS_DOCKER`, `MATRIX_NAS_CACHE`, `MATRIX_NAS_OUT`, `MATRIX_NAS_DOCKER_LIMITS`, `MATRIX_K8S_CONTEXT`, `MATRIX_K8S_NAMESPACE`, `MATRIX_OMLX_BASE_URL`, `MATRIX_CAPTION_BASE_URL`, `MATRIX_MAC_ALT_MODELS` |

`MATRIX_NAS_DOCKER_LIMITS` and `MATRIX_INFERENCE_BASE_URL` are the two optional entries: see below.
`ZAI_BASE_URL` names z.ai's Anthropic-compatible endpoint, and both zai cells pin it. The account
behind `ZAI_API_KEY` is a coding plan, which is served there and nowhere else: the preset's own
`/api/paas/v4` answers a coding plan `429 {"code":"1113","msg":"Insufficient balance"}` whatever the
request says. `provider: zai` picks its adapter from the base URL's path, so a `/api/anthropic` base
gets `/v1/messages`. The runner drops `llm.base_url` and `llm.no_thinking_params` out of a hosted
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

A cell can ask for less with `k8s.cpu_request` and `k8s.memory_request`, and the two GPU render
cells do: one CPU each. `k8s-gpu-1070` sat in FailedScheduling for `1 Insufficient cpu`, because the
node holding that card also runs the live web Deployment and had no spare two CPU to give. The
encode is on the card and the pod only feeds it. The limit is untouched either way, so a cell that
turns out to want more still gets it and the rows stay comparable. What each Job actually asked for
is written into its record as `job_requests`.

Neither render cell asks for a card, and `k8s.gpu_resource: false` is how they say so.
`k8s-gpu-t1000` was Pending on `Insufficient nvidia.com/gpu`: the inference Deployment already holds
that node's one allocatable card, and the device plugin advertises exactly one however the `SHARED`
label reads about time-slicing. The plain demo Jobs drew their titles on CUDA on that same node
having asked for nothing at all, because the node's default runtime exposes the card to every pod
on it. So the mechanism that puts a render on a card here is `runtimeClassName: nvidia`, the
`NVIDIA_DRIVER_CAPABILITIES` that includes `video`, and the product nodeSelector, none of which the
countable resource is part of. The record carries `gpu_resource_requested` so a row can say which
it did.

## What lands in the output

Everything goes under `output/setup-matrix/<library>/<timestamp>/`, which is gitignored because a
real run carries real footage. Per cell: the pinned config, every command's stdout and stderr, the
video and its `ffprobe` read, and `timing.json`, which is that cell's own record. That record also
carries `title_backend`, `encoder` and, for the two cells that pinned one, `gpu_product`. A cell dir
holding a `timing.json` is a cell that ran, which is what lets separate lane invocations add up.

Two files at the top: `summary.data.json` (schema `setup-matrix-v1`, shaped like the research data
files under `docs/research/`) and `summary.md`, the one table a person reads.

Nothing in either is measured that the run did not report. A number it did not stays null and earns
a line under `unmeasured`. The cost column is the one figure computed here rather than observed, and
it says so: see [Cost is the price list times the tokens](#cost-is-the-price-list-times-the-tokens).
Two more gaps are there by construction: token counts at or above 1000 are rounded by the end-of-run
summary, and a cell re-run over its own cache reports a re-read rather than a first derivation,
which the record says out loud.

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
