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
`/v1/messages`, and `openai-compatible`, `openai` and `zai` all speak `/v1/chat/completions` —
so anything that serves that endpoint works: mlx-vlm, [oMLX](https://github.com/jundot/omlx),
vLLM, Ollama's compatibility layer, Groq, OpenAI itself.

`openai` and `zai` are the same code path with the vendor's base URL and reasoning dialect filled
in, and only where you left the field at its default. `openai-compatible` fills in nothing: its
`base_url` stays `http://localhost:8080/v1`, which is the app's own port, so set it.

:::warning The reader needs eyes
The model named in `llm` is sent pictures — 800 px JPEG tiles of the candidates whose facts the
edit demands, plus contact sheets. A text-only model will not do the picture pass, and the run
does not degrade politely into one that can. See
[the self-hosting guide](../../deploy/self-hosting.md#what-has-actually-been-tested).
:::

## LLM Title Generation

Instead of generic "TWO WEEKS IN SPAIN, SUMMER 2025" template titles, the app feeds your trip's raw GPS data to a local LLM and gets back something like "Sous les falaises de grès" or "Odyssée le long de la côte". It works in any language and classifies your trip pattern too.

### What the LLM gets

After the analysis phase completes, the LLM receives daily GPS clusters: how many photos you took at each location, each day. From that raw data, it figures out the travel pattern (base camp? road trip? hiking trail?) and generates a title + subtitle in your locale. No pre-processing, no clustering algorithm telling it what to think: just the raw photo distribution and the model's own reasoning.

### What it produces

- **Title** and optional **subtitle** in your configured language
- **Trip type**: `base_camp`, `multi_base`, `road_trip`, or `hiking_trail`
- **Map mode** recommendation for the animated map intro
- A one-line **reason** explaining why it picked that classification

You see everything in Step 3 of the UI and can edit before rendering. Hit the regenerate button to try again with the same GPS data.

### Thinking mode has to be off

On a server whose chat template reasons by default, a bulk call reasons at its small token budget,
truncates mid-thought and returns nothing parseable. That is what `llm.no_thinking_params` is for,
and its default is already the Qwen dialect:

```yaml
llm:
  thinking: false                 # default
  no_thinking_params:             # merged into every non-thinking call
    chat_template_kwargs:
      enable_thinking: false
```

A server that reasons only when asked wants `no_thinking_params: {}` instead. Turning `thinking:
true` back on runs two calls in reasoning mode — title generation and the special-day question in
`discover-days` — while everything else stays fast, and it is refused outright alongside images; `thinking_params` carries the fields those calls send, defaulting to the
same Qwen dialect. OpenAI's reasoning models want `{"reasoning_effort": "medium"}` there, which
`provider: openai` fills in for you.

### Which model

The only configuration whose output has been graded is
`mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit` on oMLX. Everything else is expected to work and
ungraded — there is no per-model speed or reliability table here, because nobody has measured one
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

One section names the model; the editor, titles and mood detection all read it.

```yaml
advanced:
  llm:
    base_url: "http://localhost:8000/v1"   # example: oMLX. The default is 8080, the app's own port
    model: "mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit"
    api_key: ""                            # local servers ignore it
    provider: "openai-compatible"          # or ollama | openai | zai | anthropic
    timeout_seconds: 300                   # the default
```

`model` has to be the string the server reports at `GET /v1/models`, not the name you typed
somewhere else.

A separate `title_llm` section can point trip titles at a different model:

```yaml
advanced:
  title_llm:
    provider: "openai-compatible"
    base_url: "http://localhost:11434/v1"
    model: "llama3.2"
    timeout_seconds: 300
```

**Fields do not fall back to `llm`.** The switch is all-or-nothing on `title_llm.model`: set it
and the whole `title_llm` block is used, with every field you left out taking its *built-in*
default — `provider: openai-compatible`, `base_url: http://localhost:8080/v1`, empty `api_key`.
Leave `title_llm.model` empty and `llm` is used instead. Write out every field you care about, or
the two-line version above silently resets five others.
