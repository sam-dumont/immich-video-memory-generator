---
sidebar_position: 3
title: Readers
sidebar_label: "The reader: config and measurements"
---

# The reader

The reader is the model the editor hands a period to. It reads the annotation lines and writes the
story. It never sees a picture: a model looks at each picture once, at ingest (the caption model and
the heads), and the reader edits from the text that ingest banked. Any OpenAI-compatible or
Anthropic-compatible endpoint goes in `llm`, and a text-only model is enough.

The three keys that have to be right: `base_url` (it defaults to `http://localhost:8080/v1`, which
is the app's own port, so set it), `model` (the exact string the server reports at `GET /v1/models`)
and `provider`. Every `llm:` key is in the
[config reference](../reference/config-reference.md#llm-vision-model).

## Providers and dialects

Five provider values, three code paths. `ollama` speaks Ollama's native API, `anthropic` speaks
`/v1/messages`, and `openai-compatible` and `openai` speak `/v1/chat/completions`, so anything
serving that endpoint works: mlx-vlm, [oMLX](https://github.com/jundot/omlx), vLLM, Ollama's
compatibility layer, Groq, OpenAI itself. `zai` is the `anthropic` adapter with z.ai's URL and
reasoning level filled in, and it is the one provider that picks its adapter from the `base_url`
path, because z.ai serves both dialects on one host: `.../api/anthropic` gets `/v1/messages`,
`.../api/paas/v4` gets `/chat/completions`.

`openai`, `anthropic` and `zai` fill in the vendor's base URL and reasoning dialect where you left
the field at its default. `openai-compatible` fills in nothing. An explicit `base_url` always wins.

The Messages API path is `POST {base_url}/v1/messages` with `x-api-key` and
`anthropic-version: 2023-06-01` and the prompt as one user message. Nothing about the app is Claude-specific: point `base_url` at whoever serves the
dialect. Answers come back as a JSON envelope the app validates itself, and no provider-side JSON
mode is used, so a host without one loses nothing.

```yaml
advanced:
  llm:
    provider: "anthropic"
    model: "claude-sonnet-5"        # or claude-haiku-4-5 for the cheap seat
    api_key: "${ANTHROPIC_API_KEY}"
    thinking: "high"                # disabled | low | high | max | auto
```

Two things that preset handles, because Claude answers HTTP 400 otherwise: no `temperature` goes out
(from the 4.7 line on, Claude refuses any sampling parameter, so the greedy decoding every other
provider gets is not on offer), and reasoning is asked for as `thinking: {"type": "adaptive"}` with
the level as `output_config.effort` rather than the older fixed token budget. Bulk calls send
`thinking: {"type": "disabled"}`, because a photo pass at a 140-token cap that reasons comes back
with no answer in it. A model that predates that dialect needs the older switch written out:
`thinking_params: {thinking: {type: "enabled", budget_tokens: 2048}}`.

```yaml
advanced:
  llm:
    provider: "zai"
    model: "glm-5.3-flash"
    api_key: "${ZAI_API_KEY}"
    thinking: "low"
```

`base_url` defaults to `https://api.z.ai/api/anthropic`, where a coding-plan account is served. The
other route, `https://api.z.ai/api/paas/v4`, is the OpenAI-compatible one and answers that account
`429 code 1113, Insufficient balance`. Set it explicitly if your account is the other kind. z.ai
takes the level as its own word rather than an effort, and the GLM-5 line refuses `disabled`
outright, so the preset sends `low`.

For any other host serving the Messages API, name `base_url` yourself and use `thinking: "auto"`,
which sends no reasoning field in either direction and takes the host's default. That is the setting
to start from when you do not know what the host does with one. `immich-memories preflight` reports
the host's model list, or falls back to a one-token ask when it does not publish one.

### Reasoning

On a server whose chat template reasons by default, a bulk call reasons at its small token budget,
truncates mid-thought and returns nothing parseable. `llm.no_thinking_params` is what stops that, and
its default is already the Qwen dialect:

```yaml
llm:
  thinking: "disabled"            # default
  no_thinking_params:             # merged into every non-thinking call
    chat_template_kwargs:
      enable_thinking: false
```

A server that reasons only when asked wants `no_thinking_params: {}` instead. Setting `thinking` to
`low`, `high` or `max` runs two calls in reasoning mode (title generation and the special-day
question in `discover-days`) while everything else stays fast, and it is refused outright alongside
images. `thinking_params` carries the fields those calls send; OpenAI's reasoning models want
`{"reasoning_effort": "medium"}` there, which `provider: openai` fills in. `true` and `false` still
parse, as `high` and `disabled`.

The provider's own switch is merged in even when you set your own `thinking_params` or
`no_thinking_params`, because the two are not the same request field. A `thinking` key you write
yourself wins over the preset's.

Ollama has neither chat dialect: its switch is a bare top-level `think`, and it bills the thinking
inside `num_predict` the way the OpenAI dialects bill it inside `max_tokens`. So a load-bearing call
gets `think: true`, a bulk call is sent no switch at all (a model with no thinking mode answers
`think` with a 400), and a server that reasons unasked is learned from the first reply carrying a
thinking block: every later call then gets the same 16,384 tokens of room, added to `num_predict`.
An `extra_params.options.num_predict` you set yourself wins over that computed budget.

A level is a request, not a promise. z.ai's `.../api/anthropic` route answers HTTP 200 to every
setting, `disabled` and levels it has never heard of included, and then reasons on its own terms.
Measured on 2026-09-14 with `glm-5.3-flash`, a caption-shaped ask at the readers' 140-token cap spent
all 140 tokens inside a `thinking` block and came back with no answer in it. So on that route the
reader reads the first `text` block and skips the reasoning in front of it, asks for 1,024 tokens on
top of the caller's cap so the cap keeps meaning the length of the answer, and turns a reply with no
`text` block into an error naming the `stop_reason`. When a provider refuses, its own `code` and
`message` ride into the log line, bounded to 300 characters.

## Batch mode

Reading the event evidence is one prompt per episode, and those prompts do not read each other. Every
hosted provider sells that shape cheaper: hand the whole pile over at once, get it back within the
day, pay half.

```yaml
advanced:
  llm:
    batch: "auto"               # off (default) | auto
    batch_min_requests: 8       # below this, asking one at a time is quicker
    batch_max_wait_minutes: 60  # then ask whatever is left in real time
```

It pays on an unattended run: the nightly `auto run`, a scheduled memory, a matrix cell. It does not
pay on a run someone is sitting in front of, because a batch is queued work rather than a slow call
(OpenAI publishes a 24 hour window and usually answers in minutes, and "usually" is not a promise you
want between a click and a video).

What it costs if it goes wrong is the discount and nothing else. Anything unanswered by
`batch_max_wait_minutes` is asked in real time, as is any line the provider refused and any answer
the stage's parser will not read. While it waits, the progress line says which provider it is waiting
on, how many prompts are out, and when they were submitted.

| Provider | Route | Discount |
|---|---|---|
| OpenAI | `/v1/batches` (Batch API) | 50 %, documented |
| Anthropic, and hosts serving its API | `/v1/messages/batches` (Message Batches) | 50 %, documented |
| Melious | `/v1/batches`, same shape as OpenAI | none: their docs say batches run at the same per-token rate |
| z.ai | answers 404 on `/v1/messages/batches` | no batch route; stays realtime, with the reason in the log |

The declared route is probed once before anything is queued, and a host that does not serve it gets
asked once, logs why, and reads in real time for the rest of the run. Only the event evidence read
batches: the period account is a single prompt, the moment inventory pages are each told what the
pages before them found, and the story picks read the stages above them.

## What the software sends

Facts about the pipeline, not properties any particular model was judged on:

- **Prompts run to roughly 6k tokens.** The story pick, the largest single prompt, measured 5,262
  tokens on one host and 5,776 on another. Requests are bounded before they go out: episode reads at
  24,000 characters and 90 assets a page, story synthesis at 32,000, the period account at 96,000
  split into pages.
- **Answers are parsed against the stage's contract.** An answer the contract refuses costs a repair
  round on that call.

The three prompt shapes the matrix probes with are the period account, the episode read and the story
pick, in `scripts/reader_probe_prompts/`. What leaves your network when you point at a provider is on
[Network and privacy](../run/privacy.md).

## Measured on one month

Ten cells were pointed at the same month; seven produced a cut and three stopped. What follows is
what each one did, not what anyone should do about it.

:::note What these numbers are
Measurements of particular models, served by particular providers, on one library, in September 2026.
They are not an endorsement of any model or any shop, and they do not transfer: a provider can change
what a served model does without changing the name it serves it under. This run showed both halves of
that. The same model id on two shops differed only in latency, while a different model on the same
shop refused every image it was sent.

Every cost is the provider's published list price times the tokens the run measured. No provider in
the comparison returns a price with a completion, so nothing here was ever charged to an account and
checked. Each shop is priced in the currency it publishes in.
:::

**Every row in the next table was measured on 15 September 2026.** Four readers were run again on
17 September, on different hosts and tiers, and those rows are
[further down](#re-measured-on-17-september). Nobody re-measured the rest, so their token counts and
prices stand as they were taken.

February 2024: 1,417 candidates, 15 kept. The same library, the
same month, the same banked facts seeded into every cell, one line of config moved per cell.
Selection is the reader stage only, with preparation already warm. Prices were read off the model
pages on 14 September 2026. The cells and the price block are in the
[setup matrix](../contribute/setup-matrix.md).

| Model | Provider and route | Selection | Calls | Tokens in | Tokens out | List price times measured tokens | Overlap |
|---|---|---:|---:|---:|---:|---|---:|
| none, `rules` reader | no endpoint | 8 s | 0 | 0 | 0 | nothing | 25 % |
| `glm-5.3-flash` | Melious, OpenAI-compatible | 13 min 45 s | 156 | 368,198 | 43,280 | EUR 0.054 | 11 % |
| `Huihui-Qwen3.6-35B-A3B-abliterated-oQ4e-mtp` | local oMLX, OpenAI-compatible | 16 min 9 s | 151 | 331,993 | 33,091 | nothing | 30 % |
| `Qwen3-VL-30B-A3B-Instruct-4bit` | local oMLX, OpenAI-compatible | 19 min 3 s | 164 | 386,706 | 36,462 | nothing | 100 % |
| `gpt-5.6-luna` | OpenAI | 20 min 58 s | 168 | 376,015 | 77,173 | USD 0.168 | 11 % |
| `glm-5.3-flash` | z.ai, Anthropic-compatible | 25 min 16 s | 154 | 366,222 | 41,965 | no published price for this account | 20 % |
| `muse-glimmer` | Melious, OpenAI-compatible | 1 h 53 min | 156 | 367,273 | 511,579 | EUR 0.585 | 20 % |

Each kept 15 pictures and produced a 54 to 55 second film. Overlap is the share of identical pictures
against the `Qwen3-VL-30B` cut, which is the matrix's reference cell. It records that two readers
chose differently. It does not record which choice was better; nobody has graded these cuts against
each other. "Calls" is the reader calls the editor's planner made; counting POST requests in the log
gives 182 to 201 instead, because that includes the reads before the planner starts.

Three things fall out of the table:

- Input barely moves. Every reader that finished was handed 330,000 to 390,000 tokens over 150 to 170
  planner calls. Output moves by a factor of twelve, and output is what the price list bills at three
  to six times the input rate.
- The same model id on two shops came out at 13 min 45 s and 25 min 16 s, on token counts within 1 %
  of each other.
- One cluster cell was also priced, on a different host and tier: `gemma-4-31b` on Melious, 112 calls,
  481,387 in, 315,505 out, EUR 0.143. That is the cell described under HTTP 400 below.

The widest gap in the table is two Melious cells that cut the same month from the same seeded facts,
kept 15 pictures each, made **the same 156 calls**, and were handed within 1 % of the same input:

| | `muse-glimmer` | `glm-5.3-flash` |
|---|---:|---:|
| Tokens in | 367,273 | 368,198 |
| Tokens out | **511,579** | **43,280** |
| List price times measured tokens | EUR 0.585 | EUR 0.054 |
| Selection | 1 h 53 min | 13 min 45 s |

The probes locate the difference. Over the three probe prompts `muse-glimmer` produced 20,193 output
tokens against 2,441, and 8,681 of the 9,989 on one of them were inside a thinking block.

### Re-measured on 17 September

The shortlist closed on 15 September: the local 30B, one hosted reader, z.ai for a coding-plan
account, and the rules reader. Those four ran again on 17 September on the released `0.102.0`, as
part of the [setup matrix](../being-rewritten/running-modes.md#what-to-expect-on-a-first-run).

**This is not a second controlled comparison.** The table above moved one line of config per cell on
one host at one tier. These four rows sit on different hosts at different tiers, and the hosted row
read a different month, so read each one against its own setup and not against its neighbours.

| Model | Provider and route | Host and tier | Month | Selection | Calls | Tokens in | Tokens out | Cost | Overlap |
|---|---|---|---|---:|---:|---:|---:|---|---:|
| none, `rules` reader | no endpoint | Mac, `full` | February | 15 s | 0 | 0 | 0 | nothing | 17 % |
| `Qwen3-VL-30B-A3B-Instruct-4bit` | local oMLX, OpenAI-compatible | Mac, `full` | February | 28 min 39 s | 129 | 388,934 | 34,346 | nothing | reference |
| `glm-5.3-flash` | z.ai, Anthropic-compatible | cluster, `no_captions` | February | 12 min 04 s | 110 | about 273,500 | about 37,000 | no published price for this account | 13 % |
| `gpt-5.6-luna` | OpenAI | Mac, `full` | fixture | 2 min 06 s | 93 | 103,865 | 13,003 | USD 0.0364 | 23 % |

The hosted OpenAI row read the fixture month rather than February: hosted spend stays on the fixture
month, where the prompts are about half the size. It is also the one row here that did not come from
`0.102.0`. Every hosted reader was broken in that release by a config regression, fixed in
[#1071](https://github.com/sam-dumont/immich-video-memory-generator/pull/1071), so that cell was
re-run from `main` with the fix in it.

The two rows that read February were measured while other work shared the Mac. The local 30B's
selection had another project's run alive in 29 of its 30 minutes, so its 29 minutes against
19 min 3 s on 15 September is an upper bound and the older figure is the quiet one. Keep both. The
hosted OpenAI row ran after the contention log stopped, so nothing is known about what else was on
the machine then.

The rules row shows 0 calls because the reader made none. The run still made one, in the music stage
after the render, because that Mac had an `llm` endpoint configured: 1,033 prompt and 59 completion
tokens. A rules cell with no endpoint made none at all.

Reading contracts refused 3 of the local 30B's answers and asked again once; they refused 2 of the
z.ai cluster cell's and asked again none. The z.ai token counts come from the run log rather than
the per-call record, and the summary rounds them at or above 1,000.

### The three that stopped

**HTTP 400 for every image.** One hosted model answered text, system prompts, `response_format` and
`reasoning_effort` normally and returned HTTP 400 for every image it was sent
(`code invalid_request_error, message 'The request was rejected as malformed.'`). 46 of those in one
run, all inside the picture-facts loop, with the text calls around them returning 200. On the cluster,
at `no_captions`, the same model did produce a cut, with no picture observations in it. The reader
is no longer sent a picture at all, so this refusal can no longer stop a run.

**Prefill memory exhausted partway through.** A dense 31B on a local oMLX server stopped at call 91
of its story-pick stage, 90 answers in, with
`code prefill_memory_exceeded, message 'Prefill context too large for available memory'`. That run
made 151 calls in 63 minutes before failing; the reference reader completes the same month in 201. On
the same host and the same three probe prompts it produced 20.1 output tokens a second against 81.1
for a sparse 35B. A dense model activates every parameter per token where these sparse ones activate
about a tenth.

**Timed out on two of three probe shapes**, at the 300 s default, having cleared all three on the
fixture month the day before with the slowest at 277.7 s. It passed by 22 seconds.

### What the fixture month did and did not show

The demo library is 133 files, 130 of them eligible. Its reader prompts measured 1,258 tokens a call
against February's 2,378, on the same model over both months: a ten-times-larger candidate pool
yields roughly twice the prompt, because paging absorbs the rest.

Two of the three stopped cells finished it, and so did the cell that spent 511,579 output tokens. The
prefill exhaustion, the timeout and the token volume were visible only on the real month. The HTTP
400s were the exception: that model kept 0 pictures on the fixture month as well.

### Which model to pick

The only configuration whose output has been graded is
`mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit` on oMLX. Everything else is expected to work and
ungraded: nobody has judged a second model's titles, and inventing a quality ranking would be worse
than saying so.

Not measured here: quality, any actual bill, anything but a monthly memory, batch mode's published
50 % discount, and repeat observations. Every row is one run.
