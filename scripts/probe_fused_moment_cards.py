#!/usr/bin/env python3
"""Compare one-call 400px moment cards with the accepted two-stage June cards.

Every production-grouped moment keeps every visual. The difference under test
is call shape only: instead of one visual description per asset followed by one
text-only card call, one cached multimodal call reads the moment's ordered 400px
visuals and writes the same factual-card schema.

The accepted card is never sent to the model. It is copied beside the fused
answer only after the call so the result can be inspected. Inputs and outputs
remain under ``~/.immich-memories-matrix``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

from probe_people_context import PersonFact, load_person_facts

from immich_memories.analysis.contact_sheets import ContactSheetPage, TileRef
from immich_memories.analysis.editorial_gateway import (
    VisualEditorialGateway,
    VisualEditorialRequest,
)
from immich_memories.analysis.llm_query import query_llm
from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.strict_json import bounded_model_text, final_json_object
from immich_memories.analysis.visual_request_planner import VisionRequestLimits
from immich_memories.cache.judgment_cache import JudgmentCache, judgment_key
from immich_memories.config import get_config

DEFAULT_BASELINE = (
    Path.home()
    / ".immich-memories-matrix"
    / "description-moment-cards-metadata-thesis-v4-favourites-q4-2026-08-27"
    / "result.json"
)
DEFAULT_IMAGES = (
    Path.home()
    / ".immich-memories-matrix"
    / "description-bank-corrected-source-2026-08-27"
    / "400px"
)
DEFAULT_OUT = Path.home() / ".immich-memories-matrix" / "fused-moment-cards-q4-2026-08-28"
DEFAULT_CACHE = Path.home() / ".immich-memories-matrix" / "fused-moment-card-cache" / "judgments.db"
DEFAULT_MODEL = "scottlowry/Qwen3.8-27B-oQ4e-mtp"
SCHEMA_VERSION = "description-moment-card-v2"
PASS_VERSION = "fused-moment-card-v1"  # noqa: S105 - prototype wire identity
PROMPT_VERSION = "fused-moment-card-prompt-v1"
RENDER_VERSION = "banked-asset-400px-v1"
MAX_CARD_CHARS = 700


@dataclass(frozen=True)
class FusedCard:
    moment_id: str
    asset_ids: tuple[str, ...]
    visual_count: int
    favourite_count: int
    grounded_context: tuple[str, ...]
    people_metadata: tuple[dict[str, Any], ...]
    accepted_summary: str
    fused_summary: str | None
    raw: str | None
    wall_seconds: float
    request_count: int
    cache_hits: int
    actual_calls: int
    error: str | None = None


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--images", type=Path, default=DEFAULT_IMAGES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--moment", action="append", help="Moment ID; default is the full wall")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    matrix = (Path.home() / ".immich-memories-matrix").resolve()
    args.baseline = args.baseline.expanduser().resolve()
    args.images = args.images.expanduser().resolve()
    args.out = args.out.expanduser().resolve()
    args.cache = args.cache.expanduser().resolve()
    if not args.baseline.is_relative_to(matrix):
        parser.error("--baseline must be inside ~/.immich-memories-matrix")
    if not args.images.is_relative_to(matrix):
        parser.error("--images must be inside ~/.immich-memories-matrix")
    if not args.out.is_relative_to(matrix):
        parser.error("--out must be inside ~/.immich-memories-matrix")
    if not args.cache.is_relative_to(matrix):
        parser.error("--cache must be inside ~/.immich-memories-matrix")
    if (args.out / "result.json").exists():
        parser.error(f"refusing to overwrite existing result: {args.out / 'result.json'}")
    if not 1 <= args.concurrency <= 8:
        parser.error("--concurrency must be between 1 and 8")
    return args


def _asset_sheet(image_dir: Path, asset_id: str) -> ContactSheetPage:
    digest = hashlib.sha256(asset_id.encode()).hexdigest()[:20]
    path = image_dir / f"asset-description-{digest}-001.jpg"
    jpeg = path.read_bytes()
    return ContactSheetPage(
        sheet_id=f"asset-400px-{digest}",
        path=path,
        jpeg_bytes=jpeg,
        sha256=hashlib.sha256(jpeg).hexdigest(),
        tile_refs=(TileRef(1, asset_id),),
        layout_version="asset-description-400px-v1",
    )


def _people_names(card: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            name.strip()
            for item in card.get("grounded_context") or ()
            if str(item).startswith("people:")
            for name in str(item).split(":", 1)[1].split(",")
            if name.strip()
        )
    )


def _age_label(born: str | None, taken_at: str) -> str | None:
    if not born:
        return None
    try:
        birthday = date.fromisoformat(born)
        when = datetime.fromisoformat(taken_at).date()
    except ValueError:
        return None
    days = (when - birthday).days
    if days < 0:
        return "capture predates recorded birth date"
    if days <= 1:
        return "newborn"
    if days < 60:
        return f"{days} days old"
    if days < 730:
        return f"{days // 30} months old"
    years = when.year - birthday.year - ((when.month, when.day) < (birthday.month, birthday.day))
    return f"aged {years}"


def _people_metadata(
    card: dict[str, Any],
    facts: dict[str, PersonFact],
) -> tuple[dict[str, Any], ...]:
    names = _people_names(card)
    present = set(names)
    rows: list[dict[str, Any]] = []
    for name in names:
        fact = facts.get(name)
        if fact is None:
            rows.append(
                {
                    "name": name,
                    "relationship_to_owner": "unconfirmed",
                    "birth_date": None,
                    "first_library_month": None,
                    "sustained_onset": None,
                    "relationships": [],
                }
            )
            continue
        links = [
            {
                "kind": link.kind,
                "with": link.target_name,
                "source": link.source,
            }
            for link in fact.links
            if link.target_name in present
        ]
        rows.append(
            {
                "name": name,
                "relationship_to_owner": fact.relationship,
                "relationship_source": fact.relationship_source,
                "birth_date": fact.birth_date,
                "age_at_capture": _age_label(fact.birth_date, str(card["taken_at"])),
                "first_library_month": fact.first_month,
                "sustained_onset": fact.onset,
                "library_tier": fact.tier,
                "relationships": links,
            }
        )
    return tuple(rows)


def _prompt(card: dict[str, Any], people_metadata: tuple[dict[str, Any], ...]) -> str:
    context = card.get("grounded_context") or []
    metadata = {
        "moment_id": card["moment_id"],
        "visual_count": card["visual_count"],
        "favourite_count": card["favourite_count"],
        "grounded_context": context,
        "people": people_metadata,
    }
    shape = {"schema_version": SCHEMA_VERSION, "summary": "literal inventory of the moment"}
    return f"""Build one compact factual card from every attached 400px visual.

The visuals are one production-grouped moment, attached in chronological order. Inspect every
visual. Preserve every distinct visible subject, action, object, setting, and readable consequential
detail, even when it appears in only one frame and the other frames repeat a common scene. Ground
recognized people and places in the supplied metadata when relevant. A name says who Immich
recognized, not their relationship; do not invent one. Collapse only genuine repetition. Do not
rank, score, select, interpret significance, infer relationships, or invent context. Use one line
without double quotes or backslashes.

DETERMINISTIC MOMENT METADATA
{json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))}

Return only one complete JSON object with exactly these keys:
{json.dumps(shape, separators=(",", ":"))}
The schema_version value must be exactly {SCHEMA_VERSION}; do not shorten or paraphrase it."""


def _summary(raw: str) -> str:
    payload = final_json_object(raw)
    if payload is None or set(payload) != {"schema_version", "summary"}:
        raise ValueError("fused card answer is not the exact JSON envelope")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("fused card answer has the wrong schema version")
    summary = bounded_model_text(payload.get("summary"), max_chars=MAX_CARD_CHARS)
    if summary is None:
        raise ValueError("fused card summary is not safe bounded text")
    return summary


async def _repair_envelope(
    raw: str,
    *,
    llm_config: Any,
    cache_path: Path,
    timeout_seconds: int,
) -> tuple[str, bool]:
    prompt = f"""Correct only the JSON envelope below.

Return exactly one JSON object with exactly the keys schema_version and summary. Preserve the
summary text verbatim. Set schema_version to the exact literal {SCHEMA_VERSION}. Do not add prose.

INPUT
{raw}"""
    key = judgment_key(model=llm_config.model, prompt=prompt, thinking=False)
    cache_hit = JudgmentCache(cache_path).answer_for(key) is not None
    repaired = await query_llm(
        prompt,
        llm_config,
        temperature=0.0,
        max_tokens=900,
        timeout_seconds=timeout_seconds,
        thinking=False,
        cache_path=cache_path,
        require_complete=True,
    )
    return repaired, cache_hit


async def _run(
    args: argparse.Namespace,
    cards: tuple[dict[str, Any], ...],
    facts: dict[str, PersonFact],
) -> tuple[FusedCard, ...]:
    config = get_config()
    llm_config = config.llm.model_copy(update={"model": args.model})
    trace = Trace()
    gateway = VisualEditorialGateway(
        llm_config=llm_config,
        cache_path=args.cache,
        trace=trace,
    )
    semaphore = asyncio.Semaphore(args.concurrency)
    completed = 0
    started = time.monotonic()

    async def one(card: dict[str, Any]) -> FusedCard:
        nonlocal completed
        asset_ids = tuple(str(asset_id) for asset_id in card["asset_ids"])
        pages = tuple(_asset_sheet(args.images, asset_id) for asset_id in asset_ids)
        people_metadata = _people_metadata(card, facts)
        request = VisualEditorialRequest(
            pass_name="fused-moment-card",  # noqa: S106 - prototype pass identity
            pass_version=PASS_VERSION,
            prompt=_prompt(card, people_metadata),
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            pages=pages,
            ordered_input_ids=asset_ids,
            ordered_group_ids=(str(card["production_group_id"]),),
            grounded_annotations=tuple(str(item) for item in card["grounded_context"]),
            upstream_material=(),
            render_version=RENDER_VERSION,
            limits=VisionRequestLimits(
                max_pages_per_request=max(1, len(pages)),
                max_output_tokens=900,
                timeout_seconds=args.timeout_seconds,
            ),
            image_detail="high",
        )
        call_started = time.monotonic()
        answers = []
        repair_cache_hits = 0
        repair_calls = 0
        try:
            async with semaphore:
                answer = await asyncio.to_thread(gateway.ask, request)
            answers.append(answer)
            try:
                fused = _summary(answer.raw_text)
            except ValueError:
                async with semaphore:
                    repaired, repair_cache_hit = await _repair_envelope(
                        answer.raw_text,
                        llm_config=llm_config,
                        cache_path=args.cache,
                        timeout_seconds=args.timeout_seconds,
                    )
                repair_cache_hits += int(repair_cache_hit)
                repair_calls += int(not repair_cache_hit)
                answer = replace(
                    answer,
                    raw_text=repaired,
                )
                fused = _summary(answer.raw_text)
            result = FusedCard(
                moment_id=str(card["moment_id"]),
                asset_ids=asset_ids,
                visual_count=int(card["visual_count"]),
                favourite_count=int(card["favourite_count"]),
                grounded_context=tuple(str(item) for item in card["grounded_context"]),
                people_metadata=people_metadata,
                accepted_summary=str(card["summary"]),
                fused_summary=fused,
                raw=answer.raw_text,
                wall_seconds=time.monotonic() - call_started,
                request_count=len(answers) + int(repair_cache_hits + repair_calls > 0),
                cache_hits=sum(item.provenance.cache_hit for item in answers) + repair_cache_hits,
                actual_calls=(
                    sum(item.request_trace.actual_calls for item in answers) + repair_calls
                ),
            )
        except Exception as exc:  # WHY: one bad moment must not erase the comparison run
            last = answers[-1] if answers else None
            result = FusedCard(
                moment_id=str(card["moment_id"]),
                asset_ids=asset_ids,
                visual_count=int(card["visual_count"]),
                favourite_count=int(card["favourite_count"]),
                grounded_context=tuple(str(item) for item in card["grounded_context"]),
                people_metadata=people_metadata,
                accepted_summary=str(card["summary"]),
                fused_summary=None,
                raw=last.raw_text if last is not None else None,
                wall_seconds=time.monotonic() - call_started,
                request_count=len(answers) + int(repair_cache_hits + repair_calls > 0),
                cache_hits=sum(item.provenance.cache_hit for item in answers) + repair_cache_hits,
                actual_calls=(
                    sum(item.request_trace.actual_calls for item in answers) + repair_calls
                ),
                error=f"{type(exc).__name__}: {exc}",
            )
        completed += 1
        if completed == 1 or completed % 10 == 0 or completed == len(cards):
            elapsed = time.monotonic() - started
            rate = completed / elapsed if elapsed else 0.0
            eta = (len(cards) - completed) / rate if rate else 0.0
            print(
                f"fused cards: {completed}/{len(cards)} | elapsed {elapsed:.1f}s | ETA {eta:.1f}s",
                flush=True,
            )
        return result

    return tuple(await asyncio.gather(*(one(card) for card in cards)))


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def main() -> int:
    args = _arguments()
    payload = json.loads(args.baseline.read_text())
    all_cards = tuple(payload["cards"])
    wanted = set(args.moment or ())
    cards = tuple(card for card in all_cards if not wanted or card["moment_id"] in wanted)
    missing = wanted - {str(card["moment_id"]) for card in cards}
    if missing:
        raise ValueError(f"unknown moment IDs: {', '.join(sorted(missing))}")
    if not cards:
        raise ValueError("no moments selected")
    started = time.monotonic()
    facts = load_person_facts(include_derived=True)
    results = asyncio.run(_run(args, cards, facts))
    elapsed = time.monotonic() - started
    output = {
        "schema_version": "fused-moment-card-probe-v1",
        "privacy": "private real-library comparison; do not commit",
        "configuration": {
            "model": args.model,
            "temperature": 0,
            "thinking": False,
            "concurrency": args.concurrency,
            "input_fidelity": "every asset's existing 400px description sheet",
            "baseline": str(args.baseline),
            "cache": str(args.cache),
        },
        "counts": {
            "moments": len(results),
            "visuals": sum(result.visual_count for result in results),
            "successful_cards": sum(result.fused_summary is not None for result in results),
            "requests": sum(result.request_count for result in results),
            "cache_hits": sum(result.cache_hits for result in results),
            "actual_calls": sum(result.actual_calls for result in results),
        },
        "timings": {"wall_seconds": elapsed},
        "cards": [asdict(result) for result in results],
    }
    _atomic_json(args.out / "result.json", output)
    print(
        f"wrote {len(results)} fused cards over {output['counts']['visuals']} visuals "
        f"in {elapsed:.1f}s to {args.out / 'result.json'}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
