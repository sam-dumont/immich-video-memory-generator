#!/usr/bin/env python3
"""Can a small local VLM take the pairwise head's abstention band?

Lever 5 of the pairhead cascade. The trained head answers every adjacent-pair
"same picture?" question it is confident about; the band it abstains on
currently costs a 27B call each. This probe asks whether a 2-4B vision model
served by the same local oMLX endpoint is accurate enough to sit between them.

Faithfulness is the whole point, so the request is reproduced from the
production code rather than approximated:

  * `selection_selects._PAIR_PROMPT` verbatim (imported, never retyped)
  * `contact_sheets.build_contact_sheets(..., tile_px=SELECTS_TILE_PX)` -- ONE
    composite 800x400 sheet with two numbered 400px tiles, not two images
  * the verbatim preview JPEG bytes the atlas would have attached
  * `VisionRequestLimits()` defaults (max_tokens 500), temperature 0.0,
    `image_detail="high"`, the OpenAI content-parts shape from `llm_query`
  * the production parser: `final_json_object` -> schema_version gate -> bool

Every model is asked the IDENTICAL stratified sample (seed 42) so the
comparison is paired. Head probabilities are reconstructed from model.pkl /
pca.pkl exactly as the cascade probe fits them, and only the held-out TEST
split is sampled -- head probabilities on train/cal pairs are optimistic and
would flatter the control stratum.

Outputs small-vlm-probe.json and small-vlm-probe.md under the matrix dir. The
markdown carries counts and metrics only; no asset ids.
"""

from __future__ import annotations

import argparse
import base64
import json
import pickle
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_pairhead_cascade import (  # noqa: E402
    PairRecord,
    assign_splits,
    connected_components,
    load_pairs,
    pair_features,
)

from immich_memories.analysis.contact_sheets import build_contact_sheets  # noqa: E402
from immich_memories.analysis.selection_selects import (  # noqa: E402
    PAIR_SCHEMA_VERSION,
    SELECTS_TILE_PX,
    _PAIR_PROMPT,
)
from immich_memories.analysis.strict_json import final_json_object  # noqa: E402
from immich_memories.analysis.visual_request_planner import VisionRequestLimits  # noqa: E402

DEFAULT_MATRIX_DIR = Path.home() / ".immich-memories-matrix" / "pairhead-2026-08-30"
SHARED_THUMBNAIL_CACHE = Path.home() / ".immich-memories" / "cache" / "thumbnails"
SAMPLE_SEED = 42
IN_BAND_TARGET = 120
CONTROL_TARGET = 80
# Equal-count strata so the draw spreads across the observed probability range
# instead of piling into whichever region happens to be densest.
IN_BAND_STRATA = 8
CONTROL_STRATA_PER_SIDE = 2
FALLBACK_BAND_HIGH = 0.945
# Owner's bar: these are within-moment frame choices, so a wrong verdict swaps
# a frame for a near-identical sibling. "95% working is better than 100%."
AGREEMENT_BAR = 0.95


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
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--in-band", type=int, default=IN_BAND_TARGET)
    parser.add_argument("--control", type=int, default=CONTROL_TARGET)
    parser.add_argument("--sample-only", action="store_true")
    args = parser.parse_args()
    if not _within_matrix(args.matrix_dir):
        parser.error("--matrix-dir must be inside ~/.immich-memories-matrix")
    return args


# --- head reconstruction -----------------------------------------------------


@dataclass
class Head:
    """The banked pair head, loaded exactly as the live cascade loads it."""

    rows: np.ndarray
    index: dict[str, int]
    model: Any

    @classmethod
    def load(cls, matrix_dir: Path) -> Head:
        with (matrix_dir / "pca.pkl").open("rb") as handle:
            pca = pickle.load(handle)  # noqa: S301 - probe artifact written by our own script
        with (matrix_dir / "model.pkl").open("rb") as handle:
            model = pickle.load(handle)  # noqa: S301 - same
        embeddings = np.load(matrix_dir / "embeddings.npy")
        ids = json.loads((matrix_dir / "ids.json").read_text())
        return cls(
            rows=pca.transform(embeddings).astype(np.float32),
            index={asset_id: row for row, asset_id in enumerate(ids)},
            model=model,
        )

    def probabilities(self, pairs: list[PairRecord]) -> np.ndarray:
        left = np.array([self.index[pair.a] for pair in pairs])
        right = np.array([self.index[pair.b] for pair in pairs])
        features = pair_features(self.rows[left], self.rows[right]).astype(np.float32)
        return self.model.predict_proba(features)[:, 1]


def band_edges(matrix_dir: Path) -> tuple[float, float]:
    """The 98%-agreement cascade point: the band the head abstains on."""
    headline = json.loads((matrix_dir / "curve.json").read_text())["cascade_headline"]["98pct"]
    high = float(headline.get("threshold", FALLBACK_BAND_HIGH))
    return high, 1.0 - high


# --- sampling ----------------------------------------------------------------


@dataclass(frozen=True)
class SamplePair:
    a: str
    b: str
    teacher_same: bool
    p_head: float
    stratum: str


def _stratified_draw(
    candidates: list[tuple[int, float]], wanted: int, strata: int, rng: random.Random
) -> list[int]:
    """Draw `wanted` indices spread evenly across the probability range."""
    ordered = sorted(candidates, key=lambda item: item[1])
    if wanted >= len(ordered):
        return [index for index, _p in ordered]
    buckets: list[list[int]] = [[] for _ in range(strata)]
    for position, (index, _p) in enumerate(ordered):
        buckets[min(strata - 1, position * strata // len(ordered))].append(index)
    per_bucket = wanted // strata
    drawn: list[int] = []
    leftovers: list[int] = []
    for bucket in buckets:
        rng.shuffle(bucket)
        drawn.extend(bucket[:per_bucket])
        leftovers.extend(bucket[per_bucket:])
    rng.shuffle(leftovers)
    drawn.extend(leftovers[: wanted - len(drawn)])
    return drawn


def build_sample(
    matrix_dir: Path, in_band_target: int, control_target: int
) -> tuple[list[SamplePair], dict[str, Any]]:
    pairs = load_pairs(matrix_dir)
    head = Head.load(matrix_dir)
    known = [pair for pair in pairs if pair.a in head.index and pair.b in head.index]

    component_of = connected_components(known, head.index)
    split_of = assign_splits(known, head.index, component_of)
    test = [pair for pair, split in zip(known, split_of, strict=True) if split == "test"]

    probabilities = head.probabilities(test)
    high, low = band_edges(matrix_dir)

    previews = matrix_dir / "previews"
    usable = [
        (index, float(p))
        for index, p in enumerate(probabilities)
        if _preview_path(previews, test[index].a) and _preview_path(previews, test[index].b)
    ]
    in_band = [(index, p) for index, p in usable if low < p < high]
    control_low = [(index, p) for index, p in usable if p <= low]
    control_high = [(index, p) for index, p in usable if p >= high]

    rng = random.Random(SAMPLE_SEED)
    chosen: list[tuple[int, str]] = [
        (index, "in_band")
        for index in _stratified_draw(in_band, in_band_target, IN_BAND_STRATA, rng)
    ]
    for side in (control_low, control_high):
        chosen.extend(
            (index, "control")
            for index in _stratified_draw(
                side, control_target // 2, CONTROL_STRATA_PER_SIDE, rng
            )
        )

    sample = [
        SamplePair(
            a=test[index].a,
            b=test[index].b,
            teacher_same=bool(test[index].same),
            p_head=float(probabilities[index]),
            stratum=stratum,
        )
        for index, stratum in sorted(chosen)
    ]
    meta = {
        "band_high": high,
        "band_low": low,
        "test_pairs": len(test),
        "test_pairs_with_previews": len(usable),
        "test_in_band": len(in_band),
        "test_control": len(control_low) + len(control_high),
        "seed": SAMPLE_SEED,
    }
    return sample, meta


def _preview_path(previews: Path, asset_id: str) -> Path | None:
    direct = previews / f"{asset_id}.jpg"
    if direct.is_file():
        return direct
    shared = SHARED_THUMBNAIL_CACHE / asset_id[:2] / f"{asset_id}_preview.jpg"
    return shared if shared.is_file() else None


# --- the request -------------------------------------------------------------


@dataclass(frozen=True)
class _Tile:
    """The two attributes `build_contact_sheets` reads off an atlas tile."""

    entity_id: str
    jpeg_bytes: bytes


def sheet_for(pair: SamplePair, previews: Path, output_dir: Path) -> bytes:
    """The exact composite the selects pass would attach for arrangement `ab`."""
    tiles = tuple(
        _Tile(asset_id, _preview_path(previews, asset_id).read_bytes())  # type: ignore[union-attr]
        for asset_id in (pair.a, pair.b)
    )
    page = build_contact_sheets(
        tiles,
        scope_id=f"smallvlm-{pair.a[:8]}-{pair.b[:8]}-ab",
        output_dir=output_dir,
        tile_px=SELECTS_TILE_PX,
    )[0]
    return page.jpeg_bytes


def endpoint() -> tuple[str, str]:
    raw = yaml.safe_load((Path.home() / ".immich-memories" / "config.yaml").read_text())
    llm = dict(raw.get("llm") or {})
    llm.update((raw.get("advanced") or {}).get("llm") or {})
    return llm["base_url"].rstrip("/"), llm.get("api_key", "")


def _payload(
    model: str,
    jpeg: bytes,
    limits: VisionRequestLimits,
    prompt_text: str = _PAIR_PROMPT,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The body `llm_query._query_openai` builds for one visual editorial call.

    `prompt_text` defaults to the verbatim production prompt; callers running
    the example-flip control pass the one-token-changed variant instead.

    `extra` is merged in last, matching how `_query_openai` merges
    `config.no_thinking_params` -- `_ask_one_pair` requests `thinking=False`,
    and on a server whose chat template reasons unless told not to, omitting
    that field is not the same as disabling it: the call reasons anyway at
    this small `max_tokens` and truncates mid-thought. Callers pointed at
    such a model must pass the equivalent of `no_thinking_params` here.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64,"
                            + base64.b64encode(jpeg).decode("utf-8"),
                            "detail": "high",
                        },
                    },
                ],
            }
        ],
        "max_tokens": limits.max_output_tokens,
        "temperature": 0.0,
    }
    if extra:
        payload.update(extra)
    return payload


# The `reason` in the shape shown to the model. A model that returns this
# string back is copying the example, not looking: on this pass the written
# reason is measurably part of how the verdict is arrived at, so echoing it
# says the verdict was not.
_PLACEHOLDER_REASON = "what makes them one or two"


def parse_verdict(raw: str) -> tuple[bool | None, int, bool]:
    """`_ask_one_pair`'s rules: complete object, right schema, bool `same`.

    Also returns the reason's length and whether it is the shown placeholder --
    never the reason text itself, which describes private photographs.
    """
    if raw and "</think>" in raw:
        raw = raw.rsplit("</think>", 1)[1].lstrip()
    payload = final_json_object(raw) or {}
    reason = payload.get("reason")
    reason_chars = len(reason) if isinstance(reason, str) else 0
    echoed = isinstance(reason, str) and reason.strip().lower() == _PLACEHOLDER_REASON
    if payload.get("schema_version") != PAIR_SCHEMA_VERSION:
        return None, reason_chars, echoed
    same = payload.get("same")
    return (same if isinstance(same, bool) else None), reason_chars, echoed


@dataclass
class Answer:
    stratum: str
    teacher_same: bool
    p_head: float
    verdict: bool | None
    seconds: float
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    failure: str | None
    reason_chars: int = 0
    echoed_placeholder: bool = False


def ask(
    client: httpx.Client,
    url: str,
    model: str,
    pair: SamplePair,
    previews: Path,
    sheets: Path,
    limits: VisionRequestLimits,
    prompt_text: str = _PAIR_PROMPT,
    extra: dict[str, Any] | None = None,
) -> Answer:
    started = time.monotonic()
    try:
        jpeg = sheet_for(pair, previews, sheets)
        response = client.post(url, json=_payload(model, jpeg, limits, prompt_text, extra))
        response.raise_for_status()
        body = response.json()
        choice = body["choices"][0]
        message = choice["message"]
        content = message.get("content") or ""
        usage = body.get("usage") or {}
        verdict, reason_chars, echoed = parse_verdict(content)
        return Answer(
            stratum=pair.stratum,
            teacher_same=pair.teacher_same,
            p_head=pair.p_head,
            verdict=verdict,
            seconds=time.monotonic() - started,
            finish_reason=choice.get("finish_reason"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            failure=None,
            reason_chars=reason_chars,
            echoed_placeholder=echoed,
        )
    except Exception as exc:  # noqa: BLE001 - one dead call must not erase the run
        return Answer(
            stratum=pair.stratum,
            teacher_same=pair.teacher_same,
            p_head=pair.p_head,
            verdict=None,
            seconds=time.monotonic() - started,
            finish_reason=None,
            prompt_tokens=None,
            completion_tokens=None,
            failure=f"{type(exc).__name__}: {str(exc)[:200]}",
        )


def run_model(
    model: str, sample: list[SamplePair], matrix_dir: Path, args: argparse.Namespace
) -> list[Answer]:
    base_url, api_key = endpoint()
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    limits = VisionRequestLimits()
    previews = matrix_dir / "previews"
    sheets = matrix_dir / "small-vlm-sheets"
    sheets.mkdir(parents=True, exist_ok=True)
    done = 0
    with httpx.Client(timeout=float(args.timeout_seconds), headers=headers) as client:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            answers = []
            for answer in pool.map(
                lambda pair: ask(client, url, model, pair, previews, sheets, limits), sample
            ):
                answers.append(answer)
                done += 1
                if done % 20 == 0:
                    print(f"  {model}: {done}/{len(sample)}", flush=True)
    return answers


# --- metrics -----------------------------------------------------------------


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def stratum_metrics(answers: list[Answer], band_high: float, band_low: float) -> dict[str, Any]:
    total = len(answers)
    parsed = [answer for answer in answers if answer.verdict is not None]
    agree = [answer for answer in parsed if answer.verdict == answer.teacher_same]
    dangerous = [
        answer for answer in parsed if answer.verdict is True and answer.teacher_same is False
    ]
    safe = [answer for answer in parsed if answer.verdict is False and answer.teacher_same is True]
    latencies = [answer.seconds for answer in answers if answer.failure is None]
    head_calls = [
        answer for answer in answers if answer.p_head >= band_high or answer.p_head <= band_low
    ]
    head_agree = [
        answer for answer in head_calls if (answer.p_head >= 0.5) == answer.teacher_same
    ]
    completions = [
        answer.completion_tokens for answer in answers if answer.completion_tokens is not None
    ]
    prompts = [answer.prompt_tokens for answer in answers if answer.prompt_tokens is not None]
    return {
        "pairs": total,
        "parsed": len(parsed),
        "parse_rate": len(parsed) / total if total else None,
        "transport_failures": sum(1 for answer in answers if answer.failure is not None),
        "truncated": sum(1 for answer in answers if answer.finish_reason == "length"),
        "agreement": len(agree) / len(parsed) if parsed else None,
        "agreement_over_all": len(agree) / total if total else None,
        "dangerous_same_for_different": len(dangerous),
        "dangerous_rate": len(dangerous) / len(parsed) if parsed else None,
        "safe_different_for_same": len(safe),
        "safe_rate": len(safe) / len(parsed) if parsed else None,
        "teacher_same_share": (
            sum(1 for answer in answers if answer.teacher_same) / total if total else None
        ),
        "head_agreement": len(head_agree) / len(head_calls) if head_calls else None,
        "head_pairs_scored": len(head_calls),
        "median_seconds": statistics.median(latencies) if latencies else None,
        "p90_seconds": _percentile(latencies, 0.90),
        "median_completion_tokens": statistics.median(completions) if completions else None,
        "median_prompt_tokens": statistics.median(prompts) if prompts else None,
        "echoed_placeholder_reason": sum(1 for answer in answers if answer.echoed_placeholder),
        "echoed_rate": (
            sum(1 for answer in answers if answer.echoed_placeholder) / len(parsed)
            if parsed
            else None
        ),
        "median_reason_chars": (
            statistics.median([answer.reason_chars for answer in parsed]) if parsed else None
        ),
    }


def model_metrics(answers: list[Answer], band_high: float, band_low: float) -> dict[str, Any]:
    by_stratum = {
        name: [answer for answer in answers if answer.stratum == name]
        for name in ("in_band", "control")
    }
    return {
        "overall": stratum_metrics(answers, band_high, band_low),
        **{
            name: stratum_metrics(rows, band_high, band_low)
            for name, rows in by_stratum.items()
            if rows
        },
    }


def _cell(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def render_report(payload: dict[str, Any]) -> str:
    meta = payload["sample"]
    lines = [
        "# Small-VLM Middle Tier -- Pairwise-Head Abstention Band",
        "",
        "Counts and metrics only. No asset ids appear anywhere in this file.",
        "",
        "## Question",
        "",
        "The head abstains on "
        f"{meta['band_low']:.3f} < p < {meta['band_high']:.3f}; every abstention currently costs "
        "one 27B call. Can a 2-4B vision model, served by the same local endpoint, take that "
        "band instead?",
        "",
        "## Request fidelity",
        "",
        "- Prompt: `selection_selects._PAIR_PROMPT`, imported verbatim",
        f"- Evidence: ONE composite contact sheet, two numbered {SELECTS_TILE_PX}px tiles "
        "(`build_contact_sheets`), not two separate images",
        "- Attachment: the verbatim preview JPEG bytes the visual atlas would have used",
        f"- Limits: max_tokens {VisionRequestLimits().max_output_tokens}, temperature 0.0, "
        'image_detail "high", arrangement `ab` only',
        "- Parser: `final_json_object` -> `schema_version == "
        f"{PAIR_SCHEMA_VERSION}` -> boolean `same`",
        "",
        "## Sample",
        "",
        f"- Drawn from the held-out TEST split only ({meta['test_pairs']} pairs, "
        f"{meta['test_pairs_with_previews']} with local previews), seed {meta['seed']}",
        f"- In band: {payload['stratum_counts']['in_band']} of {meta['test_in_band']} available, "
        f"stratified across the probability range in {IN_BAND_STRATA} equal-count strata",
        f"- Control (head-confident): {payload['stratum_counts']['control']} of "
        f"{meta['test_control']} available, half from each side",
        "- Every model was asked the identical sample, so the comparison is paired.",
        "",
        "## Results",
        "",
        "| model | parse rate | in-band agreement | dangerous rate | control agreement | "
        "head agreement (control) | median s | p90 s |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, result in payload["models"].items():
        in_band = result["metrics"].get("in_band", {})
        control = result["metrics"].get("control", {})
        lines.append(
            f"| {name} | {_cell(result['metrics']['overall']['parse_rate'])} "
            f"| {_cell(in_band.get('agreement'))} "
            f"| {_cell(in_band.get('dangerous_rate'))} "
            f"| {_cell(control.get('agreement'))} "
            f"| {_cell(control.get('head_agreement'))} "
            f"| {_cell(result['metrics']['overall']['median_seconds'], 1)} "
            f"| {_cell(result['metrics']['overall']['p90_seconds'], 1)} |"
        )
    lines += [
        "",
        f"Bar: {AGREEMENT_BAR:.0%} in-band agreement. These are within-moment frame choices -- a "
        "wrong verdict swaps a frame for a near-identical sibling, which is invisible in the "
        "finished souvenir. The dangerous direction (model says same, teacher says different) is "
        "the one that removes a picture, so it is reported separately.",
        "",
        "The teacher's own repeats only agree with themselves ~95% of the time, so no agreement "
        "number here can be read as accuracy against ground truth; it is agreement with the "
        "incumbent.",
        "",
        "## Per-model detail",
        "",
    ]
    for name, result in payload["models"].items():
        lines += [f"### {name}", ""]
        for stratum in ("in_band", "control", "overall"):
            metrics = result["metrics"].get(stratum)
            if not metrics:
                continue
            lines.append(
                f"- **{stratum}** ({metrics['pairs']} pairs): parsed {metrics['parsed']}, "
                f"agreement {_cell(metrics['agreement'])}, "
                f"same-for-different {metrics['dangerous_same_for_different']}, "
                f"different-for-same {metrics['safe_different_for_same']}, "
                f"transport failures {metrics['transport_failures']}, "
                f"truncated {metrics['truncated']}, "
                f"median {_cell(metrics['median_seconds'], 1)}s / "
                f"p90 {_cell(metrics['p90_seconds'], 1)}s, "
                f"median completion tokens {_cell(metrics['median_completion_tokens'], 0)}, "
                f"placeholder-reason echoes {metrics['echoed_placeholder_reason']}, "
                f"median reason chars {_cell(metrics['median_reason_chars'], 0)}"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = _arguments()
    started = time.monotonic()
    sample, meta = build_sample(args.matrix_dir, args.in_band, args.control)
    counts = {
        name: sum(1 for pair in sample if pair.stratum == name) for name in ("in_band", "control")
    }
    print(f"sample: {counts} (band {meta['band_low']:.3f}..{meta['band_high']:.3f})", flush=True)
    if args.sample_only:
        return 0

    models: dict[str, Any] = {}
    for model in args.models:
        print(f"asking {model} ({len(sample)} pairs)", flush=True)
        model_started = time.monotonic()
        answers = run_model(model, sample, args.matrix_dir, args)
        models[model] = {
            "wall_seconds": time.monotonic() - model_started,
            "metrics": model_metrics(answers, meta["band_high"], meta["band_low"]),
            "failures": sorted({answer.failure for answer in answers if answer.failure})[:5],
        }
        overall = models[model]["metrics"]["overall"]
        print(
            f"  parse {_cell(overall['parse_rate'])} | "
            f"agreement {_cell(overall['agreement'])} | "
            f"median {_cell(overall['median_seconds'], 1)}s",
            flush=True,
        )

    payload = {
        "schema_version": "pairhead-small-vlm-probe-v1",
        "privacy": "counts and metrics only; the markdown carries no asset ids",
        "sample": meta,
        "stratum_counts": counts,
        "agreement_bar": AGREEMENT_BAR,
        "request": {
            "prompt": _PAIR_PROMPT,
            "prompt_source": "selection_selects._PAIR_PROMPT",
            "schema_version": PAIR_SCHEMA_VERSION,
            "tile_px": SELECTS_TILE_PX,
            "images_per_request": 1,
            "composite_sheet": True,
            "max_tokens": VisionRequestLimits().max_output_tokens,
            "temperature": 0.0,
            "image_detail": "high",
            "arrangement": "ab",
            "concurrency": args.concurrency,
            "timeout_seconds": args.timeout_seconds,
        },
        "models": models,
        "wall_seconds": time.monotonic() - started,
    }
    (args.matrix_dir / "small-vlm-probe.json").write_text(json.dumps(payload, indent=2) + "\n")
    (args.matrix_dir / "small-vlm-probe.md").write_text(render_report(payload))
    print(f"done in {payload['wall_seconds']:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
