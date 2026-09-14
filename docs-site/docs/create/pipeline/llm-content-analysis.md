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

## LLM Title Generation

Instead of generic "TWO WEEKS IN SPAIN, SUMMER 2025" template titles, the app hands a local LLM a day-by-day summary of where the trip went and gets back something like "Sous les falaises de grès" or "Odyssée le long de la côte". English and French are the two locales the app ships; it classifies the trip pattern at the same time.

### What the LLM gets

The model never sees coordinates. The selected material's GPS points are clustered greedily within 5 km, each cluster is reverse-geocoded to a city name, and what goes into the prompt is one line per day: the place names and how many of the selected pictures fell at each. From that it works out the travel pattern (base camp? road trip? hiking trail?) and writes a title and subtitle in your locale.

### What it produces

- **Title** and optional **subtitle** in your configured language
- **Trip type**: `base_camp`, `multi_base`, `road_trip`, or `hiking_trail`
- **Map mode** recommendation for the animated map intro
- A one-line **reason** explaining why it picked that classification

You see everything on the Generation Options page and can edit before rendering. Hit the regenerate button to try again with the same GPS data.

### Thinking mode has to be off

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

### Which model

The only configuration whose output has been graded is
`mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit` on oMLX. Everything else is expected to work and
ungraded: there is no per-model speed or reliability table here, because nobody has measured one
on this route and inventing one would be worse than saying so.

## Mood Detection for Music

The music pipeline needs a vision LLM to analyze video keyframes and detect mood. Any server that speaks the OpenAI `/v1/chat/completions` endpoint works: it just needs to handle image inputs.

### LLM Setup

**mlx-vlm (Recommended on Apple Silicon)**:

```bash
uvx --python 3.12 --from mlx-vlm --with torch --with torchvision \
  mlx_vlm.server --port 8080
```

**Ollama**:

```bash
ollama pull llava
ollama serve
```

**Cloud APIs (Groq, OpenAI, etc.)**: any cloud API that supports vision and speaks the OpenAI chat completions format works.

## Configuration

Every `llm:` key, with its default, is in the
[config reference](../../reference/config-reference.md#llm-vision-model). The three that have to be
right: `base_url` (which defaults to the app's own port, so set it), `model` (the exact string the
server reports at `GET /v1/models`) and `provider`, which picks the dialect and, on `openai`,
`anthropic` and `zai`, fills in the vendor URL when you leave `base_url` alone.
