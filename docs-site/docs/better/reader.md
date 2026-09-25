---
title: Add a reader
sidebar_label: "Add a reader (local or hosted)"
---

# Add a reader

Reader: power user.

The NAS makes the film without one. A reader is a text model that writes the prose (what happened
in each episode, an account of the period, the film's title, the music's mood) and then polishes the
rules draft: it names the shots that add nothing, and a better shot from the same story takes the
seat. It never plans a one-window film from scratch, and it never sees a picture: a model looks at
each picture once, at ingest, and the reader works from the text ingest banked. What exactly it
changes, with diagrams: [What a model adds](../how-it-chooses/what-a-model-adds.md).

## What you need

- A text model with at least a 32k context. No vision needed.
- An endpoint that speaks the OpenAI `/v1/chat/completions` or the Anthropic `/v1/messages` API, or
  Ollama's own.
- For a local model, a machine that holds it for as long as its server is up. The default is
  **Gemma 4 E4B** (`mlx-community/gemma-4-e4b-it-6bit` on a Mac, `google/gemma-4-E4B-it` under vLLM
  or Ollama; Apache 2.0): 5.7 GB of weights and 6.7 GB at its peak on an 8k-token read, so it fits
  a 16 GB Mac beside the 500M caption server (2.4 GB).

It is the one graded model. A bigger one (a 30B, about 17 GB) was measured against it and did not
make better cuts, so it is not worth the memory. Anything else that holds 32k and returns valid JSON is expected to work, and its
quality is your own measurement.

### How the default was chosen

Gemma 4 E4B against a 30B (Qwen3-VL-30B-A3B) on the same four months and one year, with the same prepared store and
the same rules draft: every episode reading, account and title written by Gemma, the polish vote
left with the 30B in both.

| | Gemma 4 E4B | the 30B |
|---|---|---|
| episode readings read on the first try (public test set, 243 episodes) | 100 % | 97 to 100 % |
| names or places in the prose that the input does not carry (owner year) | 0 of 591 | 0 of 230 |
| prose seconds, one year cold | 929 | 763 |
| the finished cut's overlap with the 30B's (months; year) | 0.80 to 1.00; 0.99 | 1.00 (February, asked twice) |
| finished-cut invariant violations | 0 | 0 |

Known gaps, measured and left as they are: Gemma names fewer moments as records (58 against 146 on
the year, most of the 30B's extra ones infer a "first" the prompt forbids); its episode sentences
read more like a list than a story; the special-day scan (`discover-days`) finds a different set of
days than the 30B does, and neither set was judged better. Gemma 4 E2B (the 2B one) parses well but
leaves the polish vote with nothing to remove, names the prompt's own example city in a quarter of
its titles (the title check then falls back to the template), and writes music moods outside the
allowed list, so it is not recommended.

The `full` tier (a [caption server](./captions.md)) is worth adding with a reader: the reader reads
the captions, and the family-viewing check's activity question needs them.

## Local, on a Mac

[oMLX](https://github.com/jundot/omlx) serves MLX models over an OpenAI-compatible API (macOS 15+):

```bash
brew tap jundot/omlx https://github.com/jundot/omlx
brew install jundot/omlx/omlx
omlx start        # serves on port 8000
```

Pull `mlx-community/gemma-4-e4b-it-6bit` from `http://localhost:8000/admin/chat`, then point the
app at it:

```yaml
advanced:
  llm:
    provider: openai-compatible
    base_url: http://localhost:8000/v1
    model: gemma-4-e4b-it-6bit
```

`model` must be exactly what the server reports at `GET /v1/models`. `base_url` defaults to
`http://localhost:8080/v1`, the app's own port, so always set it. From the app in Docker the host
is `host.docker.internal`, and from a NAS it is the Mac's LAN name:
[Reaching a model server](../run/docker.md#reaching-a-model-server). A server that answers `401`
wants its token in `llm.api_key` (`IMMICH_MEMORIES_LLM__API_KEY`).

On Linux with a card, serve the same model with vLLM or Ollama and set `base_url` and `model` the
same way. [mlx-vlm](https://github.com/Blaizzy/mlx-vlm) is another way to serve it on a Mac.

## Hosted

Same contract, a provider URL and a key. Read this first: **the candidates' annotation lines leave
your network**, with the people and place names on them, the dates and the captions. No picture
does. If that text should stay home, run the model locally.

```yaml
advanced:
  llm:
    provider: "openai"             # ollama | openai-compatible | openai | zai | anthropic
    model: "gpt-4.1-mini"
    api_key: "${OPENAI_API_KEY}"
```

Leave `base_url` unset and `openai`, `anthropic` and `zai` fill in their own. The prose is banked,
so a week is read and paid for once, not once per film. Every outbound request is on
[Privacy](../run/privacy.md).

## Check it

```bash
immich-memories preflight
```

The `LLM` row checks that the endpoint answers for your model (Ollama's tag list, a minimal chat
call on an OpenAI-compatible host, the model list or a one-token ask on an Anthropic one). On the
rules reader it reads `SKIPPED`. A blank `llm.model` means rules (`advanced.editorial.reader: auto`,
the default); `reader: model` with a blank model stops with
`editorial runtime needs a nonblank LLM model`.

A reader that fails mid-film does not fail the film. The period account is asked twice; after the
second failure the rules draft ships with the passes a no-model film gets, and the log says so:
`The model polish did not run (<reason>); the film is the rules draft`.

## The Laya audience pre-screen

On Apple Silicon, one question the reader asks of every shot on the `full` tier can be answered
locally instead: does the caption describe a bath, a nappy change, breastfeeding or one of the other
private activities a family film holds back. Laya is a 0.4B text classifier (Apache-2.0),
fine-tuned on captions of public CC BY photographs whose authors are credited in the archive. It
reads the same caption, in about 14 ms a shot where the reader takes seconds. It is off by
default.

```bash
pip install laya-mlx                         # Apple Silicon only
immich-memories models fetch --laya          # 811 MB, digest-pinned
```

```yaml
advanced:
  editorial:
    laya_audience: true
```

It only adds holds. The detector holds (the sensitive-content detector and the uncovered-person
head) apply first and are never lifted, its findings go through the same support checks as the
reader's, and a shot it doesn't answer goes to the reader. The threshold,
`laya_audience_threshold: 0.186`, is the lowest that kept every hold of its public calibration
split. Its known gap: a travel or administrative document (a boarding pass, an invoice) can slip
through, since few such captions were in its training data. The detectors stay the floor either way.

## Providers and dialects

Five provider values, three code paths. `ollama` speaks Ollama's native API, `anthropic` speaks
`/v1/messages`, and `openai-compatible` and `openai` speak `/v1/chat/completions`, so anything
serving that endpoint works: mlx-vlm, oMLX, vLLM, Ollama's compatibility layer, Groq, OpenAI
itself. `zai` is the `anthropic` adapter with z.ai's URL and reasoning level filled in, and it is
the one provider that picks its adapter from the `base_url` path, because z.ai serves both dialects
on one host: `.../api/anthropic` gets `/v1/messages`, `.../api/paas/v4` gets `/chat/completions`.

`openai`, `anthropic` and `zai` fill in the vendor's base URL and reasoning dialect where you left
the field at its default. `openai-compatible` fills in nothing. An explicit `base_url` always wins.

The Messages API path is `POST {base_url}/v1/messages` with `x-api-key`,
`anthropic-version: 2023-06-01` and the prompt as one user message. Nothing about it is
Claude-specific: point `base_url` at whoever serves the dialect. Answers come back as a JSON
envelope the app validates itself, and no provider-side JSON mode is used, so a host without one
loses nothing.

```yaml
advanced:
  llm:
    provider: "anthropic"
    model: "claude-sonnet-5"        # or claude-haiku-4-5 for the cheap seat
    api_key: "${ANTHROPIC_API_KEY}"
    thinking: "high"                # disabled | low | high | max | auto
```

That preset handles two things Claude answers HTTP 400 to otherwise: no `temperature` goes out
(from the 4.7 line on, Claude refuses any sampling parameter), and reasoning is asked for as
`thinking: {"type": "adaptive"}` with the level as `output_config.effort`. Bulk calls send
`thinking: {"type": "disabled"}`, because a bulk call at a 140-token cap that reasons comes back with
no answer in it. A model older than that dialect needs the switch written out:
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
`429 code 1113, Insufficient balance`; set it explicitly if your account is the other kind. The
GLM-5 line refuses `disabled`, so the preset sends `low`.

For any other host serving the Messages API, set `base_url` yourself and use `thinking: "auto"`,
which sends no reasoning field and takes the host's default.

### Reasoning

On a server whose chat template reasons by default, a bulk call reasons through its small token
budget, stops mid-thought and returns nothing parseable. `llm.no_thinking_params` stops that, and
its default is already the Qwen dialect:

```yaml
llm:
  thinking: "disabled"            # default
  no_thinking_params:             # merged into every non-thinking call
    chat_template_kwargs:
      enable_thinking: false
```

A server that reasons only when asked wants `no_thinking_params: {}` instead. `low`, `high` or
`max` switch two calls to reasoning (title generation and the special-day question in
`discover-days`) while everything else stays fast. `thinking_params` carries the fields those calls
send; OpenAI's reasoning models want `{"reasoning_effort": "medium"}` there, which `provider:
openai` fills in. `true` and `false` still parse, as `high` and `disabled`. The provider's own switch
is merged in even when you set your own params, and a `thinking` key you write yourself wins.

Ollama has neither chat dialect: its switch is a bare top-level `think`, billed inside
`num_predict`. A load-bearing call gets `think: true`, a bulk call gets no switch at all (a model
without a thinking mode answers `think` with a 400), and a server that reasons unasked is learned
from its first thinking block: every later call then gets 16,384 extra tokens in `num_predict`. An
`extra_params.options.num_predict` you set yourself wins.

A level is a request, not a promise. z.ai's `.../api/anthropic` route answers HTTP 200 to every
setting and then reasons on its own terms, so on that route the reader reads the first `text` block
and skips the reasoning in front of it, asks for 1,024 tokens on top of the caller's cap, and turns
a reply with no `text` block into an error naming the `stop_reason`. A provider's own error `code`
and `message` go into the log line, cut at 300 characters.

## Batch mode

The episode readings are one prompt per episode, and those prompts don't read each other. Every
hosted provider sells that shape cheaper: hand the pile over at once, get it back within the day,
pay half.

```yaml
advanced:
  llm:
    batch: "auto"               # off (default) | auto
    batch_min_requests: 8       # below this, asking one at a time is quicker
    batch_max_wait_minutes: 60  # then ask whatever is left in real time
```

It pays on an unattended run (the nightly `auto run`, a `prepare --overviews` over a year) and not
on a run someone is waiting for: a batch is queued work, and "usually within minutes" is not a
promise you want between a click and a film. If it goes wrong you lose the discount and nothing
else. Anything unanswered by `batch_max_wait_minutes`, any line the provider refused and any
answer the parser won't read is asked again in real time.

| Provider | Route | Discount |
|---|---|---|
| OpenAI | `/v1/batches` (Batch API) | 50 %, documented |
| Anthropic, and hosts serving its API | `/v1/messages/batches` (Message Batches) | 50 %, documented |
| Melious | `/v1/batches`, same shape as OpenAI | none: their docs say batches run at the same per-token rate |
| z.ai | answers 404 on `/v1/messages/batches` | no batch route; stays real time, with the reason in the log |

The route is probed once before anything is queued. A host that doesn't serve it is asked once,
logs why, and reads in real time for the rest of the run.

## What the software sends

- **Prompts are bounded before they go out**: episode reads at 24,000 characters and 90 pictures a
  page, story synthesis at 32,000, the period account split into pages. A 32k context holds every
  one.
- **Answers are parsed against the stage's contract.** An answer the contract refuses costs one
  repair round on that call.
- **Text only.** No request to the reader carries a picture; a test fails the build if one does.

The prompt shapes the setup matrix probes readers with are in `scripts/reader_probe_prompts/`.
Time, tokens and euros per reader go on [Measured](./measured.md).
