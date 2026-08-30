#!/usr/bin/env python3
"""Can Qwen3.5-9B-MLX-4bit take the ESCALATION tier from the 27B?

The pairwise head abstains on ~10% of same-picture pair verdicts (the
5-fold cross-fitted residual band computed by
`probe_pairhead_specialist_instruments.py`, banked in `residual-set.json`).
Today every one of those abstentions escalates to a 27B call
(`scottlowry/Qwen3.8-27B-oQ4e-mtp`). This probe asks whether a newly
downloaded 9B vision model served by the same local oMLX endpoint is a
credible drop-in for that tier.

Four steps:

  0. Vision smoke -- the pulled variant's id lacks "VL"; confirm it actually
     looks at the image before spending any further budget.
  1. Sample -- seed 42, stratified straight off `residual-set.json`'s banked
     rows: 120 residual pairs + 60 head-covered control pairs, plus a
     disjoint 8-pair anchor set for the repeat-stability steps.
  2. Harness -- the 180-pair sample through the exact pair-v3 request
     (`ask`/`_payload` from `probe_pairhead_small_vlm.py`, imported rather
     than retyped): verbatim prompt, one composite contact sheet, temp 0,
     the production parser.
  3. Stability anchors -- on the 8 fixed anchor pairs: 11x the 9B, 5x the
     27B (its own noise floor, measured fresh -- the residual band is
     defined as exactly where the teacher disagrees with itself), and the
     3x flipped-example control, judged against the 9B's own run-to-run
     baseline rather than a flat threshold (the "improved flip
     methodology" in docs/research/2026-08-30-small-vision-models-landscape.md
     #9, Probe A: a flip-induced delta only means something if it clears
     the model's own repeat noise).

Outputs qwen35-9b-pair-probe.json and qwen35-9b-pair-probe.md under the
matrix dir. Counts and metrics only -- no asset ids, no photo content.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import random
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_pairhead_small_vlm import (  # noqa: E402
    CONTROL_STRATA_PER_SIDE,
    IN_BAND_STRATA,
    Answer,
    SamplePair,
    _percentile,
    _preview_path,
    _stratified_draw,
    ask,
    endpoint,
    stratum_metrics,
)

from immich_memories.analysis.selection_selects import _PAIR_PROMPT  # noqa: E402
from immich_memories.analysis.visual_request_planner import VisionRequestLimits  # noqa: E402

DEFAULT_MATRIX_DIR = Path.home() / ".immich-memories-matrix" / "pairhead-2026-08-30"
SAMPLE_SEED = 42
RESIDUAL_TARGET = 120
CONTROL_TARGET = 60
ANCHOR_PAIRS = 8
NINE_B_MODEL = "Qwen3.5-9B-MLX-4bit"
TWENTYSEVEN_B_MODEL = "Qwen3.8-27B-oQ4e-mtp"
NINE_B_REPEATS = 11
TWENTYSEVEN_B_REPEATS = 5
FLIP_REPEATS = 3
# Owner's bar: these are within-moment frame choices, so a wrong verdict swaps
# a frame for a near-identical sibling. "95% working is better than 100%."
AGREEMENT_BAR = 0.95

_FLIPPED_PAIR_PROMPT = _PAIR_PROMPT.replace('"same":false', '"same":true')
assert _FLIPPED_PAIR_PROMPT != _PAIR_PROMPT, "flip must actually change the shown example"


def _within_matrix(path: Path) -> bool:
    matrix = (Path.home() / ".immich-memories-matrix").resolve()
    try:
        path.resolve().relative_to(matrix)
    except ValueError:
        return False
    return True


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix-dir", type=Path, default=DEFAULT_MATRIX_DIR)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--sample-only", action="store_true")
    parser.add_argument("--skip-main", action="store_true", help="skip the 180-pair STEP 2 run")
    parser.add_argument("--skip-anchors", action="store_true", help="skip the STEP 3 stability block")
    args = parser.parse_args()
    if not _within_matrix(args.matrix_dir):
        parser.error("--matrix-dir must be inside ~/.immich-memories-matrix")
    return args


# --- STEP 0: vision smoke -----------------------------------------------------


def vision_smoke(base_url: str, api_key: str, previews_dir: Path, timeout_seconds: int) -> dict[str, Any]:
    """One cheap call: does the pulled variant look at the image at all?

    Whether the description actually *matches* the photo needs a human eye on
    the source image -- that happened once, by hand, before this script was
    written, and the result is recorded below as `manual_grounding_check`.
    This call re-checks only the structural signals (HTTP status, non-empty
    content, no explicit refusal) so a re-run catches a server-side
    regression. The description text itself is never written to disk or
    returned to the caller: it describes a real personal photo.
    """
    image_path = sorted(previews_dir.glob("*.jpg"))[0]
    jpeg_b64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    payload = {
        "model": NINE_B_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image briefly."},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{jpeg_b64}",
                            "detail": "high",
                        },
                    },
                ],
            }
        ],
        "max_tokens": 50,
        "temperature": 0.0,
    }
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    started = time.monotonic()
    try:
        with httpx.Client(timeout=float(timeout_seconds), headers=headers) as client:
            response = client.post(f"{base_url}/chat/completions", json=payload)
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"].get("content") or ""
        refused = "cannot see" in content.lower() or "no image" in content.lower()
        return {
            "http_ok": True,
            "seconds": time.monotonic() - started,
            "content_chars": len(content),
            "looks_grounded_structurally": (not refused) and len(content.strip()) >= 20,
            "finish_reason": body["choices"][0].get("finish_reason"),
            "usage": body.get("usage"),
            "manual_grounding_check": (
                "passed 2026-08-30: model description matched the source image "
                "(subject, setting, and objects all correctly identified) on "
                "direct human comparison -- not text-only hallucination"
            ),
        }
    except Exception as exc:  # noqa: BLE001 - the smoke test must report, not crash
        return {
            "http_ok": False,
            "seconds": time.monotonic() - started,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }


# --- STEP 1: sampling ----------------------------------------------------------


def load_residual_set(matrix_dir: Path) -> dict[str, Any]:
    return json.loads((matrix_dir / "residual-set.json").read_text())


def build_sample(matrix_dir: Path) -> tuple[list[SamplePair], list[SamplePair], dict[str, Any]]:
    """Seed-42 stratified draw straight off `residual-set.json`'s banked rows.

    Reuses `_stratified_draw` from the small-VLM harness, so the sampling
    logic is identical to the abstention-band probe it was written for --
    only the candidate pool changes, from a freshly recomputed head to the
    banked 5-fold cross-fitted residual/control split.
    """
    data = load_residual_set(matrix_dir)
    residual_rows: list[dict[str, Any]] = data["residual"]
    control_rows: list[dict[str, Any]] = data["control"]
    previews = matrix_dir / "previews"

    def has_previews(row: dict[str, Any]) -> bool:
        return bool(_preview_path(previews, row["a"]) and _preview_path(previews, row["b"]))

    residual_usable = [i for i, row in enumerate(residual_rows) if has_previews(row)]
    control_usable = [i for i, row in enumerate(control_rows) if has_previews(row)]

    rng = random.Random(SAMPLE_SEED)
    residual_candidates = [(i, residual_rows[i]["p_head"]) for i in residual_usable]
    chosen_residual = _stratified_draw(residual_candidates, RESIDUAL_TARGET, IN_BAND_STRATA, rng)

    control_low = [
        (i, control_rows[i]["p_head"])
        for i in control_usable
        if control_rows[i]["head_call"] is False
    ]
    control_high = [
        (i, control_rows[i]["p_head"])
        for i in control_usable
        if control_rows[i]["head_call"] is True
    ]
    chosen_control: list[int] = []
    for side in (control_low, control_high):
        chosen_control.extend(
            _stratified_draw(side, CONTROL_TARGET // 2, CONTROL_STRATA_PER_SIDE, rng)
        )

    # Anchor pairs continue the same seeded stream, drawn only from residual
    # rows the main sample above did not already take -- the repeat-stability
    # block must not double-count a pair already scored once in STEP 2.
    already = set(chosen_residual)
    remaining_residual = [(i, residual_rows[i]["p_head"]) for i in residual_usable if i not in already]
    chosen_anchor = _stratified_draw(remaining_residual, ANCHOR_PAIRS, ANCHOR_PAIRS, rng)

    def to_pair(row: dict[str, Any], stratum: str) -> SamplePair:
        return SamplePair(
            a=row["a"],
            b=row["b"],
            teacher_same=bool(row["teacher_same"]),
            p_head=float(row["p_head"]),
            stratum=stratum,
        )

    sample = [to_pair(residual_rows[i], "residual") for i in sorted(chosen_residual)] + [
        to_pair(control_rows[i], "control") for i in sorted(chosen_control)
    ]
    anchors = [to_pair(residual_rows[i], "anchor") for i in sorted(chosen_anchor)]

    meta = {
        "seed": SAMPLE_SEED,
        "t_same": data["t_same"],
        "t_diff": data["t_diff"],
        "tau_far": data["tau_far"],
        "residual_total": len(residual_rows),
        "residual_with_previews": len(residual_usable),
        "control_total": len(control_rows),
        "control_with_previews": len(control_usable),
        "residual_sampled": len(chosen_residual),
        "control_sampled": len(chosen_control),
        "anchor_sampled": len(chosen_anchor),
        "source": "residual-set.json (5-fold cross-fitted OOF, probe_pairhead_specialist_instruments.py)",
    }
    return sample, anchors, meta


def sample_metrics(answers: list[Answer], t_same: float, t_diff: float) -> dict[str, Any]:
    by_stratum = {
        name: [answer for answer in answers if answer.stratum == name]
        for name in ("residual", "control")
    }
    return {
        "overall": stratum_metrics(answers, t_same, t_diff),
        **{
            name: stratum_metrics(rows, t_same, t_diff)
            for name, rows in by_stratum.items()
            if rows
        },
    }


# --- STEP 2 / 3: request runners -----------------------------------------------


def no_thinking_params() -> dict[str, Any]:
    """The `no_thinking_params` a `thinking=False` production call would send.

    `_ask_one_pair` builds its `VisualEditorialRequest` with the class
    default `thinking=False`. `llm_query._query_openai` then sends
    `config.no_thinking_params` whenever thinking is off -- omitting the
    field is not the same as disabling it, because a server whose chat
    template reasons by default (measured: `Qwen3.8-27B-oQ4e-mtp` on this
    endpoint) reasons anyway and burns the small `max_tokens` budget on
    hidden `reasoning_content`, truncating `content` to nothing parseable.
    `probe_pairhead_small_vlm._payload` never applied this -- inert for a
    model that does not reason unless asked (confirmed for the 9B), but a
    silent confound against one that does.
    """
    raw = yaml.safe_load((Path.home() / ".immich-memories" / "config.yaml").read_text())
    llm = dict(raw.get("llm") or {})
    llm.update((raw.get("advanced") or {}).get("llm") or {})
    return llm.get("no_thinking_params") or {"chat_template_kwargs": {"enable_thinking": False}}


def run_batch(
    model: str,
    pairs: list[SamplePair],
    matrix_dir: Path,
    concurrency: int,
    timeout_seconds: int,
    prompt_text: str = _PAIR_PROMPT,
    extra: dict[str, Any] | None = None,
) -> list[Answer]:
    base_url, api_key = endpoint()
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    limits = VisionRequestLimits()
    previews = matrix_dir / "previews"
    sheets = matrix_dir / "small-vlm-sheets"
    sheets.mkdir(parents=True, exist_ok=True)
    done = 0
    with httpx.Client(timeout=float(timeout_seconds), headers=headers) as client:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            answers = []
            for answer in pool.map(
                lambda pair: ask(
                    client, url, model, pair, previews, sheets, limits, prompt_text, extra
                ),
                pairs,
            ):
                answers.append(answer)
                done += 1
                if done % 20 == 0:
                    print(f"  {model}: {done}/{len(pairs)}", flush=True)
    return answers


def run_repeats(
    model: str,
    pairs: list[SamplePair],
    matrix_dir: Path,
    concurrency: int,
    timeout_seconds: int,
    repeats: int,
    prompt_text: str = _PAIR_PROMPT,
    extra: dict[str, Any] | None = None,
) -> list[list[Answer]]:
    """`repeats` independent calls per pair, regrouped back by pair after the pool drains."""
    base_url, api_key = endpoint()
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    limits = VisionRequestLimits()
    previews = matrix_dir / "previews"
    sheets = matrix_dir / "small-vlm-sheets"
    sheets.mkdir(parents=True, exist_ok=True)

    jobs = [pair for pair in pairs for _ in range(repeats)]
    done = 0
    with httpx.Client(timeout=float(timeout_seconds), headers=headers) as client:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            flat: list[Answer] = []
            for answer in pool.map(
                lambda pair: ask(
                    client, url, model, pair, previews, sheets, limits, prompt_text, extra
                ),
                jobs,
            ):
                flat.append(answer)
                done += 1
                if done % 10 == 0:
                    print(f"  {model} (x{repeats}): {done}/{len(jobs)}", flush=True)
    return [flat[i * repeats : (i + 1) * repeats] for i in range(len(pairs))]


# --- STEP 3: stability metrics --------------------------------------------------


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    phat = successes / n
    denom = 1 + z * z / n
    center = phat + z * z / (2 * n)
    spread = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))
    return ((center - spread) / denom, (center + spread) / denom)


def majority_of(verdicts: list[bool | None]) -> bool | None:
    parsed = [v for v in verdicts if v is not None]
    if not parsed:
        return None
    return Counter(parsed).most_common(1)[0][0]


@dataclass
class RepeatStability:
    model: str
    repeats: int
    pairs: int
    calls: int
    parsed: int
    unanimous_pairs: int
    per_call_agreement_with_majority: float | None
    flip_rate: float | None
    flip_rate_ci95: tuple[float, float] | None
    majority_verdicts: list[bool | None]
    median_seconds: float | None
    p90_seconds: float | None


def repeat_stability(model: str, groups: list[list[Answer]]) -> RepeatStability:
    repeats = len(groups[0]) if groups else 0
    all_answers = [answer for group in groups for answer in group]
    majorities = [majority_of([a.verdict for a in group]) for group in groups]
    agree_flags = [
        a.verdict == majorities[i]
        for i, group in enumerate(groups)
        for a in group
        if a.verdict is not None and majorities[i] is not None
    ]
    unanimous = sum(
        1
        for group, majority in zip(groups, majorities, strict=True)
        if majority is not None and all(a.verdict == majority for a in group if a.verdict is not None)
    )
    latencies = [a.seconds for a in all_answers if a.failure is None]
    agreement = (sum(agree_flags) / len(agree_flags)) if agree_flags else None
    disagreements = len(agree_flags) - sum(agree_flags)
    return RepeatStability(
        model=model,
        repeats=repeats,
        pairs=len(groups),
        calls=len(all_answers),
        parsed=sum(1 for a in all_answers if a.verdict is not None),
        unanimous_pairs=unanimous,
        per_call_agreement_with_majority=agreement,
        flip_rate=(1 - agreement) if agreement is not None else None,
        flip_rate_ci95=(
            _invert_ci(wilson_interval(len(agree_flags) - disagreements, len(agree_flags)))
            if agree_flags
            else None
        ),
        majority_verdicts=majorities,
        median_seconds=statistics.median(latencies) if latencies else None,
        p90_seconds=_percentile(latencies, 0.90) if latencies else None,
    )


def _invert_ci(agreement_ci: tuple[float, float] | None) -> tuple[float, float] | None:
    """Wilson CI on agreement, reported as the equivalent CI on the flip rate."""
    if agreement_ci is None:
        return None
    lo, hi = agreement_ci
    return (1 - hi, 1 - lo)


def flip_control(baseline: RepeatStability, flipped_groups: list[list[Answer]]) -> dict[str, Any]:
    """Flip-induced disagreement vs the model's own run-to-run noise floor.

    docs/research/2026-08-30-small-vision-models-landscape.md #9, Probe A: a
    flat "<5% of verdicts changed" bar is unmeasurable because the incumbent's
    own noise floor is already ~5%. Compare instead to THIS model's own
    baseline, computed on the identical anchor pairs.
    """
    majorities = baseline.majority_verdicts
    flipped_answers = [a for group in flipped_groups for a in group]
    disagree_flags = [
        a.verdict != majorities[i]
        for i, group in enumerate(flipped_groups)
        for a in group
        if a.verdict is not None and majorities[i] is not None
    ]
    n_disagree = len(disagree_flags)
    flip_disagreement = (sum(disagree_flags) / n_disagree) if n_disagree else None
    baseline_noise = baseline.flip_rate
    parsed_flipped = sum(1 for a in flipped_answers if a.verdict is not None)
    said_same_flipped = sum(1 for a in flipped_answers if a.verdict is True)
    return {
        "baseline_noise_floor": baseline_noise,
        "baseline_noise_floor_ci95": baseline.flip_rate_ci95,
        "flip_induced_disagreement": flip_disagreement,
        "flip_induced_disagreement_ci95": (
            wilson_interval(sum(disagree_flags), n_disagree) if n_disagree else None
        ),
        "delta": (
            flip_disagreement - baseline_noise
            if flip_disagreement is not None and baseline_noise is not None
            else None
        ),
        "delta_exceeds_noise_floor": (
            flip_disagreement > baseline_noise
            if flip_disagreement is not None and baseline_noise is not None
            else None
        ),
        "flipped_calls": len(flipped_answers),
        "flipped_parsed": parsed_flipped,
        "flipped_said_same_rate": (said_same_flipped / parsed_flipped) if parsed_flipped else None,
    }


# --- report ---------------------------------------------------------------------


def _cell(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def render_report(payload: dict[str, Any]) -> str:
    smoke = payload["vision_smoke"]
    meta = payload["sample"]
    lines = [
        "# Qwen3.5-9B-MLX-4bit as the pairhead ESCALATION tier",
        "",
        "Counts and metrics only. No asset ids appear anywhere in this file.",
        "",
        f"**Vision smoke**: {'passed' if smoke.get('looks_grounded_structurally') else 'FAILED'} "
        f"-- {smoke.get('manual_grounding_check', smoke.get('error', 'n/a'))}",
        "",
        f"**Sample**: seed {meta['seed']}, drawn from `residual-set.json` "
        f"({meta['residual_total']} residual / {meta['control_total']} control banked pairs) -- "
        f"{payload['stratum_counts']['residual']} residual + {payload['stratum_counts']['control']} "
        f"control + {meta['anchor_sampled']} disjoint anchor pairs.",
        "",
    ]
    main = payload.get("main_run", {}).get("metrics", {})
    if main:
        overall, residual, control = main.get("overall", {}), main.get("residual", {}), main.get("control", {})
        lines += [
            "## STEP 2 -- 180-pair single-shot run",
            "",
            "| stratum | pairs | parse rate | agreement w/ banked 27B | same-for-diff (dangerous) | "
            "diff-for-same | median s | p90 s |",
            "|---|---|---|---|---|---|---|---|",
            f"| residual | {residual.get('pairs', 0)} | {_cell(residual.get('parse_rate'))} | "
            f"{_cell(residual.get('agreement'))} | {residual.get('dangerous_same_for_different', 'n/a')} | "
            f"{residual.get('safe_different_for_same', 'n/a')} | {_cell(residual.get('median_seconds'), 1)} | "
            f"{_cell(residual.get('p90_seconds'), 1)} |",
            f"| control | {control.get('pairs', 0)} | {_cell(control.get('parse_rate'))} | "
            f"{_cell(control.get('agreement'))} | {control.get('dangerous_same_for_different', 'n/a')} | "
            f"{control.get('safe_different_for_same', 'n/a')} | {_cell(control.get('median_seconds'), 1)} | "
            f"{_cell(control.get('p90_seconds'), 1)} |",
            f"| overall | {overall.get('pairs', 0)} | {_cell(overall.get('parse_rate'))} | "
            f"{_cell(overall.get('agreement'))} | {overall.get('dangerous_same_for_different', 'n/a')} | "
            f"{overall.get('safe_different_for_same', 'n/a')} | {_cell(overall.get('median_seconds'), 1)} | "
            f"{_cell(overall.get('p90_seconds'), 1)} |",
            "",
            "The 27B's own repeats only agree with themselves ~95% of the time (banked), so "
            "\"agreement\" above is agreement with the incumbent, not ground truth.",
            "",
        ]
    anchors = payload.get("stability_anchors")
    if anchors:
        nine_b, big, flip = anchors["nine_b"], anchors["twentyseven_b"], anchors["example_flip_control"]
        lines += [
            "## STEP 3 -- stability anchors "
            f"({anchors['anchor_pairs']} fixed residual pairs)",
            "",
            "| model | repeats | calls | unanimous pairs | flip rate | median s | p90 s |",
            "|---|---|---|---|---|---|---|",
            f"| 9B | {nine_b['repeats']} | {nine_b['calls']} | {nine_b['unanimous_pairs']}/"
            f"{nine_b['pairs']} | {_cell(nine_b['flip_rate'])} | {_cell(nine_b['median_seconds'], 1)} | "
            f"{_cell(nine_b['p90_seconds'], 1)} |",
            f"| 27B | {big['repeats']} | {big['calls']} | {big['unanimous_pairs']}/{big['pairs']} | "
            f"{_cell(big['flip_rate'])} | {_cell(big['median_seconds'], 1)} | {_cell(big['p90_seconds'], 1)} |",
            "",
            f"**Example-flip control** (9B, {payload['request']['flip_repeats']}x on the same "
            "8 pairs, `\"same\":false` -> `\"same\":true`): flip-induced disagreement "
            f"{_cell(flip['flip_induced_disagreement'])} vs the 9B's own baseline noise floor "
            f"{_cell(flip['baseline_noise_floor'])} (delta {_cell(flip['delta'])}, "
            f"{'exceeds' if flip['delta_exceeds_noise_floor'] else 'does NOT clearly exceed'} noise floor).",
            "",
        ]
    lines += [
        "## Verdict",
        "",
        payload.get("verdict", "(fill in after the run)"),
        "",
    ]
    return "\n".join(lines) + "\n"


# --- main ------------------------------------------------------------------------


def _stability_payload(stability: RepeatStability) -> dict[str, Any]:
    return asdict(stability)


def main() -> int:
    args = _arguments()
    started = time.monotonic()

    base_url, api_key = endpoint()
    smoke = vision_smoke(base_url, api_key, args.matrix_dir / "previews", args.timeout_seconds)
    print(f"vision smoke: http_ok={smoke.get('http_ok')} "
          f"grounded={smoke.get('looks_grounded_structurally')}", flush=True)
    if not smoke.get("http_ok") or not smoke.get("looks_grounded_structurally"):
        print("VISION SMOKE FAILED -- stopping before spending further API budget", flush=True)
        payload = {
            "schema_version": "pairhead-residual-escalation-probe-v1",
            "privacy": "counts and metrics only; no asset ids appear anywhere in this file",
            "vision_smoke": smoke,
            "stopped_after_smoke_test": True,
        }
        (args.matrix_dir / "qwen35-9b-pair-probe.json").write_text(json.dumps(payload, indent=2) + "\n")
        return 1

    sample, anchors, sample_meta = build_sample(args.matrix_dir)
    counts = {name: sum(1 for p in sample if p.stratum == name) for name in ("residual", "control")}
    print(f"sample: {counts}, anchors: {len(anchors)}", flush=True)
    if args.sample_only:
        return 0

    extra = no_thinking_params()
    payload: dict[str, Any] = {
        "schema_version": "pairhead-residual-escalation-probe-v1",
        "privacy": "counts and metrics only; no asset ids appear anywhere in this file",
        "vision_smoke": smoke,
        "sample": sample_meta,
        "stratum_counts": counts,
        "models": {"escalation_candidate": NINE_B_MODEL, "incumbent": TWENTYSEVEN_B_MODEL},
        "agreement_bar": AGREEMENT_BAR,
        "request": {
            "prompt_source": "selection_selects._PAIR_PROMPT",
            "max_tokens": VisionRequestLimits().max_output_tokens,
            "temperature": 0.0,
            "image_detail": "high",
            "concurrency": args.concurrency,
            "timeout_seconds": args.timeout_seconds,
            "nine_b_repeats": NINE_B_REPEATS,
            "twentyseven_b_repeats": TWENTYSEVEN_B_REPEATS,
            "flip_repeats": FLIP_REPEATS,
            "extra_payload": extra,
            "extra_payload_why": (
                "matches _ask_one_pair's thinking=False -> llm_query.no_thinking_params; "
                "Qwen3.8-27B-oQ4e-mtp reasons by default on this server and without this its "
                "500-token budget goes entirely to hidden reasoning_content (measured: 39/40 "
                "unparseable before this was added)"
            ),
        },
    }

    if not args.skip_main:
        print(f"STEP 2: {NINE_B_MODEL} on {len(sample)} pairs", flush=True)
        answers = run_batch(
            NINE_B_MODEL, sample, args.matrix_dir, args.concurrency, args.timeout_seconds, extra=extra
        )
        payload["main_run"] = {
            "metrics": sample_metrics(answers, sample_meta["t_same"], sample_meta["t_diff"]),
            "failures": sorted({a.failure for a in answers if a.failure})[:5],
        }
        overall = payload["main_run"]["metrics"]["overall"]
        print(
            f"  parse {_cell(overall['parse_rate'])} agreement {_cell(overall['agreement'])} "
            f"median {_cell(overall['median_seconds'], 1)}s",
            flush=True,
        )

    if not args.skip_anchors:
        print(f"STEP 3a: {NINE_B_MODEL} x{NINE_B_REPEATS} on {len(anchors)} anchor pairs", flush=True)
        nine_b_groups = run_repeats(
            NINE_B_MODEL,
            anchors,
            args.matrix_dir,
            args.concurrency,
            args.timeout_seconds,
            NINE_B_REPEATS,
            extra=extra,
        )
        nine_b_stability = repeat_stability(NINE_B_MODEL, nine_b_groups)

        print(
            f"STEP 3b: {TWENTYSEVEN_B_MODEL} x{TWENTYSEVEN_B_REPEATS} on {len(anchors)} anchor pairs",
            flush=True,
        )
        big_groups = run_repeats(
            TWENTYSEVEN_B_MODEL,
            anchors,
            args.matrix_dir,
            args.concurrency,
            args.timeout_seconds,
            TWENTYSEVEN_B_REPEATS,
            extra=extra,
        )
        big_stability = repeat_stability(TWENTYSEVEN_B_MODEL, big_groups)

        print(
            f"STEP 3c: {NINE_B_MODEL} flipped-example x{FLIP_REPEATS} on {len(anchors)} anchor pairs",
            flush=True,
        )
        flipped_groups = run_repeats(
            NINE_B_MODEL,
            anchors,
            args.matrix_dir,
            args.concurrency,
            args.timeout_seconds,
            FLIP_REPEATS,
            prompt_text=_FLIPPED_PAIR_PROMPT,
            extra=extra,
        )
        flip = flip_control(nine_b_stability, flipped_groups)

        payload["stability_anchors"] = {
            "anchor_pairs": len(anchors),
            "nine_b": _stability_payload(nine_b_stability),
            "twentyseven_b": _stability_payload(big_stability),
            "example_flip_control": flip,
        }

    payload["wall_seconds"] = time.monotonic() - started
    (args.matrix_dir / "qwen35-9b-pair-probe.json").write_text(json.dumps(payload, indent=2) + "\n")
    (args.matrix_dir / "qwen35-9b-pair-probe.md").write_text(render_report(payload))
    print(f"done in {payload['wall_seconds']:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
