#!/usr/bin/env python3
"""Measure oMLX thinking transport on one private 400px image.

This is a wire-level diagnostic, not an editorial pass. It sends the same
single-image prompt with thinking disabled, a deliberately small thinking
budget, and the requested 4096-token budget. The private reasoning text is
never printed or persisted; only its source, length, and SHA-256 are retained.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

from immich_memories.analysis.llm_query import build_llm_timeout
from immich_memories.analysis.strict_json import final_json_object
from immich_memories.config import get_config

DEFAULT_MODEL = "scottlowry/Qwen3.8-27B-oQ4e-mtp"
DEFAULT_IMAGES = (
    Path.home()
    / ".immich-memories-matrix"
    / "description-bank-corrected-source-2026-08-27"
    / "400px"
)
DEFAULT_OUT = Path.home() / ".immich-memories-matrix" / "omlx-thinking-single-image-q4-2026-08-28"
DEFAULT_BUDGETS = (256, 4096)
ANSWER_TOKENS = 900


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-id", required=True, help="Private Immich asset UUID")
    parser.add_argument("--images", type=Path, default=DEFAULT_IMAGES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--budget", type=int, action="append")
    parser.add_argument("--answer-tokens", type=int, default=ANSWER_TOKENS)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args()
    matrix = (Path.home() / ".immich-memories-matrix").resolve()
    args.images = args.images.expanduser().resolve()
    args.out = args.out.expanduser().resolve()
    if not args.images.is_relative_to(matrix):
        parser.error("--images must be inside ~/.immich-memories-matrix")
    if not args.out.is_relative_to(matrix):
        parser.error("--out must be inside ~/.immich-memories-matrix")
    if (args.out / "result.json").exists():
        parser.error(f"refusing to overwrite existing result: {args.out / 'result.json'}")
    budgets = tuple(args.budget or DEFAULT_BUDGETS)
    if any(budget < 1 for budget in budgets):
        parser.error("--budget must be positive")
    if args.answer_tokens < 100:
        parser.error("--answer-tokens must be at least 100")
    args.budgets = budgets
    return args


def _image_path(image_dir: Path, asset_id: str) -> Path:
    digest = hashlib.sha256(asset_id.encode()).hexdigest()[:20]
    path = image_dir / f"asset-description-{digest}-001.jpg"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _prompt() -> str:
    shape = {
        "description": "literal visible content",
        "consequential_visible_fact": "important fact visible here, or null",
        "display_value_without_context": "brief factual assessment",
    }
    return f"""Inspect this one 400px image carefully. Reason privately before answering.

Describe only what is visibly grounded. Do not infer a backstory or identify a person. Distinguish
an ordinary object from an object that visibly records a consequential fact. Return only one JSON
object with exactly these keys:
{json.dumps(shape, separators=(",", ":"))}"""


def _payload(
    *, model: str, prompt: str, jpeg: bytes, budget: int | None, answer_tokens: int
) -> dict[str, Any]:
    thinking = budget is not None
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode(),
                            "detail": "high",
                        },
                    },
                ],
            }
        ],
        "temperature": 0.0,
        "max_tokens": answer_tokens + (budget or 0),
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": thinking},
    }
    if budget is not None:
        payload["thinking_budget"] = budget
    return payload


def _split_answer(message: dict[str, Any]) -> tuple[str, str, str]:
    content = message.get("content")
    reasoning = message.get("reasoning_content")
    if content is not None and not isinstance(content, str):
        raise TypeError("oMLX response content is not text")
    if reasoning is not None and not isinstance(reasoning, str):
        raise TypeError("oMLX reasoning_content is not text")
    answer = content or ""
    if reasoning:
        return reasoning, answer, "reasoning_content"
    if "</think>" in answer:
        before, answer = answer.split("</think>", 1)
        return before.removeprefix("<think>"), answer.lstrip(), "content-think-tags"
    return "", answer, "none"


def _usage_record(usage: object) -> dict[str, Any]:
    if not isinstance(usage, dict):
        return {}
    details = usage.get("completion_tokens_details")
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "completion_tokens_details": details if isinstance(details, dict) else None,
    }


def _one(
    client: httpx.Client,
    *,
    url: str,
    model: str,
    prompt: str,
    jpeg: bytes,
    budget: int | None,
    answer_tokens: int,
) -> dict[str, Any]:
    payload = _payload(
        model=model,
        prompt=prompt,
        jpeg=jpeg,
        budget=budget,
        answer_tokens=answer_tokens,
    )
    started = time.monotonic()
    response = client.post(url, json=payload)
    elapsed = time.monotonic() - started
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise TypeError("oMLX response is not an object")
    choice = body["choices"][0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        raise TypeError("oMLX response has no message object")
    message = choice["message"]
    reasoning, content, reasoning_source = _split_answer(message)
    parsed = final_json_object(content)
    return {
        "thinking": budget is not None,
        "thinking_budget": budget,
        "max_tokens": payload["max_tokens"],
        "finish_reason": choice.get("finish_reason"),
        "wall_seconds": round(elapsed, 3),
        "message_keys": sorted(message),
        "reasoning_source": reasoning_source,
        "reasoning_chars": len(reasoning),
        "reasoning_sha256": hashlib.sha256(reasoning.encode()).hexdigest() if reasoning else None,
        "content_chars": len(content),
        "content": content,
        "json_parsed": parsed is not None,
        "usage": _usage_record(body.get("usage")),
    }


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def main() -> int:
    args = _arguments()
    config = get_config().llm.model_copy(update={"model": args.model})
    image_path = _image_path(args.images, args.asset_id)
    jpeg = image_path.read_bytes()
    prompt = _prompt()
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    variants = (None, *args.budgets)
    results: list[dict[str, Any]] = []
    with httpx.Client(
        timeout=build_llm_timeout(float(args.timeout_seconds)), headers=headers
    ) as client:
        for index, budget in enumerate(variants, start=1):
            label = "off" if budget is None else str(budget)
            print(f"thinking probe {index}/{len(variants)} | budget {label}", flush=True)
            try:
                results.append(
                    _one(
                        client,
                        url=url,
                        model=args.model,
                        prompt=prompt,
                        jpeg=jpeg,
                        budget=budget,
                        answer_tokens=args.answer_tokens,
                    )
                )
            except Exception as exc:  # WHY: one dialect failure must not erase the controls
                results.append(
                    {
                        "thinking": budget is not None,
                        "thinking_budget": budget,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    output = {
        "schema_version": "omlx-thinking-single-image-probe-v1",
        "privacy": "private real-library diagnostic; reasoning text intentionally discarded",
        "configuration": {
            "omlx_app_version": "0.6.2",
            "omlx_app_build": "2347",
            "model": args.model,
            "temperature": 0,
            "answer_tokens": args.answer_tokens,
            "requested_budgets": list(args.budgets),
            "image_detail": "high",
        },
        "input": {
            "asset_id": args.asset_id,
            "image_sha256": hashlib.sha256(jpeg).hexdigest(),
            "image_bytes": len(jpeg),
            "image_path": str(image_path),
        },
        "prompt": prompt,
        "results": results,
    }
    result_path = args.out / "result.json"
    _atomic_json(result_path, output)
    print(
        json.dumps(
            {
                "result": str(result_path),
                "runs": [
                    {
                        "budget": row.get("thinking_budget"),
                        "finish": row.get("finish_reason"),
                        "reasoning_chars": row.get("reasoning_chars"),
                        "json_parsed": row.get("json_parsed"),
                        "wall_seconds": row.get("wall_seconds"),
                        "error": row.get("error"),
                    }
                    for row in results
                ],
            },
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
