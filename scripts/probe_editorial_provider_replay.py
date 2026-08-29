#!/usr/bin/env python3
"""Replay a frozen private moment wall through one hosted editorial model.

Only thesis and final moment admission are called.  The source filter, Cull,
Structure, descriptions, and cards come from a completed smart-edit result, so
provider comparisons see byte-identical evidence.  Inputs and outputs are
restricted to ``~/.immich-memories-matrix`` because they contain private card
text and model answers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import probe_description_moment_cut as prototype
import probe_smart_edit_matrix as smart

from immich_memories.analysis import llm_metrics
from immich_memories.analysis.llm_query import query_llm
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
        return LLMConfig(
            provider="openai-compatible",
            base_url=_required_env("MELIOUS_AI_BASE_URL"),
            model=model,
            api_key=_required_env("MELIOUS_AI_KEY"),
            thinking=False,
            no_thinking_params={"thinking": {"type": "disabled"}},
        )
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


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--provider", required=True, choices=("openai", "zai-anthropic", "melious"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args()
    args.result = _private_path(args.result, parser, "--result")
    args.out = _private_path(args.out, parser, "--out")
    if not args.result.is_file():
        parser.error(f"result does not exist: {args.result}")
    if args.out.exists():
        parser.error(f"output already exists: {args.out}")
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
    print(
        f"{args.provider}/{args.model}: selected "
        f"{replay['counts']['selected_moments']}/{replay['counts']['wall_moments']} in "
        f"{total.get('wall_seconds', 0):.1f}s; "
        f"tokens {total.get('llm_prompt_tokens', 0)} in / "
        f"{total.get('llm_completion_tokens', 0)} out",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
