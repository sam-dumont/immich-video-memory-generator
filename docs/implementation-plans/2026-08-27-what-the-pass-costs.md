# What the Pass Costs

> Measured 2026-08-27 against the real library and the production path, on the
> owner's local single-stream endpoint at temperature 0. Every number comes from
> banked answers or timed live calls, not from a model of the model.

Stage B asks a question per pair of neighbours, in two arrangements. On the
dense month that gated at 1312 calls and roughly 35 minutes of wall clock — for
one month, uncached. This page records where that time actually goes, because
the two obvious answers are both wrong.

---

## 1. The endpoint is local, so concurrency is not the lever

One model, one stream. Parallelising the gateway would queue, not overlap. That
removes the usual first answer and makes every remaining option a question of
either **fewer calls** or **cheaper calls**.

## 2. Pixels are not the bill

| tile px | median call | agrees with the 400px answer |
|---|---|---|
| 400 | 1.19s | 30/30 |
| 300 | 1.16s | 29/30 |
| 250 | 1.01s | 29/30 |
| 200 | 1.06s | 28/30 |

Quartering the pixel area moved the median 11% and started moving the answer.
The vision encoder resizes the sheet to its own grid, so the tile size the pass
declares changes what the model can *see* without changing what it *costs*.

**400px stays.** It was chosen on a fidelity measurement and there is no cost
argument against it.

## 3. The written reason is half the runtime

| variant | median call | characters written | agrees with the banked answer |
|---|---|---|---|
| verdict + reason, 4000 cap | 1.06s | 484 | 30/30 |
| **verdict only, 4000 cap** | **0.51s** | **49** | 29/30 |
| verdict only, 64 cap | 0.49s | 49 | 18/18 — **12 of 30 calls failed** |

The prose is 484 of 533 characters and half the wall clock. Dropping it halves
the call and does not move the verdict.

The third row is a trap of the kind already catalogued here: it reads as the
fastest variant because its failures left the sample. A 64-token cap truncates
mid-JSON and the answer never arrives. **The cap stays at 4000**; generation
stops on its own after ~49 characters.

### What replaces the reason

Murch's rule is real — "avoid writing 'NG' without saying why… what is bad when
you first see it may be exactly what you want two months later" — and it is met
by evidence rather than prose. Every pair records its exact sheet hash, both
arrangements' verdicts and the run it built, so the decision is reopened by
looking at the two tiles that caused it.

That is the stronger record *here* specifically, because of what this project
already measured: every failed question shape returned fluent, specific,
grounded-sounding reasons for answers that were following tile position. A
stored sentence from this model is not evidence that the model looked.

## 4. Short-circuiting the second arrangement

Only the intersection absorbs, so `forward and backward` cannot become true once
`forward` is false. The second arrangement of a pair already called different
changes no outcome.

Measured on the dense month: **121 of 1312 calls, 9.2%**. Exact, not estimated —
the pair is kept whole either way.

## 5. What the two arrangements are worth

| | dense month |
|---|---|
| pairs | 656 |
| swap agreement | **616/656 = 0.939** |
| both arrangements said same | 510 |
| first said different | 121 |

The 40 disagreeing pairs are kept, which is the point. Dropping the second
arrangement would save a further 45% of calls and absorb those 40 on a single
arrangement's word — pushing the dense month's survival under the 25% floor and
removing the only check that the answer is not order-sensitive. Not taken.

## 6. Where the dense month lands

Model time only; the rest of a run is pipeline overhead shared with Pass 0.

| | calls | model time |
|---|---|---|
| as first gated | 1312 | 23 min |
| \+ short-circuit | 1191 | 21 min |
| \+ verdict only | 1191 | **10 min** |

Both changes shipped. The cost of a month is now roughly `2 × (frames −
moments)` calls at about half a second each, minus the pairs settled by one
call.

## 7. Probes

| probe | asks | calls |
|---|---|---|
| `probe_cost.py` A | exact per-pair arrangements, from the bank | 0 |
| `probe_cost.py` B | does the answer survive a smaller tile | 120 |
| `probe_tokens.py` | is the written reason the cost | 90 |
