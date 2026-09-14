---
sidebar_position: 3
title: Readers
sidebar_label: "Readers: what was measured"
---

# Readers, measured on one month

The reader is the model the editor hands a period to. It reads the annotation lines, writes the
story, and asks for 800 px tiles of the pictures it cannot settle on paper. Any OpenAI-compatible
or Anthropic-compatible endpoint can go in `llm`.

This page is a measurement report. Ten cells were pointed at the same month; seven produced a cut
and three stopped. What follows is what each one did, not what anyone should do about it.

:::note What these numbers are
Measurements of particular models, served by particular providers, on one library, in September
2026. They are not an endorsement of any model or any shop, and they do not transfer: a provider
can change what a served model does without changing the name it serves it under. This run already
showed both halves of that. The same model id on two shops differed only in latency, while a
different model on the same shop refused every image it was sent.

Every cost is the provider's published list price times the tokens the run measured. No provider in
the comparison returns a price with a completion, so nothing here was ever charged to an account
and checked. Each shop is priced in the currency it publishes in and nothing converts between them.
:::

## The run

February 2024: 13,552 pictures in scope, 1,417 of them candidates, 15 kept. The same library, the
same month, the same banked facts seeded into every cell, one line of config moved per cell.
Selection is the reader stage only, with preparation already warm. Prices were read off the model
pages on 14 September 2026. The cells and the price block are in the
[setup matrix](../contribute/setup-matrix.md).

## The seven that produced a cut

| Model | Provider and route | Selection | Calls | Tokens in | Tokens out | List price times measured tokens | Overlap |
|---|---|---:|---:|---:|---:|---|---:|
| none, `rules` reader | no endpoint | 8 s | 0 | 0 | 0 | nothing | 25 % |
| `glm-5.3-flash` | Melious, OpenAI-compatible | 13 min 45 s | 156 | 368,198 | 43,280 | EUR 0.054 | 11 % |
| `Huihui-Qwen3.6-35B-A3B-abliterated-oQ4e-mtp` | local oMLX, OpenAI-compatible | 16 min 9 s | 151 | 331,993 | 33,091 | nothing | 30 % |
| `Qwen3-VL-30B-A3B-Instruct-4bit` | local oMLX, OpenAI-compatible | 19 min 3 s | 164 | 386,706 | 36,462 | nothing | 100 % |
| `gpt-5.6-luna` | OpenAI | 20 min 58 s | 168 | 376,015 | 77,173 | USD 0.168 | 11 % |
| `glm-5.3-flash` | z.ai, Anthropic-compatible | 25 min 16 s | 154 | 366,222 | 41,965 | no published price for this account | 20 % |
| `muse-glimmer` | Melious, OpenAI-compatible | 1 h 53 min | 156 | 367,273 | 511,579 | EUR 0.585 | 20 % |

Each of those kept 15 pictures and produced a 54 to 55 second film. Overlap is the share of
identical pictures against the `Qwen3-VL-30B` cut, which is the matrix's reference cell. It records
that two readers chose differently. It does not record which choice was better; nobody has graded
these cuts against each other.

"Calls" is the reader calls the editor's planner made. Counting POST requests in the log gives 182
to 201 instead, because that includes the reads before the planner starts. Both describe the same
run.

Three observations that fall out of the table:

- Input barely moves. Every reader that finished was handed 330,000 to 390,000 tokens over 150 to
  170 planner calls. Output moves by a factor of twelve, and output is what the price list bills at
  three to six times the input rate.
- The same model id on two shops came out at 13 min 45 s and 25 min 16 s, on token counts within
  1 % of each other.
- One cluster cell was also priced, on a different host and tier: `gemma-4-31b` on Melious, 112
  calls, 481,387 in, 315,505 out, EUR 0.143. That cell is the one described under HTTP 400 below.

### The widest gap in the table

Two of those Melious cells cut the same month from the same seeded facts, kept 15 pictures each,
made **the same 156 calls**, and were handed within 1 % of the same input:

| | `muse-glimmer` | `glm-5.3-flash` |
|---|---:|---:|
| Tokens in | 367,273 | 368,198 |
| Tokens out | **511,579** | **43,280** |
| List price times measured tokens | EUR 0.585 | EUR 0.054 |
| Selection | 1 h 53 min | 13 min 45 s |

The probes locate the difference. Over the three probe prompts `muse-glimmer` produced 20,193
output tokens against 2,441, and 8,681 of the 9,989 on one of them were inside a thinking block.
Both cells completed and both are in the table above.

## The three that stopped

### HTTP 400 for every image

One hosted model answered text, system prompts, `response_format` and `reasoning_effort` normally
and returned HTTP 400 for every image it was sent:

```text
code invalid_request_error, message 'The request was rejected as malformed.
Check the message format, tools schema, or response_format.'
```

46 of those in one run, all inside the picture-facts loop, with the text calls around them
returning 200. The reader sends 800 px tiles there. This is the one failure on the page that the
fixture month also caught: the same model kept 0 pictures there. On the cluster, at
`no_captions`, the same model did produce a cut, with no picture observations in it. A probe that
sends only text does not reach this at all.

### Prefill memory exhausted partway through

A dense 31B on a local oMLX server stopped at call 91 of its story-pick stage, 90 answers in:

```text
code prefill_memory_exceeded, message 'oMLX prefill memory guard rejected this prompt:
Prefill context too large for available memory'
```

That run made 151 calls in 63 minutes before failing; the reference reader completes the same month
in 201. On the same host and the same three probe prompts it produced 20.1 output tokens a second
against 81.1 for a sparse 35B, and 17.6 against 167.7 on an earlier probe. A dense model activates
every parameter per token where these sparse ones activate about a tenth.

### Timed out on two of three probe shapes

One hosted model, at the 300 s default:

```text
  episodes    -    -  0 in  0 out  300.0s  transport: TimeoutError
  period      200  stop  1515 in  1286 out  104.1s  ok (4 evidence rows)
  story-pick  -    -  0 in  0 out  300.0s  transport: TimeoutError
```

The same model had cleared all three shapes on the fixture month the day before, with the slowest
at 277.7 s. It passed by 22 seconds.

## What the fixture month did and did not show

The demo library is 133 files, 130 of them eligible. Its reader prompts measured 1,258 tokens a
call against February's 2,378, on the same model over both months: a ten-times-larger candidate
pool yields roughly twice the prompt, because paging absorbs the rest.

Two of the three stopped cells finished it, and so did the cell that spent 511,579 output tokens.
The prefill exhaustion, the timeout and the token volume were visible only on the real month. The
HTTP 400s were the exception: that model kept 0 pictures on the fixture month as well.

## What the software sends a reader

Facts about the pipeline, not properties any particular model was judged on:

- **Pictures go out as images.** The picture-facts stage posts 800 px JPEG tiles at quality 90. A
  model whose API cannot accept an image cannot do that stage. It is not a loud failure: each
  request comes back empty, is banked as a failure, and the edit finishes carrying
  `picture observations unavailable`.
- **Prompts run to roughly 6k tokens.** The story pick, the largest single prompt, measured 5,262
  tokens on one host and 5,776 on another. Requests are bounded before they go out: episode reads
  at 24,000 characters and 90 assets a page, story synthesis at 32,000, the period account at
  96,000 split into pages.
- **Answers are parsed against the stage's contract.** The app validates the JSON envelope itself
  and never asks the provider for a JSON mode, so a host without one is not disadvantaged. An answer
  the contract refuses costs a repair round on that call.

The three prompt shapes the matrix probes with are the period account, the episode read and the
story pick, in `scripts/reader_probe_prompts/`. `immich-memories preflight` reports whether a host
answers and what it lists at `/models`.

Provider dialects, reasoning switches and batch mode are on
[LLM titles and mood](../create/pipeline/llm-content-analysis.md#any-openai-compatible-api). What
leaves your network when you point at a provider is on
[Network & Privacy](./configuration/network-and-privacy.md).

## What was not measured

- Quality. This page is time, tokens and list price. The only reader whose output has been judged
  end to end is the local `Qwen3-VL-30B-A3B-Instruct-4bit` reference.
- Any actual bill.
- Anything but a monthly memory. Years, trips and seasons have not been run on any reader.
- Batch mode. The 50 % discount two of the routes publish is documented by the providers and was
  not exercised here.
- Repeat observations. Every row is one run.
