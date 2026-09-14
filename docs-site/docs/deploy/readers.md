---
sidebar_position: 3
title: Readers
sidebar_label: "Readers: what works"
---

# Which models can read a period

The reader is the model the editor hands a period to. It reads the annotation lines, writes the
story, and asks for 800 px tiles of the few dozen pictures it cannot settle on paper. Any
OpenAI-compatible or Anthropic-compatible endpoint can go in `llm`. Not all of them finish the job.

Everything below was measured by the [setup matrix](../contribute/setup-matrix.md) on one real
month, February 2024: 13,552 pictures in scope, 1,417 of them candidates, 15 kept. Same library,
same month, the same banked facts seeded into every cell, one line of config moved. Selection is
the reader stage only, preparation already warm. Every cost is list price times measured tokens,
never a bill.

## What the job asks of a model

Three things, and a model that misses any one of them cannot be configured into working:

- **It must accept images.** The picture pass posts 800 px JPEG tiles at quality 90. A text-only
  model does not fail loudly: every picture request comes back empty, is banked as a failure, and
  the edit finishes carrying `picture observations unavailable`.
- **It must hold about 6k tokens of prompt comfortably.** The story pick, the largest single prompt
  the reader sends, measured 5,262 tokens on one host and 5,776 on another. A 32k context is the
  floor, because requests are bounded before they go out: episode reads at 24,000 characters and 90
  assets a page, story synthesis at 32,000, the period account at 96,000 split into pages.
- **It must answer a structured request without needing repair.** The app validates the JSON
  envelope itself and never asks the provider for a JSON mode, so a host without one loses nothing.
  A model that has to be asked twice pays for it on every call.

## What each reader took

| Reader | Where it runs | Selection | Cost per cut | Overlap with the reference cut |
|---|---|---:|---:|---:|
| `rules`, no model at all | nowhere | 8 s | free | 25 % |
| `glm-5.3-flash` | Melious, OpenAI-compatible route | 13 min 45 s | EUR 0.054 | 11 % |
| `Huihui-Qwen3.6-35B-A3B-abliterated-oQ4e-mtp` | a local OpenAI-compatible server | 16 min 9 s | free | 30 % |
| `Qwen3-VL-30B-A3B-Instruct-4bit` | the same, and the graded reference | 19 min 3 s | free | 100 % |
| `gpt-5.6-luna` | OpenAI | 20 min 58 s | USD 0.168 | 11 % |
| `glm-5.3-flash` | z.ai, Anthropic-compatible route | 25 min 16 s | flat-fee coding plan | 20 % |

Every one of those kept 15 pictures and made a 54 to 55 second film. Overlap is the share of
identical pictures against the local reference cut; it says two readers disagreed, not which one
was right. Nobody has graded these cuts against each other.

The same model on two providers is 14 minutes against 25. Provider and model are separate choices.

## What a cut costs in tokens

Every cost on this page is **the provider's list price times the tokens the run measured**. It is
not a bill. No provider in the comparison returns a price with a completion, so nothing here was
ever charged to an account and checked. Each shop is priced in the currency it publishes in and
nothing converts between them: an exchange rate is a number nobody measured.

| Reader | Calls | Tokens in | Tokens out | At list |
|---|---:|---:|---:|---|
| `Qwen3-VL-30B-A3B-Instruct-4bit`, local | 164 | 386,706 | 36,462 | free |
| `Huihui-Qwen3.6-35B-A3B`, local | 151 | 331,993 | 33,091 | free |
| `glm-5.3-flash` on Melious | 156 | 368,198 | 43,280 | EUR 0.054 |
| `glm-5.3-flash` on z.ai | 154 | 366,222 | 41,965 | no price list |
| `gpt-5.6-luna` on OpenAI | 168 | 376,015 | 77,173 | USD 0.168 |
| `gemma-4-31b` on Melious, cluster cell | 112 | 481,387 | 315,505 | EUR 0.143 |

"Calls" is the reader calls the editor's planner made. Counting POST requests in the log gives 182
to 201 instead, because that includes the reads before the planner starts. Both numbers are true of
the same run.

One monthly memory is therefore 150 to 170 planner calls and about 370,000 input tokens, on every
reader that finished. Input barely moves between them. **Output is where the bill is decided**, and
it moves by a factor of twelve.

The cluster row is the only one that also carries a render and a full pipeline, so read it as the
size of a monthly bill rather than as a recommendation of the model in it: `gemma-4-31b` is the
model in the images rejection below, and 46 of its picture requests came back HTTP 400. It cut the
month without them.

## Not supported, and why

Each of these was measured. Every one of them finished a fixture month first.

**A dense 31B on a local server.** It died at call 91 of its story-pick stage, 90 answers in, with
the server refusing the prompt outright:

```text
code prefill_memory_exceeded, message 'oMLX prefill memory guard rejected this prompt:
Prefill context too large for available memory'
```

That run made 151 calls in 63 minutes before failing, where the reference reader completes the same
month in 201 calls. It is also the slowest local reader measured: on the same host and the same
three probe prompts it produced 20.1 output tokens a second against 81.1 for a sparse 35B, and on
an earlier probe 17.6 against 167.7. Call it four to ten times slower, depending on the day. A
dense model activates every parameter per token; the sparse ones activate about a tenth. At the
same weight on disk, sparse is the better buy.

**A model whose API refuses images.** One hosted candidate answered text, system prompts,
`response_format` and `reasoning_effort` normally, and returned HTTP 400 for any image:

```text
code invalid_request_error, message 'The request was rejected as malformed.
Check the message format, tools schema, or response_format.'
```

46 of those in one run, all of them inside the picture-facts loop, all of the text calls around
them returning 200. The reader sends 800 px tiles there, so that is where the run dies. Nothing
configurable fixes it, and a probe that only sends text will never find it.

**A model that answers correctly and ruinously.** Two Melious readers cut the same month from the
same seeded facts and both kept 15 pictures. They made **the same 156 calls** and were handed
within 1 % of the same input. What came back was not comparable:

| | `muse-glimmer` | `glm-5.3-flash` |
|---|---:|---:|
| Tokens in | 367,273 | 368,198 |
| Tokens out | **511,579** | **43,280** |
| At list | **EUR 0.585** | **EUR 0.054** |
| Selection | 1 h 53 min | 13 min 45 s |

Eleven times the output for the same job, ten times the price, and eight times the wall clock. The
probes say where it goes: on the three probe prompts muse-glimmer produced 20,193 output tokens
against 2,441, and 8,681 of the 9,989 on one of them were inside a thinking block. A model that
reasons at length about a photograph is expensive twice, in money and in the two hours you wait.

**A model too slow to finish.** One candidate timed out on two of the three probe shapes at the
300 s default:

```text
  episodes    -    -  0 in  0 out  300.0s  transport: TimeoutError
  period      200  stop  1515 in  1286 out  104.1s  ok (4 evidence rows)
  story-pick  -    -  0 in  0 out  300.0s  transport: TimeoutError
```

The same model had cleared all three on the fixture month a day earlier, with the slowest shape at
277.7 s. It passed by 22 seconds, and that is the whole argument of the next section.

## A fixture month cannot decide this

The demo library is 133 pictures, 130 of them eligible, and its reader prompts are about half the
size of a real month's: 1,258 prompt tokens a call against 2,378 on February, measured on the same
model over both. A ten-times-larger candidate pool yields roughly twice the prompt, because paging
absorbs the rest.

Every rejection above passed the fixture month. A model that runs out of context, or is merely
slow, still finishes it and looks fine doing it. Probe a candidate on one real month before you
trust it, and read the wall clock as well as the answer.

## Bringing your own model

Name it in `llm` and run one real month. `immich-memories preflight` checks the host answers and
reports its model list first. The three prompt shapes the matrix probes with are the period
account, the episode read and the story pick, in `scripts/reader_probe_prompts/`; the story pick is
the big one and the one that decides whether a context window is enough.

Watch for three things the probe will show you: a timeout on any shape, an HTTP 400 when a tile
goes out, and an output-token count several times the others for the same answer.

Provider dialects, reasoning switches and batch mode are on
[LLM titles and mood](../create/pipeline/llm-content-analysis.md#any-openai-compatible-api). What
leaves your network when you point at a provider is on
[Network & Privacy](./configuration/network-and-privacy.md).

## What is still unmeasured

- Quality. This page is time and money. The only reader whose output has been judged end to end is
  the local `Qwen3-VL-30B-A3B-Instruct-4bit` reference.
- Any actual bill. Every cost here is list price times measured tokens, because no provider in the
  comparison returns a price with a completion.
- Anything but a monthly memory. Years, trips and seasons have not been priced on any reader.
- Batch mode against these numbers. The 50 % discount two of the routes publish is documented by
  the providers, not measured here.
