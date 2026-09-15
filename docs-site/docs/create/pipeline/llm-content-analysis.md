---
sidebar_position: 7
title: LLM Titles and Mood
---

# LLM Titles and Mood

The `llm` section names one OpenAI-compatible model and three things read it: the editor's
period readings (see [The Curator](./the-curator.md) and
[Editorial annotation setup](../../deploy/configuration/editorial-preparation.md)), the trip
titles below, and the mood detection the music pipeline uses. This page covers the last two.

## Any OpenAI-compatible API

Five provider values, three code paths. `ollama` speaks Ollama's native API, `anthropic` speaks
`/v1/messages`, and `openai-compatible` and `openai` speak `/v1/chat/completions`, so anything that
serves that endpoint works: mlx-vlm, [oMLX](https://github.com/jundot/omlx), vLLM, Ollama's
compatibility layer, Groq, OpenAI itself. `zai` is the `anthropic` adapter with z.ai's URL and
reasoning level filled in, and it is the one provider that picks its adapter from the `base_url`
path, because z.ai serves both dialects on one host: `.../api/anthropic` gets `/v1/messages`,
`.../api/paas/v4` gets `/chat/completions`.

`openai`, `anthropic` and `zai` fill in the vendor's base URL and reasoning dialect where you left
the field at its default. The provider's own reasoning switch is merged in even when you set your
own `thinking_params` or `no_thinking_params`, because the two are not the same request field. A
`thinking` key you write yourself wins over the preset's, which is how you pick a z.ai reasoning
level other than the one its preset chose for your model.
`openai-compatible` fills in nothing: its `base_url` stays `http://localhost:8080/v1`, which is the
app's own port, so set it.

## Anthropic-compatible

`provider: anthropic` is the Messages API: `POST {base_url}/v1/messages`, `x-api-key`,
`anthropic-version: 2023-06-01`, the prompt and then the reader's tiles as base64 `image` blocks in
one user message. Claude serves it, and so does every host that copied it. Nothing about the app
is Claude-specific: point `base_url` at whoever serves the dialect and the reader works.

Answers come back as a JSON envelope the app validates itself. No provider-side JSON mode is used,
so a host that does not have one loses nothing.

**Claude**

```yaml
advanced:
  llm:
    provider: "anthropic"
    model: "claude-sonnet-5"        # or claude-haiku-4-5 for the cheap seat
    api_key: "${ANTHROPIC_API_KEY}"
    thinking: "high"                # disabled | low | high | max | auto
```

`base_url` defaults to `https://api.anthropic.com`, so leave it out. Two things the preset handles
for you, because Claude answers HTTP 400 otherwise: no `temperature` goes out (from the 4.7 line on
Claude refuses any sampling parameter, so the greedy decoding every other provider gets is not on
offer), and reasoning is asked for as `thinking: {"type": "adaptive"}` with the level as
`output_config.effort` rather than the older fixed token budget. Bulk calls send
`thinking: {"type": "disabled"}`: Claude's current models reason by default, and a photo pass at a
140-token cap that reasons comes back with no answer in it. If you pin a model that predates that
dialect, write the older switch out yourself:

```yaml
    thinking_params:
      thinking: {type: "enabled", budget_tokens: 2048}
```

**z.ai coding plan**

```yaml
advanced:
  llm:
    provider: "zai"
    model: "glm-5.3-flash"
    api_key: "${ZAI_API_KEY}"
    thinking: "low"
```

`base_url` defaults to `https://api.z.ai/api/anthropic`, which is where a coding-plan account is
served. The other route, `https://api.z.ai/api/paas/v4`, is the OpenAI-compatible one and answers
that account `429 code 1113, Insufficient balance`. Set it explicitly if your account is the other
kind. z.ai takes the level as its own word rather than an effort, and the GLM-5 line refuses
`disabled` outright, so the preset sends `low` there.

**Anything else that serves the Messages API**

```yaml
advanced:
  llm:
    provider: "anthropic"
    base_url: "https://your-gateway.example.com/anthropic"
    model: "whatever-it-serves"
    api_key: "${ANTHROPIC_API_KEY}"
    thinking: "auto"
```

An explicit `base_url` wins over the preset and the adapter stays the Messages API. `auto` sends no
reasoning field in either direction and takes the host's default, which is the setting to start
from when you do not know what the host does with one. Run `immich-memories preflight` and it will
report the host's model list, or fall back to a one-token ask when it does not publish one.

:::warning The reader needs eyes
The model named in `llm` is sent pictures: 800 px JPEG tiles of the candidates whose facts the
edit demands, plus contact sheets. A text-only model will not do the picture pass, and the run
does not degrade politely into one that can. See
[the self-hosting guide](../../deploy/self-hosting.md#what-has-been-tested).
:::

## Batch mode

Reading the event evidence is one prompt per episode, and those prompts do not read each
other. Every hosted provider sells that shape cheaper: hand the whole pile over at once,
get it back within the day, pay half. Set `llm.batch: auto` and the stage does exactly
that.

```yaml
advanced:
  llm:
    batch: "auto"               # off (default) | auto
    batch_min_requests: 8       # below this, asking one at a time is quicker
    batch_max_wait_minutes: 60  # then ask whatever is left in real time
```

**When it pays.** An unattended run: the nightly `auto run`, a scheduled memory, a matrix
cell. Half the bill on the largest stage of a hosted run is real money, and nobody is
watching the bar.

**When it does not.** A run someone is sitting in front of. A batch is queued work, not a
slow call. OpenAI publishes a 24 hour completion window and usually answers in minutes;
"usually" is not a promise you want between a click and a video.

**What it costs you if it goes wrong: the discount, and nothing else.** Anything the
provider has not answered by `batch_max_wait_minutes` is asked in real time, as is any
line it refused, and any answer the stage's own parser will not read. The run finishes
either way. While it waits, the progress line says which provider it is waiting on, how
many prompts are out, and when they were submitted.

**Which hosts.** Two routes cover the field, and the declared one is probed once before
anything is queued:

| Provider | Route | Discount |
|---|---|---|
| OpenAI | `/v1/batches` (Batch API) | 50%, documented |
| Anthropic, and hosts serving its API | `/v1/messages/batches` (Message Batches) | 50%, documented |
| Melious | `/v1/batches`, same shape as OpenAI | none: their docs say batches run at the same per-token rate |
| z.ai | answers 404 on `/v1/messages/batches` | no batch route; stays realtime, with the reason in the log |

A host that does not serve the route it was expected to serve gets asked once, logs why,
and reads in real time for the rest of the run.

**Which stages.** Only the event evidence read, because it is the only stage whose prompts
are independent of each other. The period account is a single prompt. The moment inventory
pages are each told what the pages before them found. The story picks read the stages
above them. A batch is submitted whole, so none of those can be in one.

## Trip titles

A template gives you "TWO WEEKS IN SPAIN, SUMMER 2025". The model gives you "Sous les falaises de
grès" or "Odyssée le long de la côte". English and French are the two locales the app ships.

The model never sees coordinates. The selected material's GPS points are clustered greedily within
5 km, each cluster is reverse-geocoded to a city name, and the prompt is one line per day: the place
names and how many of the selected pictures fell at each. Back come a title, an optional subtitle, a
[trip classification](./title-screens-and-maps.md#trip-classification) with a one-line reason, and a
map mode for the intro. All of it is editable on the Generation Options page, and the regenerate
button asks again over the same GPS data.

## Thinking mode has to be off

On a server whose chat template reasons by default, a bulk call reasons at its small token budget,
truncates mid-thought and returns nothing parseable. That is what `llm.no_thinking_params` is for,
and its default is already the Qwen dialect:

```yaml
llm:
  thinking: "disabled"            # default
  no_thinking_params:             # merged into every non-thinking call
    chat_template_kwargs:
      enable_thinking: false
```

A server that reasons only when asked wants `no_thinking_params: {}` instead. Setting `thinking` to
`low`, `high` or `max` runs two calls in reasoning mode (title generation and the special-day
question in `discover-days`), while everything else stays fast, and it is refused outright
alongside images; `thinking_params` carries the fields those calls send, defaulting to the same
Qwen dialect. OpenAI's reasoning models want `{"reasoning_effort": "medium"}` there, which
`provider: openai` fills in for you.

The level only reaches hosts that take one: Claude gets it as `output_config.effort`, z.ai as its
own level word. Everywhere else it is on or off, and `thinking_params` says how hard. `auto` sends
nothing in either direction. `true` and `false` still parse, as `high` and `disabled`.

A level is a request, not a promise. z.ai's `.../api/anthropic` route answers HTTP 200 to every
setting, `disabled` and levels it has never heard of included, and then reasons on its own terms.
Measured on 2026-09-14 with `glm-5.3-flash`: a caption-shaped ask at the readers' 140-token cap
spent all 140 tokens inside a `thinking` block and came back with no answer in it. So on that route
the reader reads the first `text` block and skips the reasoning in front of it, asks for 1,024
tokens on top of the caller's cap so the cap keeps meaning the length of the answer, and turns a
reply with no `text` block into an error naming the `stop_reason` instead of an empty string.

When a provider refuses, its own `code` and `message` ride into the reader's log line, bounded to
300 characters. Before that, a 400 and a 429 reached the operator as the bare status and a link to
MDN, so `1210` and `1113 Insufficient balance` both read as "Client error".

## Which model

The only configuration whose output has been graded is
`mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit` on oMLX. Everything else is expected to work and
ungraded: nobody has judged a second model's titles, and inventing a quality ranking would be worse
than saying so.

Speed and cost are a different question, and ten cells were measured on one real month:
[Readers](../../deploy/readers.md). Those numbers are the editor's period reads, not title
generation, but it is the same `llm` model and the same endpoint.

## Mood for music

The same model picks the memory's mood, which is what chooses a bundled track and what the
generators are asked for. It reads text: the saved cut's thesis, story labels and prepared captions.
No new images go out. See [Audio & Music](./audio-and-music.md).

## Configuration

Every `llm:` key, with its default, is in the
[config reference](../../reference/config-reference.md#llm-vision-model). The three that have to be
right: `base_url` (which defaults to the app's own port, so set it), `model` (the exact string the
server reports at `GET /v1/models`) and `provider`, which picks the dialect and, on `openai`,
`anthropic` and `zai`, fills in the vendor URL when you leave `base_url` alone.
