---
sidebar_position: 4
title: Setup matrix
---

# Running the setup matrix

The setup matrix varies the machine rather than the request: twenty-one setups, one memory each, the
same month of the same library. It answers "how do the same pictures come out under each mode, and
what does each mode tax", and the answer is a table of preparation, selection and render seconds,
peak memory, the pictures each setup chose, the overlap against the reference cut, and the film.

Its results go on [Measured](../better/measured.md). This page is how to run it again.

`scripts/setup_matrix.yaml` holds the cells, `scripts/setup_matrix.py` runs them.

| Cell | Lane | Reader | Picture facts | Tier | What it answers |
|---|---|---|---|---|---|
| `mac-local` | Mac | local model | in process | full | The reference cut |
| `mac-rules` | Mac | rules | in process | full | What the editorial model is worth |
| `mac-hosted-melious-deepseek-v4.1-flash` | Mac | hosted | in process | full | A very large model at the cheap end |
| `mac-hosted-melious-gemma-4-31b` | Mac | hosted | in process | full | Weights a workstation can hold |
| `mac-hosted-melious-muse-glimmer-30b` | Mac | hosted | in process | full | The other self-hostable candidate |
| `mac-hosted-melious-glm-5.3-flash` | Mac | hosted | in process | full | The zai model from the other shop |
| `mac-hosted-openai-luna` | Mac | hosted | in process | full | The same price at a name everybody knows |
| `mac-hosted-openai-terra` | Mac | hosted | in process | full | What paying ten times more buys |
| `mac-local-alt-<model>` | Mac | local model | in process | full | One per id in `MATRIX_MAC_ALT_MODELS` |
| `nas-rules-local` | NAS | rules | in process | no_captions | The shipped NAS default |
| `nas-rules-service` | NAS | rules | inference service | no_captions | What the classifiers cost a NAS |
| `nas-hosted-melious` | NAS | hosted | inference service | no_captions | A NAS that buys judgement |
| `nas-hosted-zai` | NAS | hosted | inference service | no_captions | Provider result or hosted result |
| `k8s-rules-service` | Kubernetes | rules | inference service | no_captions | The cluster with no model bill |
| `k8s-hosted-melious` | Kubernetes | hosted | inference service | no_captions | The same job, hosted reader |
| `k8s-hosted-zai` | Kubernetes | hosted | inference service | no_captions | The cheapest published setup |
| `k8s-rules-local` | Kubernetes | rules | in process | no_captions | The inference service against itself |
| `k8s-gpu-t1000` | Kubernetes | rules | inference service | no_captions | When do you need a GPU |
| `k8s-gpu-1070` | Kubernetes | rules | inference service | no_captions | T1000 or GTX 1070 |
| `k8s-full-rules` | Kubernetes | rules | inference service | full | What captioning a whole month costs a cluster |
| `k8s-full-melious` | Kubernetes | hosted | inference service | full | `mac-local` without the Mac |

"Picture facts" is where the detectors and the encoder run. Captions are a third endpoint again, set
per cell by `editorial.preparation.caption_base_url`, and only tier `full` asks for any: a cell can
read its facts in process and still send its captions out.

## The reader bake-off

These Mac cells answer a question the rest of the matrix cannot: does a cheaper reader cut a worse
memory. Each is `mac-local` with a single line moved, so the reader is the only variable and the
overlap column is a straight comparison against the reference cut.

| Cell | Reader | API model id | Context | Per 1M in | Per 1M out | Weights |
|---|---|---|---|---|---|---|
| `mac-local` | oMLX, resident | what the operator's config names | whatever the local server is started with | nothing: not sold by the token | nothing: not sold by the token | on the desk |
| `mac-local-alt-<model>` | oMLX, resident | each id in `MATRIX_MAC_ALT_MODELS` | whatever the local server is started with | nothing: not sold by the token | nothing: not sold by the token | on the desk |
| `mac-hosted-melious-gemma-4-31b` | Melious | `gemma-4-31b` | 256K | EUR 0.10 | EUR 0.30 | Apache 2.0 |
| `mac-hosted-melious-glm-5.3-flash` | Melious | `glm-5.3-flash` | 1M | EUR 0.10 | EUR 0.40 | MIT |
| `mac-hosted-melious-deepseek-v4.1-flash` | Melious | `deepseek-v4.1-flash` | 1M | EUR 0.20 | EUR 1.00 | MIT |
| `mac-hosted-melious-muse-glimmer-30b` | Melious | `muse-glimmer` | 128K | EUR 0.20 | EUR 1.00 | Apache 2.0 |
| `mac-hosted-openai-luna` | OpenAI | `gpt-5.6-luna` | 1.05M | USD 0.20 | USD 1.20 | closed |
| `mac-hosted-openai-terra` | OpenAI | `gpt-5.6-terra` | 1.05M | USD 2.00 | USD 12.00 | closed |

All eight take image input. Prices and licences are what the model pages carried on 2026-09-14, and
the same figures sit in `pricing:` in the manifest with the page each came from. Nothing converts
between the two currencies: a rate is a number nobody measured.

The question behind the table is what somebody who self-hosts gets out of a cheap model, so the cheap
ones are the ones in it. The OpenAI pair is the ballpark check: Luna is the Melious picks' price with
a dollar sign on it, and Terra is ten times Luna, in the table to answer "what does paying more buy".
Both need `OPENAI_KEY`, the real platform key, which is not the `OPENAI_API_KEY` the other Mac cells
use (that one is the bearer token the operator's own oMLX wants). That account holds single-digit
dollars, so run Luna first and read `est_cost` off its row before pointing Terra at a real month.

`MATRIX_MAC_ALT_MODELS` is a list because a build that is strong on text may well beat a
vision-language one at a job that is mostly reading. Set it to comma-separated model ids the local
server has resident and the runner makes one cell per id. No id is in this repo. Two of the six
hosted models ship weights anybody can download and serve (`gemma-4-31b` and `muse-glimmer`, both
Apache 2.0), so putting either on the local server and naming it here prices the hosted convenience
against the electricity.

The reader needs a 32k context and no vision. What it is handed is annotation lines: a model looks
at each picture once, at ingest, and the reader is never sent a picture. So the context window is
what decides whether a model can do this job at all.

## Four rules that make the numbers mean anything

The facts warm-up reports its attempt, last failure and remaining budget every 30 seconds.
A service loading models gets 15 minutes; a port-forward that cannot start a listener stops
after three attempts (about one minute), with the last `kubectl` error.

**One cache per cell.** The runner pins `cache.directory`, `cache.database` and the annotation bank
per cell, so nothing one cell derived and nothing one cell decided reaches the next. This is not a
tidiness rule: the first Mac lane run shared the operator's cache across both cells, and `mac-rules`
published losses in the model's words because `mac-local` had filled the bank minutes earlier. Its
cold preparation came in at one second against fifteen. Both rows were the second cell reading the
first one's work. Model files are the opposite case and stay shared, so no detector is downloaded
once per cell.

**Cold numbers need `--fresh-cache`.** A cell's bank survives the run that filled it, so the second
time a cell runs, its cold preparation is a replay. `--fresh-cache` empties each cell's own cache
before it prepares, and each lane does it where that directory lives. `/models` is never touched,
because re-fetching model files measures a network rather than a setup. It is off by default: warm
numbers are the point of the second prepare of the same run.

**A seeded cell prepares nothing.** Preparation depends on the host, the tier and where the picture
facts come from, not on who reads afterwards. Mac cells that vary only the reader carry
`seed_cache_from: mac-local` and copy that cell's bank. What crosses is preparation (captions, head
facts, pixel facts, motion); what does not is anything a reader decided, and the copy is followed by
a delete over every table in the annotation store that holds a model's answer. A seeded cell's
`prep cold` column holds `= mac-local` rather than a number.

**The cluster's config is pins only.** A Job's ConfigMap is built from `baseline_config` and the
cell's own pins, because the operator's config never leaves the laptop, so any key the manifest does
not name is the operator's value on two lanes and the schema's default on the third.
`title_screens.ending_duration` is how that surfaced: the operator's config carried 4.0 s against the
schema's 7.0 s, so every cluster cell came out at 14 shots against 15 everywhere else, measuring an
unpinned key rather than the cluster. `baseline_config` now pins every field the timeline plan reads,
and a test asserts the cluster ConfigMap and the Mac cell's pinned config carry identical blocks.
Anything new that changes a timeline belongs in that list on the day it lands.

The same applies to home. `MATRIX_HOMEBASE_LATITUDE` and `MATRIX_HOMEBASE_LONGITUDE` are pinned on
every cell of every lane, and their absence is a crash rather than a skip: the schema's default for
both is `0.0`, and before this was pinned the eight cluster cells of run 1 ran at Null Island, graded
every Brussels happening as time away, and published a different cut with no column saying why. Each
cell's `timing.json` records `homebase: pinned`, a word and never the coordinates.

## Cost is the price list times the tokens

The CLI labels LLM duration as `summed request time`: it adds the duration of every request,
including requests that overlap. Four concurrent 30-second calls contribute 120 request-seconds.
Use the selection and render columns for elapsed time. Stored `llm_wall_seconds` and
`hosted_usage.wall_seconds` keep this same cumulative meaning. The CLI also shows how many
completion tokens were reasoning; they are already included in the completion total.

Before a reader cell runs, its probe sends an episode read and a story pick, text only. A failed shape stops the cell.
The manifest's `libraries.<name>.reader_budget` projects each shape over a conservative call
count. Only stages marked `parallel` divide elapsed time by the reader's configured concurrency;
every call still counts toward cost, including reasoning tokens already billed in the completion.

| Library | Reader time ceiling | Token cost ceilings |
|---|---|---|
| `demo` | 20 minutes | EUR 0.10 / USD 0.20 |
| `february` | 45 minutes | EUR 0.25 / USD 0.50 |

These are estimates for selection, not a cap on the provider's final bill or the whole render.
The probe prints its counts, concurrency, projection and ceiling. Missing hosted prices or token
usage cannot pass the cost check. To deliberately measure one expensive cell, pass
`--allow-reader-budget-overrun CELL` to either matrix command. This waives its budget refusal,
but image support and readable answers remain required. No currency conversion is assumed.

`hosted_usage.tokens_in` and `tokens_out` come from `llm-usage.json`, which every run that asked a
model leaves in its attempt directory. `usage_source` says `record` when the row was read from there,
and `counted_exactly` is then true. A run made before that file existed is added back up out of its
own artifacts and says `reconstructed`; failing that, the row falls back to the end-of-run block the
CLI prints, which rounds anything at or above 1000, and says `log`. Read a `log` row's euro figure at
two digits rather than four.

Where a row has both halves, the summary publishes `est_cost_eur` and shows the arithmetic under the
table. Where either half is missing the column stays empty and the cell earns a line under
`unmeasured`, because a price with no token count is a price list and a token count with no price is
not money. The table is keyed by reader as well as by model id, because `glm-5.3-flash` is sold by two
shops at two prices.

Every cell also counts `rejections/repairs`: how many times a reading contract refused an answer and
asked again (each one a call paid for twice), and every answer a contract refused. Overlap says which
pictures a reader chose; this says how hard the run had to work to get an answer in the shape the
contract asked for. The column exists because a hosted `qwen3-30b` answered `story-pick-K02` with the
whole offered row instead of the label it named, produced the same shape again under repair, and
killed a run four minutes in with nothing rendered, and nothing in the published table said so.

## Running it

```bash
uv run python scripts/setup_matrix.py --dry-run --serve-fixture
```

That prints every command and every manifest, touches nothing, and names every cell it would skip and
why. It is also what to paste into a pull request: the transcript carries variable names, never their
values.

Two untracked files outside the repo hold the rest. `~/.immich-memories-matrix/.env` holds
`MELIOUS_AI_BASE_URL`, `MELIOUS_AI_KEY`, `ZAI_API_KEY`, `ZAI_BASE_URL`, `OPENAI_KEY` and the two
homebase coordinates. `~/.immich-memories-matrix/matrix.env` holds `MATRIX_NAS_SSH`,
`MATRIX_NAS_DOCKER`, `MATRIX_NAS_CACHE`, `MATRIX_NAS_OUT`, `MATRIX_NAS_DOCKER_LIMITS` (optional),
`MATRIX_K8S_CONTEXT`, `MATRIX_K8S_NAMESPACE`, `MATRIX_K8S_DATA_STORAGE` (optional),
`MATRIX_OMLX_BASE_URL`, `MATRIX_CAPTION_BASE_URL` and `MATRIX_MAC_ALT_MODELS`. Point at others with `--env-file`, repeatable. In the YAML, `$env:NAME` is
resolved by the runner before it executes and is for anything the app will not expand itself;
`${NAME}` is written into the pinned config verbatim and is for credentials only, so a key is never
written to a file.

A variable a cell needs and cannot find is not a crash: the cell stays in the table with a
`skip_reason` and is listed under `unmeasured`, because a lane that could not run is a result.

Four environment notes that cost an afternoon each. `MATRIX_NAS_SSH` is an ssh destination and not
an ssh command line, so use a `Host` alias in `~/.ssh/config` and put only the alias in the variable.
The NAS lane moves files with tar over ssh rather than `scp`, because a Synology runs an OpenSSH 8.2
server with the SFTP subsystem off and a modern `scp` client speaks SFTP. And
`MATRIX_NAS_DOCKER_LIMITS` defaults to `--cpus 4 --memory 4g`, which a Synology kernel built without
the CFS bandwidth controller refuses; such a host wants `--cpuset-cpus 0-3 --memory 4g` instead.
Last, `MATRIX_K8S_DATA_STORAGE` is the size a cluster cell's data claim is applied at, `10Gi` when
unset. A claim can grow and never shrink, so once you grow the one on the cluster (a February
preview pool outgrew 10Gi), set the variable to at least that size. Otherwise every apply is refused
with `field can not be less than status.capacity` and no cluster cell starts. A cell with claims of
its own gets the same size, so its storage class has to allow volume expansion once its claim
exists.

`--library demo` is the stock June 2024 fixture library, public and needing no anonymising;
`--serve-fixture` puts it on `0.0.0.0:8078` so the NAS and the cluster can read it over the LAN.
`--library february` is the owner's own month, and the runner refuses to write a summary for it
without `--anonymize`, which turns asset ids into positional handles and drops every piece of text
the reader wrote. Overlap stays computable after the strip; nothing else survives.

```bash
uv run python scripts/setup_matrix.py --lane mac --library demo --serve-fixture
uv run python scripts/setup_matrix.py --lane nas --lane k8s --library demo --out <the same dir>
```

Lanes run at the same time. Inside a lane cells run one at a time, because a lane is one host and
everything the matrix publishes is a timing taken on it: the Mac has one resident model and panicked
under parallel load, the NAS pins each cell to 4 CPUs and has exactly 4, and the cluster cells share
one inference service.

## What lands in the output

Everything goes under `output/setup-matrix/<library>/<timestamp>/`, gitignored because a real run
carries real footage. Per cell: the pinned config, every command's stdout and stderr, the video and
its `ffprobe` read, and `timing.json`, which is that cell's own record. A cell directory holding a
`timing.json` is a cell that ran, which is what lets separate lane invocations add up.

Two files at the top: `summary.data.json` (schema `setup-matrix-v1`) and `summary.md`, the one table
a person reads. Nothing in either is measured that the run did not report. A number it did not stays
null and earns a line under `unmeasured`.

A remote cell's editorial cache is never pulled back, so its container copies its own memory's
`editorial-runs/<cell>` into `/out/attempts/` on the way out, and the capture reads that attempt with
the same reader the Mac lane uses. A cell that cut a film and failed to copy it back still reports
its numbers, off the stdout the step that ran it returned. A cell whose attempt never came back still
says which pictures it kept, read off the `/api/assets/<id>/original` lines in its own log and marked
`cut_source: generate log`; that is a set and not a running order, so `order_kept` stays null.

Peak memory is per step, by running each local step under `/usr/bin/time` and taking the largest of
the three, because the kernel's own counter is the maximum over every child the runner reaped, which
is the lane and not the cell. Remote cells keep their cgroup readings.

```bash
make test-one T=tests/test_setup_matrix_plan.py
make test-one T=tests/test_setup_matrix_capture.py
make test-one T=tests/test_setup_matrix_summary.py
make test-one T=tests/test_setup_matrix_readiness.py
```

The cells never run in CI, so the plan tests are the real gate: they assert that the plan the runner
would execute is the right one, that it is identical between calls, and that no value from the
environment reaches the rendered text.

## Rendering one of each memory type

A different driver, for a different question. When selection changes, one film tells you almost
nothing: you need one of each kind (a month, a person, a trip, a year, an album) rendered the same
way, uploaded to the same place, watched back to back. `scripts/matrix_routes.py` drives that through
the public CLI and adds no selection logic of its own.

`examples/matrix-routes.example.json` lists eleven routes, each naming a memory type, a target
duration, a bundled music loop and a scope whose values are `@placeholders`. Which year, which person,
which album never enter the repository: they live in an overlay file of your own, outside it, which
also carries the output root, the album name and an optional `reference` block holding the plan hash
of a run you already approved. A placeholder with no value is a hard error at `build` time, never a
skip, because rendering nine routes and quietly dropping the tenth is how you end up grading a matrix
with a hole in it.

```bash
python scripts/matrix_routes.py build   --routes examples/matrix-routes.example.json \
                                        --private ~/matrix/private.json \
                                        --manifest ~/matrix/manifest.private.json
python scripts/matrix_routes.py run     --manifest ~/matrix/manifest.private.json
python scripts/matrix_routes.py collect --manifest ~/matrix/manifest.private.json
python scripts/matrix_routes.py report  --manifest ~/matrix/manifest.private.json
```

`build` checks every flag it produces against the live Click tree, so a renamed option fails here
rather than three hours into a batch, and it gives every case the same fixed flags so the route is
the only thing that varies. `run` is serial and resumable, holds an exclusive lock on the manifest,
re-checks the frozen config's SHA-256 before every case and refuses to start below a 50 GiB free-disk
floor. `collect` never trusts an exit code: one `.mp4` per case at 1920x1080, an audio stream, the
`Audio mixed successfully` line, and a full `ffmpeg -xerror … -f null -` decode of both streams, and
if the batch asked for an upload and no Immich asset id was recorded the case is `failed` rather than
`ready`.

`report` is the part that decides anything:

| Verdict | Means |
|---|---|
| `identical` | The plan bytes match the accepted run. Your previous grade stands |
| `same-carriers` | Different plan, same clips in the same order. Something around selection moved; the cut did not |
| `changed; owner approval not transferred` | A different cut. Watch it |
| `no reference` | Nothing banked for this route yet |
| `not collected` | `collect` has not run, or it failed |

One dated album per matrix, named in the overlay, so the whole set is one scroll in Immich and old
batches never mix into a new one. The eleventh route, `monthly-supersede`, exists to exercise one
behaviour: uploading a memory whose recipe already exists in the album should trash the older copy
rather than sit beside it. After the batch that album should hold ten films, not eleven.
