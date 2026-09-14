---
sidebar_position: 7
title: LLM Titles and Mood
---

# LLM Titles and Mood

The `llm` section names the model used by the editor's
period readings and optional title generation. This page covers title settings; see
[The Curator](./the-curator.md) and [Editorial annotation setup](../../deploy/configuration/editorial-preparation.md)
for preparation and selection.

## Any OpenAI-compatible API

Five provider values, three code paths. `ollama` speaks Ollama's native API, `anthropic` speaks
`/v1/messages`, and `openai-compatible` and `openai` speak `/v1/chat/completions`, with compatible servers such as mlx-vlm, [oMLX](https://github.com/jundot/omlx), vLLM, Ollama's
compatibility layer, Groq, OpenAI itself. `zai` picks one of those last two from its `base_url`
path, because z.ai serves both: `.../api/anthropic` gets `/v1/messages`, everything else gets
`/chat/completions`.

`openai` and `zai` fill in the vendor's base URL and reasoning dialect where you left the field at
its default. The provider's own reasoning switch is merged in even when you set your own
`thinking_params` or `no_thinking_params`, because the two are not the same request field. A
`thinking` key you write yourself wins over the preset's, which is how you pick a z.ai reasoning
level other than the one its preset chose for your model.
`openai-compatible` fills in nothing: its `base_url` stays `http://localhost:8080/v1`, which is the
app's own port, so set it.

:::warning The reader needs eyes
The model named in `llm` is sent pictures: 800 px JPEG tiles of the candidates whose facts the
edit demands, plus contact sheets. The model reader requires image support; a text-only model cannot complete its picture pass. See
[the self-hosting guide](../../deploy/self-hosting.md#what-has-been-tested).
:::

## LLM Title Generation

For a trip, the web UI can send a day-by-day location summary to the configured model for a title, subtitle and trip classification. English and French are supported. The model may run locally or on a remote service.

### What the LLM gets

The trip-title prompt uses place names rather than raw coordinates. Other model requests, including editorial evidence and special-day discovery, have different inputs. The selected material's GPS points are clustered greedily within 5 km, each cluster is reverse-geocoded to a city name, and what goes into the prompt is one line per day: the place names and how many of the selected pictures fell at each. From that it works out the travel pattern (base camp? road trip? hiking trail?) and writes a title and subtitle in your locale.

### What it produces

- **Title** and optional **subtitle** in your configured language
- **Trip type**: `base_camp`, `multi_base`, `road_trip`, or `hiking_trail`
- **Map mode** recommendation for the animated map intro
- A one-line **reason** explaining why it picked that classification

The Generation Options page shows the suggestion and lets you edit or regenerate it. The returned map mode does not change the renderer.

On the CLI, model titles are opt-in with `--llm-title`; an explicit `--title` wins. If title generation fails, the CLI uses its template title.

### Reasoning settings

`llm.thinking` allows supported text-only calls to request reasoning. Requests carrying
images disable that switch in the shared query path. Provider-specific fields still
matter: some servers reason by default unless explicitly told otherwise.

```yaml
llm:
  thinking: false
  no_thinking_params:
    chat_template_kwargs:
      enable_thinking: false
```

These defaults use the Qwen request dialect. Use `no_thinking_params: {}` for a server
that needs no such field. The `openai` and `zai` provider presets supply their own
reasoning fields; an explicit matching key takes precedence. Check the server's response
and logs if requests fail or exhaust their token budget.

### Choosing a model

The model reader needs image support as well as reliable structured answers. Use the
[current self-hosting guide](../../deploy/self-hosting.md#what-has-been-tested) for tested
configurations. API compatibility alone does not establish model quality or reliability.

## Mood and music

A vision model is not required to add music. The current generation path uses existing
clip emotions when available and falls back to a calm generation timeline when they are
absent. Bundled track selection can fall back to the whole library. See [Audio & Music](./audio-and-music.md).

## Configuration

The editor and optional title generation share this model unless you configure a title override.

```yaml
advanced:
  llm:
    base_url: "http://localhost:8000/v1"   # example: oMLX. The default is 8080, the app's own port
    model: "mlx-community/Qwen3-VL-30B-A3B-Instruct-4bit"
    api_key: ""                            # set if the server requires authentication
    provider: "openai-compatible"          # or ollama | openai | zai | anthropic
    timeout_seconds: 300                   # the default
```

Use the model ID accepted by your server. OpenAI-compatible servers commonly list these at `GET /v1/models`.

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
default; `provider: openai-compatible`, `base_url: http://localhost:8080/v1`, empty `api_key`.
Leave `title_llm.model` empty and `llm` is used instead. Set the provider, endpoint and credentials explicitly when the title model needs different values.
