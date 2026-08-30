#!/usr/bin/env python3
"""Replay a frozen private editorial wall through one hosted model.

The default stage calls only thesis and final moment admission.  The final-cut
stage calls only the terminal text cut over already-described reservoir assets.
The source filter, Cull, Structure, visual descriptions, and cards come from a
completed smart-edit result, so provider comparisons see frozen evidence.
Inputs and outputs are restricted to ``~/.immich-memories-matrix`` because they
contain private card text and model answers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import probe_description_moment_cut as prototype
import probe_smart_edit_matrix as smart

from immich_memories.analysis import llm_metrics
from immich_memories.analysis.llm_query import query_llm
from immich_memories.config import get_config
from immich_memories.config_models_llm import LLMConfig

_OPENAI_PRICES_PER_MILLION = {
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "gpt-5.6-terra": (2.00, 0.20, 12.00),
    "gpt-5.6-sol": (4.00, 0.40, 20.00),
}


def _private_path(path: Path, parser: argparse.ArgumentParser, flag: str) -> Path:
    resolved = path.expanduser().resolve()
    matrix = (Path.home() / ".immich-memories-matrix").resolve()
    if not resolved.is_relative_to(matrix):
        parser.error(f"{flag} must be inside ~/.immich-memories-matrix")
    return resolved


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def _provider_config(provider: str, model: str) -> LLMConfig:
    if provider == "configured":
        return get_config().llm.model_copy(update={"model": model, "thinking": False})
    if provider == "openai":
        return LLMConfig(
            provider="openai",
            model=model,
            api_key=_required_env("OPENAI_KEY"),
            thinking=False,
            no_thinking_params={},
            extra_params={"reasoning_effort": "none"},
        )
    if provider == "zai-anthropic":
        return LLMConfig(
            provider="anthropic",
            base_url=_required_env("ZAI_BASE_URL"),
            model=model,
            api_key=_required_env("ZAI_API_KEY"),
            thinking=False,
            no_thinking_params={"thinking": {"type": "disabled"}},
        )
    if provider == "melious":
        return smart._hosted_llm_config("melious", model)
    raise ValueError(f"unsupported provider: {provider}")


def _estimated_openai_cost(model: str, metrics: dict[str, Any]) -> float | None:
    rates = _OPENAI_PRICES_PER_MILLION.get(model)
    if rates is None:
        return None
    input_rate, cached_rate, output_rate = rates
    input_tokens = int(metrics.get("llm_prompt_tokens", 0))
    cached_tokens = int(metrics.get("llm_cached_prompt_tokens", 0))
    output_tokens = int(metrics.get("llm_completion_tokens", 0))
    uncached_tokens = max(0, input_tokens - cached_tokens)
    return (
        uncached_tokens * input_rate + cached_tokens * cached_rate + output_tokens * output_rate
    ) / 1_000_000


async def _call(
    prompt: str,
    config: LLMConfig,
    *,
    max_tokens: int,
    timeout_seconds: int,
) -> tuple[str, dict[str, Any]]:
    started = time.monotonic()
    with llm_metrics.collecting() as counters:
        raw = await query_llm(
            prompt,
            config,
            temperature=0.0,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            thinking=False,
            require_complete=True,
        )
    record = counters.as_metrics()
    record["wall_seconds"] = time.monotonic() - started
    estimated = _estimated_openai_cost(config.model, record)
    if estimated is not None:
        record["estimated_cost_usd"] = estimated
    return raw, record


def _replace_thesis(
    selection_prompt: str,
    old_thesis: dict[str, Any],
    new_thesis: dict[str, Any],
) -> str:
    old = json.dumps(old_thesis, ensure_ascii=False, separators=(",", ":"))
    new = json.dumps(new_thesis, ensure_ascii=False, separators=(",", ":"))
    if selection_prompt.count(old) != 1:
        raise ValueError(
            "frozen selection prompt does not contain its recorded thesis exactly once"
        )
    return selection_prompt.replace(old, new, 1)


def _merge_lifecycle_anchors(
    selection: dict[str, Any],
    *,
    ordered_ids: tuple[str, ...],
    requirements: tuple[smart.LifecycleRequirement, ...],
    reference_selection: dict[str, Any],
) -> dict[str, Any]:
    anchor_ids = tuple(dict.fromkeys(item.anchor_id for item in requirements))
    if not anchor_ids:
        return selection
    reference_rows = {row["moment_id"]: row for row in reference_selection.get("keep", [])}
    by_id = {row["moment_id"]: row for row in selection["keep"]}
    for anchor_id in anchor_ids:
        by_id[anchor_id] = reference_rows[anchor_id]
    return {
        **selection,
        "keep": [by_id[moment_id] for moment_id in ordered_ids if moment_id in by_id],
        "lifecycle_anchor_ids": list(anchor_ids),
    }


def _total_metrics(*phases: dict[str, Any]) -> dict[str, Any]:
    total = {
        key: sum(phase.get(key, 0) for phase in phases)
        for key in {key for phase in phases for key in phase}
        if key != "estimated_cost_usd"
    }
    costs = [phase["estimated_cost_usd"] for phase in phases if "estimated_cost_usd" in phase]
    if costs:
        total["estimated_cost_usd"] = sum(costs)
    return total


async def _replay(args: argparse.Namespace) -> dict[str, Any]:
    result = json.loads(args.result.read_text())
    stage = getattr(args, "stage", "moment")
    if stage == "final-cut":
        return await _replay_final_cut(args, result)
    if stage == "global-review":
        return await _replay_global_review(args, result)
    if stage == "deliberation":
        return await _replay_deliberation(args, result)
    return await _replay_moment(args, result)


async def _replay_moment(args: argparse.Namespace, result: dict[str, Any]) -> dict[str, Any]:
    case_dir = args.result.parent
    cards_payload = json.loads((case_dir / "cards.json").read_text())
    ordered_ids = tuple(card["moment_id"] for card in cards_payload["cards"])
    valid_ids = frozenset(ordered_ids)
    edit = result["edit"]
    if edit["configuration"]["shape"] != "flat":
        raise ValueError("provider replay currently requires one frozen flat wall")
    if len(edit["thesis_calls"]) != 1 or len(edit["selection_calls"]) != 1:
        raise ValueError("provider replay requires exactly one thesis and one selection call")

    requirements = tuple(
        smart.LifecycleRequirement(**row) for row in edit.get("lifecycle_requirements", [])
    )
    capacity = int(edit["configuration"]["capacity"]["moment_capacity"])
    required_ids = frozenset(item.anchor_id for item in requirements)
    common = {
        "schema_version": "editorial-provider-replay-v1",
        "privacy": "private replay artifact; do not commit prompts, answers, names, or IDs",
        "source_result": str(args.result),
        "case": result["case"],
        "provider": args.provider,
        "model": args.model,
        "configuration": {
            "temperature": 0.0,
            "thinking": False,
            "frozen_upstream": True,
            "capacity": capacity,
        },
        "reference": {
            "model": edit["configuration"]["text_model"],
            "thesis": edit["thesis"],
            "selection": edit["selection"],
        },
    }

    config = _provider_config(args.provider, args.model)
    thesis_prompt = edit["thesis_calls"][0]["prompt"]
    raw_thesis, thesis_metrics = await _call(
        thesis_prompt,
        config,
        max_tokens=1800,
        timeout_seconds=args.timeout_seconds,
    )
    try:
        thesis = smart._with_lifecycle_turning_points(
            prototype._read_thesis(raw_thesis, valid_ids), requirements
        )
    except (TypeError, ValueError) as error:
        return {
            **common,
            "status": "invalid_thesis",
            "error": str(error),
            "metrics": {"thesis": thesis_metrics, "total": _total_metrics(thesis_metrics)},
            "calls": {"thesis": {"prompt": thesis_prompt, "raw": raw_thesis}},
        }

    selection_prompt = _replace_thesis(edit["selection_calls"][0]["prompt"], edit["thesis"], thesis)
    raw_selection, selection_metrics = await _call(
        selection_prompt,
        config,
        max_tokens=4000,
        timeout_seconds=args.timeout_seconds,
    )
    try:
        selection = smart._read_selection_with_comparison_repair(
            raw_selection,
            valid_ids,
            capacity - len(required_ids),
            excluded_ids=required_ids,
        )
    except (TypeError, ValueError) as error:
        return {
            **common,
            "status": "invalid_selection",
            "error": str(error),
            "thesis": thesis,
            "metrics": {
                "thesis": thesis_metrics,
                "selection": selection_metrics,
                "total": _total_metrics(thesis_metrics, selection_metrics),
            },
            "calls": {
                "thesis": {"prompt": thesis_prompt, "raw": raw_thesis},
                "selection": {"prompt": selection_prompt, "raw": raw_selection},
            },
        }
    selection = _merge_lifecycle_anchors(
        selection,
        ordered_ids=ordered_ids,
        requirements=requirements,
        reference_selection=edit["selection"],
    )
    return {
        **common,
        "status": "complete",
        "thesis": thesis,
        "selection": selection,
        "counts": {
            "wall_moments": len(ordered_ids),
            "selected_moments": len(selection["keep"]),
            "lifecycle_anchors": len(required_ids),
        },
        "metrics": {
            "thesis": thesis_metrics,
            "selection": selection_metrics,
            "total": _total_metrics(thesis_metrics, selection_metrics),
        },
        "calls": {
            "thesis": {"prompt": thesis_prompt, "raw": raw_thesis},
            "selection": {"prompt": selection_prompt, "raw": raw_selection},
        },
    }


def _frozen_effective_media_kind(row: dict[str, Any]) -> str:
    """Accept a moving label only when frozen temporal evidence earned it."""
    label = row["media_kind"]
    source = row.get("source_media_kind", label)
    moving_labels = {"video", "live_photo", "live-motion", "motion"}
    if label not in moving_labels:
        return label
    if row.get("motion_observed") is not True:
        return "photo"
    if row.get("motion_contribution") != "meaningful":
        return "photo"
    return "video" if source == "video" else "live-motion"


def _fine_cut_candidates(payload: dict[str, Any]) -> tuple[smart.FineCutCandidate, ...]:
    """Restore the exact chronological text wall without fetching private media."""
    candidates = tuple(
        smart.FineCutCandidate(
            alias=row["alias"],
            asset_id=row["asset_id"],
            moment_id=row["moment_id"],
            taken_at=datetime.fromisoformat(row["taken_at"]),
            media_kind=(effective_media_kind := _frozen_effective_media_kind(row)),
            favourite=row["favourite"],
            description=row["description"],
            context=tuple(row.get("context", ())),
            episode_id=row.get("episode_id"),
            people_context=tuple(row.get("people_context", ())),
            motion_contribution=row.get("motion_contribution"),
            motion_reason=row.get("motion_reason"),
            source_media_kind=row.get("source_media_kind", row.get("media_kind")),
            motion_observed=row.get("motion_observed") is True,
            render_mode=(
                "motion" if effective_media_kind in {"video", "live-motion"} else "still"
            ),
            render_frame_seconds=row.get("render_frame_seconds"),
        )
        for row in payload["assets"]
    )
    if len({candidate.alias for candidate in candidates}) != len(candidates):
        raise ValueError("frozen final-cut aliases must be unique")
    expected = payload.get("counts", {}).get("fine_cut_candidates")
    if expected is not None and expected != len(candidates):
        raise ValueError("frozen final-cut candidate count changed")
    return candidates


def _frozen_global_wall(
    args: argparse.Namespace, result: dict[str, Any]
) -> tuple[
    dict[str, Any],
    tuple[smart.FineCutCandidate, ...],
    dict[str, Any],
    tuple[str, ...],
    SimpleNamespace,
]:
    """Load the frozen candidate pool and its pre-global chapter winners."""
    final_path = args.result.parent / "final-cut.json"
    if not final_path.is_file():
        raise ValueError("global-review stage requires a sibling final-cut.json")
    frozen = json.loads(final_path.read_text())
    candidates = _fine_cut_candidates(frozen)
    frozen_selection = frozen["selection"]
    proposed_rows = frozen_selection.get("pre_global_review_keep")
    if not isinstance(proposed_rows, list) or not proposed_rows:
        raise ValueError("global-review stage requires frozen pre-global winners")
    proposed_aliases = tuple(row.get("asset_id") for row in proposed_rows)
    candidate_aliases = {candidate.alias for candidate in candidates}
    if (
        any(not isinstance(alias, str) for alias in proposed_aliases)
        or len(set(proposed_aliases)) != len(proposed_aliases)
        or not set(proposed_aliases) <= candidate_aliases
    ):
        raise ValueError("frozen pre-global winners are not unique grounded aliases")
    chapter_selection = {**frozen_selection, "keep": proposed_rows}
    case_payload = result["case"]
    case = SimpleNamespace(
        label=case_payload["label"],
        product=case_payload["product"],
        brief=case_payload["brief"],
    )
    return frozen, candidates, chapter_selection, proposed_aliases, case


async def _replay_global_review(args: argparse.Namespace, result: dict[str, Any]) -> dict[str, Any]:
    """Replay only the frozen whole-sequence wall assembled by local chapter cuts."""
    frozen, candidates, chapter_selection, proposed_aliases, case = _frozen_global_wall(
        args, result
    )
    frozen_selection = frozen["selection"]
    config = _provider_config(args.provider, args.model)
    started = time.monotonic()
    with llm_metrics.collecting() as counters:
        selection, review, call = await smart._global_final_sequence_review(
            candidates,
            chapter_selection,
            case=case,
            thesis=result["edit"]["thesis"],
            llm_config=config,
            cache_path=args.out.with_suffix(".db"),
            timeout_seconds=args.timeout_seconds,
        )
    metrics = counters.as_metrics()
    metrics["elapsed_seconds"] = round(time.monotonic() - started, 3)
    estimated = _estimated_openai_cost(config.model, metrics)
    if estimated is not None:
        metrics["estimated_cost_usd"] = estimated
    selected = {row["asset_id"] for row in selection["keep"]}
    represented = {candidate.moment_id for candidate in candidates if candidate.alias in selected}
    return {
        "schema_version": "editorial-global-sequence-provider-replay-v1",
        "privacy": "private replay artifact; do not commit prompts, answers, names, or IDs",
        "source_result": str(args.result),
        "case": result["case"],
        "provider": args.provider,
        "model": args.model,
        "status": "complete",
        "configuration": {
            "temperature": 0.0,
            "thinking": False,
            "frozen_pre_global_wall": True,
            "vision_calls": 0,
            "capacity": int(frozen["configuration"]["capacity"]),
        },
        "reference": {
            "model": result["edit"]["configuration"]["text_model"],
            "selection": frozen_selection,
        },
        "selection": selection,
        "counts": {
            "wall_assets": len(proposed_aliases),
            "selected_assets": len(selected),
            "represented_moments": len(represented),
        },
        "metrics": {"total": metrics},
        "calls": {"global_review": smart._call_record(call)},
        "review_status": review["status"],
    }


async def _replay_deliberation(args: argparse.Namespace, result: dict[str, Any]) -> dict[str, Any]:
    """Replay the bounded text-only corpus audit over every frozen candidate."""
    frozen, candidates, chapter_selection, proposed_aliases, case = _frozen_global_wall(
        args, result
    )
    capacity = int(frozen["configuration"]["capacity"])
    max_iterations = getattr(args, "max_iterations", 3)
    config = _provider_config(args.provider, args.model)
    started = time.monotonic()
    with llm_metrics.collecting() as counters:
        selection, initial_review, initial_call = await smart._global_final_sequence_review(
            candidates,
            chapter_selection,
            case=case,
            thesis=result["edit"]["thesis"],
            llm_config=config,
            cache_path=args.out.with_suffix(".db"),
            timeout_seconds=args.timeout_seconds,
        )
        selection, deliberation = await smart._iterative_final_asset_review(
            candidates,
            selection,
            case=case,
            thesis=result["edit"]["thesis"],
            capacity=capacity,
            llm_config=config,
            cache_path=args.out.with_suffix(".db"),
            timeout_seconds=args.timeout_seconds,
            max_iterations=max_iterations,
        )
        changed = any(row.get("outcome") == "accepted" for row in deliberation["iterations"])
        final_call = None
        final_review = initial_review
        if changed:
            selection, final_review, final_call = await smart._global_final_sequence_review(
                candidates,
                selection,
                case=case,
                thesis=result["edit"]["thesis"],
                llm_config=config,
                cache_path=args.out.with_suffix(".db"),
                timeout_seconds=args.timeout_seconds,
            )
    metrics = counters.as_metrics()
    metrics["elapsed_seconds"] = round(time.monotonic() - started, 3)
    estimated = _estimated_openai_cost(config.model, metrics)
    if estimated is not None:
        metrics["estimated_cost_usd"] = estimated
    selected = {row["asset_id"] for row in selection["keep"]}
    represented = {candidate.moment_id for candidate in candidates if candidate.alias in selected}
    return {
        "schema_version": "editorial-final-deliberation-provider-replay-v1",
        "privacy": "private replay artifact; do not commit prompts, answers, names, or IDs",
        "source_result": str(args.result),
        "case": result["case"],
        "provider": args.provider,
        "model": args.model,
        "status": "complete",
        "configuration": {
            "temperature": 0.0,
            "thinking": False,
            "frozen_candidate_pool": True,
            "vision_calls": 0,
            "capacity": capacity,
            "max_iterations": max_iterations,
        },
        "reference": {
            "model": result["edit"]["configuration"]["text_model"],
            "selection": frozen["selection"],
        },
        "selection": selection,
        "deliberation": deliberation,
        "counts": {
            "wall_assets": len(candidates),
            "candidate_pool_assets": len(candidates),
            "initial_wall_assets": len(proposed_aliases),
            "selected_assets": len(selected),
            "represented_moments": len(represented),
            "deliberation_iterations": len(deliberation["iterations"]),
        },
        "metrics": {"total": metrics},
        "calls": {
            "initial_global_review": smart._call_record(initial_call),
            "deliberation": [row["calls"] for row in deliberation["iterations"]],
            "final_global_review": smart._call_record(final_call),
        },
        "review_status": final_review["status"],
    }


async def _replay_final_cut(args: argparse.Namespace, result: dict[str, Any]) -> dict[str, Any]:
    """Replay only the frozen terminal asset walls; perform no visual work."""
    final_path = args.result.parent / "final-cut.json"
    if not final_path.is_file():
        raise ValueError("final-cut stage requires a sibling final-cut.json")
    frozen = json.loads(final_path.read_text())
    edit = result["edit"]
    if edit["configuration"]["shape"] != "hierarchical":
        raise ValueError("final-cut provider replay currently requires a hierarchical wall")

    candidates = _fine_cut_candidates(frozen)
    required_aliases = tuple(frozen["selection"].get("required_asset_ids", ()))
    capacity = int(frozen["configuration"]["capacity"])
    plans = smart._hierarchical_final_cut_plan(
        edit,
        candidates,
        required_aliases=required_aliases,
        capacity=capacity,
    )
    case_payload = result["case"]
    case = SimpleNamespace(
        label=case_payload["label"],
        product=case_payload["product"],
        brief=case_payload["brief"],
    )
    config = _provider_config(args.provider, args.model)
    started = time.monotonic()
    with llm_metrics.collecting() as counters:
        chapter_selection, calls = await smart._hierarchical_final_asset_cut(
            plans,
            case=case,
            thesis=edit["thesis"],
            llm_config=config,
            cache_path=args.out.with_suffix(".db"),
            concurrency=2,
            timeout_seconds=args.timeout_seconds,
        )
        pre_global_review_assets = len(chapter_selection["keep"])
        selection, _review, global_review_call = await smart._global_final_sequence_review(
            candidates,
            chapter_selection,
            case=case,
            thesis=edit["thesis"],
            llm_config=config,
            cache_path=args.out.with_suffix(".db"),
            timeout_seconds=args.timeout_seconds,
        )
    metrics = counters.as_metrics()
    metrics["elapsed_seconds"] = round(time.monotonic() - started, 3)
    estimated = _estimated_openai_cost(config.model, metrics)
    if estimated is not None:
        metrics["estimated_cost_usd"] = estimated
    selected = {row["asset_id"] for row in selection["keep"]}
    represented = {candidate.moment_id for candidate in candidates if candidate.alias in selected}
    return {
        "schema_version": "editorial-final-asset-provider-replay-v1",
        "privacy": "private replay artifact; do not commit prompts, answers, names, or IDs",
        "source_result": str(args.result),
        "case": result["case"],
        "provider": args.provider,
        "model": args.model,
        "status": "complete",
        "configuration": {
            "temperature": 0.0,
            "thinking": False,
            "frozen_final_candidates": True,
            "vision_calls": 0,
            "global_sequence_review": True,
            "shape": "hierarchical",
            "capacity": capacity,
        },
        "reference": {
            "model": edit["configuration"]["text_model"],
            "selection": frozen["selection"],
        },
        "selection": selection,
        "counts": {
            "wall_assets": len(candidates),
            "chapters": len(plans),
            "pre_global_review_assets": pre_global_review_assets,
            "selected_assets": len(selected),
            "represented_moments": len(represented),
        },
        "metrics": {"total": metrics},
        "calls": {
            "selection": [smart._call_record(call) for call in calls],
            "global_review": smart._call_record(global_review_call),
        },
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--provider",
        required=True,
        choices=("configured", "openai", "zai-anthropic", "melious"),
    )
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--stage",
        choices=("moment", "final-cut", "global-review", "deliberation"),
        default="moment",
        help=(
            "replay thesis plus moment admission, the hierarchical terminal asset cut, only "
            "the frozen pre-global wall, or bounded all-reservoir deliberation"
        ),
    )
    parser.add_argument("--max-iterations", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args()
    args.result = _private_path(args.result, parser, "--result")
    args.out = _private_path(args.out, parser, "--out")
    if not args.result.is_file():
        parser.error(f"result does not exist: {args.result}")
    if args.out.exists():
        parser.error(f"output already exists: {args.out}")
    if (
        args.stage in {"final-cut", "global-review", "deliberation"}
        and args.out.with_suffix(".db").exists()
    ):
        parser.error("asset replay cache already exists; choose a fresh --out path")
    return args


def main() -> int:
    args = _arguments()
    replay = asyncio.run(_replay(args))
    smart._atomic_json(args.out, replay)
    total = replay["metrics"]["total"]
    if replay["status"] != "complete":
        print(
            f"{args.provider}/{args.model}: {replay['status']} in "
            f"{total.get('wall_seconds', 0):.1f}s; "
            f"tokens {total.get('llm_prompt_tokens', 0)} in / "
            f"{total.get('llm_completion_tokens', 0)} out",
            flush=True,
        )
        return 2
    asset_stage = args.stage in {"final-cut", "global-review", "deliberation"}
    selected_key = "selected_assets" if asset_stage else "selected_moments"
    wall_key = "wall_assets" if asset_stage else "wall_moments"
    wall_seconds = total.get(
        "elapsed_seconds", total.get("wall_seconds", total.get("llm_wall_seconds", 0))
    )
    print(
        f"{args.provider}/{args.model}: selected "
        f"{replay['counts'][selected_key]}/{replay['counts'][wall_key]} in "
        f"{wall_seconds:.1f}s; tokens {total.get('llm_prompt_tokens', 0)} in / "
        f"{total.get('llm_completion_tokens', 0)} out",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
