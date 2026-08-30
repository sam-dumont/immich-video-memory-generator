#!/usr/bin/env python3
"""Can Huihui-Qwen3.6-35B-A3B-abliterated-oQ4e-mtp take the ESCALATION tier?

Same question as `probe_pairhead_residual_escalation.py` (which measured
Qwen3.5-9B-MLX-4bit against the 27B incumbent), asked of a third candidate: a
35B MoE with ~3B active parameters, served by the same local oMLX endpoint.
It is a community "abliterated" variant, so quality is measured, not assumed.

This script is a thin sibling, not a fork: the sample, request, parser and
scoring all come from `probe_pairhead_small_vlm` and
`probe_pairhead_residual_escalation` unchanged, so the 180-pair draw (120
residual + 60 control, seed 42) and the 8-pair anchor set are byte-identical
to the 9B run -- `build_sample` is a pure function of `residual-set.json` and
the seed, independent of which candidate model is under test.

Two differences from the 9B script:

  * 5 repeats on the anchor block, not 11 (matching the 27B's own repeat
    count, since this is a feasibility read rather than a production-swap
    qualification).
  * The 27B's own anchor noise floor is NOT re-measured live -- it is read
    back from the banked `qwen35-9b-pair-probe.json` (`stability_anchors.
    twentyseven_b`), since the anchor draw is identical and re-calling the
    27B would only add load-swap thrash on the same local endpoint for data
    already on disk.

Outputs qwen36-35ba3b-pair-probe.json and .md under the matrix dir. Counts
and metrics only -- no asset ids, no photo content.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_pairhead_residual_escalation import (  # noqa: E402
    _FLIPPED_PAIR_PROMPT,
    AGREEMENT_BAR,
    FLIP_REPEATS,
    _cell,
    build_sample,
    flip_control,
    repeat_stability,
    run_batch,
    run_repeats,
    sample_metrics,
)
from probe_pairhead_small_vlm import endpoint  # noqa: E402

DEFAULT_MATRIX_DIR = Path.home() / ".immich-memories-matrix" / "pairhead-2026-08-30"
BANKED_9B_PROBE_NAME = "qwen35-9b-pair-probe.json"
CANDIDATE_MODEL = "Huihui-Qwen3.6-35B-A3B-abliterated-oQ4e-mtp"
INCUMBENT_27B = "Qwen3.8-27B-oQ4e-mtp"
CANDIDATE_REPEATS = 5
SMOKE_PROMPT = "Describe this image briefly."
SMOKE_MAX_TOKENS = 60


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
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--sample-only", action="store_true")
    parser.add_argument("--smoke-only", action="store_true", help="run only the vision-smoke gate")
    parser.add_argument(
        "--skip-main", action="store_true", help="skip the 180-pair single-shot run"
    )
    parser.add_argument(
        "--skip-anchors", action="store_true", help="skip the stability-anchor block"
    )
    args = parser.parse_args()
    if not _within_matrix(args.matrix_dir):
        parser.error("--matrix-dir must be inside ~/.immich-memories-matrix")
    return args


def vision_smoke(
    base_url: str, api_key: str, previews_dir: Path, timeout_seconds: int
) -> dict[str, Any]:
    """One cheap call: does the candidate look at the image at all?

    Structural signals only (HTTP status, non-empty content, no explicit
    refusal). The description text itself is never written to disk or
    returned to the caller -- it describes a real personal photo. Whether the
    description actually matches the photo needs a human eye on the source
    image; that happens once, by hand, alongside this call.
    """
    image_path = sorted(previews_dir.glob("*.jpg"))[0]
    jpeg_b64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    payload = {
        "model": CANDIDATE_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": SMOKE_PROMPT},
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
        "max_tokens": SMOKE_MAX_TOKENS,
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
            "image_path_used": image_path.name,
        }
    except Exception as exc:  # noqa: BLE001 - the smoke test must report, not crash
        return {
            "http_ok": False,
            "seconds": time.monotonic() - started,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }


def load_banked_27b_anchor_stability(matrix_dir: Path) -> dict[str, Any] | None:
    path = matrix_dir / BANKED_9B_PROBE_NAME
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    return payload.get("stability_anchors", {}).get("twentyseven_b")


def _cross_check_sample_meta(matrix_dir: Path, meta: dict[str, Any]) -> str | None:
    """Compare this run's sample meta against the banked 9B run's, if present.

    `build_sample` is a pure function of `residual-set.json` + the seed, so if
    both runs saw the same banked file the counts must match exactly. A
    mismatch means the underlying residual-set.json changed between runs.
    """
    path = matrix_dir / BANKED_9B_PROBE_NAME
    if not path.exists():
        return None
    banked_meta = json.loads(path.read_text()).get("sample", {})
    fields = (
        "residual_total",
        "control_total",
        "residual_sampled",
        "control_sampled",
        "anchor_sampled",
        "t_same",
        "t_diff",
        "tau_far",
    )
    mismatches = {
        f: (banked_meta.get(f), meta.get(f)) for f in fields if banked_meta.get(f) != meta.get(f)
    }
    if mismatches:
        return f"sample meta DIFFERS from banked 9B run: {mismatches}"
    return "sample meta matches the banked 9B run exactly (identical draw confirmed)"


def render_report(payload: dict[str, Any]) -> str:
    smoke = payload["vision_smoke"]
    meta = payload["sample"]
    lines = [
        "# Huihui-Qwen3.6-35B-A3B-abliterated-oQ4e-mtp as the pairhead ESCALATION tier",
        "",
        "Counts and metrics only. No asset ids appear anywhere in this file.",
        "",
        f"**Vision smoke**: {'passed' if smoke.get('looks_grounded_structurally') else 'FAILED'}",
        "",
        f"**Sample cross-check**: {payload.get('sample_cross_check', 'n/a')}",
        "",
        f"**Sample**: seed {meta['seed']}, drawn from `residual-set.json` "
        f"({meta['residual_total']} residual / {meta['control_total']} control banked pairs) -- "
        f"{payload['stratum_counts']['residual']} residual + {payload['stratum_counts']['control']} "
        f"control + {meta['anchor_sampled']} disjoint anchor pairs.",
        "",
    ]
    main = payload.get("main_run", {}).get("metrics", {})
    if main:
        overall, residual, control = (
            main.get("overall", {}),
            main.get("residual", {}),
            main.get("control", {}),
        )
        lines += [
            "## STEP 2 -- 180-pair single-shot run",
            "",
            "| stratum | pairs | parse rate | agreement w/ banked 27B | same-for-diff (dangerous) | "
            "diff-for-same | median s | p90 s |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for name, row in (("residual", residual), ("control", control), ("overall", overall)):
            lines.append(
                f"| {name} | {row.get('pairs', 0)} | {_cell(row.get('parse_rate'))} | "
                f"{_cell(row.get('agreement'))} | {row.get('dangerous_same_for_different', 'n/a')} | "
                f"{row.get('safe_different_for_same', 'n/a')} | {_cell(row.get('median_seconds'), 1)} | "
                f"{_cell(row.get('p90_seconds'), 1)} |"
            )
        lines += [
            "",
            '"Agreement" is agreement with the banked 27B-derived teacher label, not ground truth.',
            "",
        ]
    anchors = payload.get("stability_anchors")
    if anchors:
        candidate, flip = anchors["candidate"], anchors["example_flip_control"]
        banked_27b = anchors.get("banked_twentyseven_b")
        lines += [
            f"## STEP 3 -- stability anchors ({anchors['anchor_pairs']} fixed residual pairs)",
            "",
            "| model | repeats | calls | unanimous pairs | flip rate | median s | p90 s |",
            "|---|---|---|---|---|---|---|",
            f"| 35B-A3B | {candidate['repeats']} | {candidate['calls']} | "
            f"{candidate['unanimous_pairs']}/{candidate['pairs']} | {_cell(candidate['flip_rate'])} | "
            f"{_cell(candidate['median_seconds'], 1)} | {_cell(candidate['p90_seconds'], 1)} |",
        ]
        if banked_27b:
            lines.append(
                f"| 27B (banked, from {BANKED_9B_PROBE_NAME}) | {banked_27b['repeats']} | "
                f"{banked_27b['calls']} | {banked_27b['unanimous_pairs']}/{banked_27b['pairs']} | "
                f"{_cell(banked_27b['flip_rate'])} | {_cell(banked_27b['median_seconds'], 1)} | "
                f"{_cell(banked_27b['p90_seconds'], 1)} |"
            )
        lines += [
            "",
            f"**Example-flip control** (35B-A3B, {payload['request']['flip_repeats']}x on the same "
            '8 pairs, `"same":false` -> `"same":true`): flip-induced disagreement '
            f"{_cell(flip['flip_induced_disagreement'])} vs the model's own baseline noise floor "
            f"{_cell(flip['baseline_noise_floor'])} (delta {_cell(flip['delta'])}, "
            f"{'exceeds' if flip['delta_exceeds_noise_floor'] else 'does NOT clearly exceed'} noise floor).",
            "",
        ]
    lines += ["## Verdict", "", payload.get("verdict", "(fill in after the run)"), ""]
    return "\n".join(lines) + "\n"


def main() -> int:
    args = _arguments()
    started = time.monotonic()

    if args.sample_only:
        # Pure dry-run: no endpoint lookup, no network call of any kind.
        sample, anchors, sample_meta = build_sample(args.matrix_dir)
        cross_check = _cross_check_sample_meta(args.matrix_dir, sample_meta)
        counts = {
            name: sum(1 for p in sample if p.stratum == name) for name in ("residual", "control")
        }
        print(f"sample: {counts}, anchors: {len(anchors)} | {cross_check}", flush=True)
        return 0

    base_url, api_key = endpoint()
    smoke = vision_smoke(base_url, api_key, args.matrix_dir / "previews", args.timeout_seconds)
    print(
        f"vision smoke: http_ok={smoke.get('http_ok')} "
        f"grounded={smoke.get('looks_grounded_structurally')} seconds={smoke.get('seconds', 0):.1f}",
        flush=True,
    )
    if args.smoke_only:
        print(json.dumps(smoke, indent=1))
        return 0 if smoke.get("looks_grounded_structurally") else 1
    if not smoke.get("http_ok") or not smoke.get("looks_grounded_structurally"):
        print("VISION SMOKE FAILED -- stopping before spending further API budget", flush=True)
        payload = {
            "schema_version": "pairhead-35ba3b-escalation-probe-v1",
            "privacy": "counts and metrics only; no asset ids appear anywhere in this file",
            "vision_smoke": smoke,
            "stopped_after_smoke_test": True,
        }
        (args.matrix_dir / "qwen36-35ba3b-pair-probe.json").write_text(
            json.dumps(payload, indent=2) + "\n"
        )
        return 1

    sample, anchors, sample_meta = build_sample(args.matrix_dir)
    cross_check = _cross_check_sample_meta(args.matrix_dir, sample_meta)
    counts = {name: sum(1 for p in sample if p.stratum == name) for name in ("residual", "control")}
    print(f"sample: {counts}, anchors: {len(anchors)} | {cross_check}", flush=True)

    payload: dict[str, Any] = {
        "schema_version": "pairhead-35ba3b-escalation-probe-v1",
        "privacy": "counts and metrics only; no asset ids appear anywhere in this file",
        "vision_smoke": smoke,
        "sample": sample_meta,
        "sample_cross_check": cross_check,
        "stratum_counts": counts,
        "models": {"escalation_candidate": CANDIDATE_MODEL, "incumbent": INCUMBENT_27B},
        "agreement_bar": AGREEMENT_BAR,
        "request": {
            "prompt_source": "selection_selects._PAIR_PROMPT",
            "temperature": 0.0,
            "image_detail": "high",
            "concurrency": args.concurrency,
            "timeout_seconds": args.timeout_seconds,
            "candidate_repeats": CANDIDATE_REPEATS,
            "flip_repeats": FLIP_REPEATS,
        },
    }

    if not args.skip_main:
        print(f"STEP 2: {CANDIDATE_MODEL} on {len(sample)} pairs", flush=True)
        answers = run_batch(
            CANDIDATE_MODEL, sample, args.matrix_dir, args.concurrency, args.timeout_seconds
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
        print(
            f"STEP 3a: {CANDIDATE_MODEL} x{CANDIDATE_REPEATS} on {len(anchors)} anchor pairs",
            flush=True,
        )
        candidate_groups = run_repeats(
            CANDIDATE_MODEL,
            anchors,
            args.matrix_dir,
            args.concurrency,
            args.timeout_seconds,
            CANDIDATE_REPEATS,
        )
        candidate_stability = repeat_stability(CANDIDATE_MODEL, candidate_groups)

        print(
            f"STEP 3b: {CANDIDATE_MODEL} flipped-example x{FLIP_REPEATS} on {len(anchors)} anchor pairs",
            flush=True,
        )
        flipped_groups = run_repeats(
            CANDIDATE_MODEL,
            anchors,
            args.matrix_dir,
            args.concurrency,
            args.timeout_seconds,
            FLIP_REPEATS,
            prompt_text=_FLIPPED_PAIR_PROMPT,
        )
        flip = flip_control(candidate_stability, flipped_groups)

        payload["stability_anchors"] = {
            "anchor_pairs": len(anchors),
            "candidate": asdict(candidate_stability),
            "banked_twentyseven_b": load_banked_27b_anchor_stability(args.matrix_dir),
            "example_flip_control": flip,
        }

    payload["wall_seconds"] = time.monotonic() - started
    (args.matrix_dir / "qwen36-35ba3b-pair-probe.json").write_text(
        json.dumps(payload, indent=2) + "\n"
    )
    (args.matrix_dir / "qwen36-35ba3b-pair-probe.md").write_text(render_report(payload))
    print(f"done in {payload['wall_seconds']:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
