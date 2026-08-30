#!/usr/bin/env python3
"""Compare fused moment-card answers across schema variants and models.

Replays the banked 2007 fused-card requests (same prompt, same 400px contact
sheets) against the local OpenAI-compatible server at temperature 0, under two
schema variants:

``current``  the shipped envelope: {schema_version, summary}
``fixed``    the same envelope plus three guessable keys (people, relations,
             activity) whose placeholder names an explicit "insufficient
             evidence" value, so the hedge is a schema value rather than a
             prose instruction.

Everything private (prompts, card text, ids) is read from and written below
``~/.immich-memories-matrix``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from immich_memories.analysis.llm_query import query_llm  # noqa: E402
from immich_memories.analysis.strict_json import (  # noqa: E402
    bounded_model_text,
    final_json_object,
)
from immich_memories.config_models_llm import LLMConfig  # noqa: E402

MATRIX_ROOT = Path.home() / ".immich-memories-matrix"
DEFAULT_CASE = MATRIX_ROOT / "smart-edit-consistency-v23-2026-08-30" / "01-year-2007"
DEFAULT_OUT = MATRIX_ROOT / "pairhead-2026-08-30"
DEFAULT_MODEL = "scottlowry/Qwen3.8-27B-oQ4e-mtp"
CARD_SCHEMA = "description-moment-card-v2"
MAX_CARD_CHARS = 700
MAX_OUTPUT_TOKENS = 900

CURRENT_SHAPE = {"schema_version": CARD_SCHEMA, "summary": "literal inventory of the moment"}
FIXED_SHAPE = {
    "schema_version": CARD_SCHEMA,
    "summary": "literal inventory of the moment",
    "people": "who is visible, or insufficient evidence",
    "relations": "how they are related, or insufficient evidence",
    "activity": "what they are doing, or insufficient evidence",
}
FIXED_SENTENCE = (
    "Write exactly insufficient evidence for people, relations, "
    "or activity the visuals do not show."
)
HEDGE = "insufficient evidence"

PERSON_NOUN = re.compile(
    r"\b(person|people|man|men|woman|women|child|children|boy|boys|girl|girls|adult|adults"
    r"|individual|individuals|figure|figures|baby|babies|toddler|teenager|teenagers)\b",
    re.IGNORECASE,
)
UNCERTAIN = re.compile(
    r"\b(distant|distance|blurry|blurred|out of focus|out-of-focus|partially|obscured"
    r"|silhouette|silhouetted|indistinct|unclear|not visible|difficult to|small|far away"
    r"|cropped|dark|low light|motion blur|back to the camera|facing away|underexposed"
    r"|grainy|overexposed)\b",
    re.IGNORECASE,
)
RELATION_TERM = re.compile(
    r"\b(mother|father|mom|dad|parent|parents|son|daughter|sister|brother|sibling|siblings"
    r"|grandmother|grandfather|grandma|grandpa|granddaughter|grandson|aunt|uncle|cousin"
    r"|niece|nephew|wife|husband|spouse|partner|girlfriend|boyfriend|couple|family"
    r"|friend|friends|colleague|classmate)\b",
    re.IGNORECASE,
)
SPECULATION = re.compile(
    r"\b(appears to|appear to|seems to|seem to|likely|possibly|presumably|apparently"
    r"|suggesting|suggests|indicating|implying|probably|perhaps|may be|might be)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SampleMoment:
    """One banked moment replayed by every arm of the probe."""

    moment_id: str
    stratum: str
    visual_count: int
    favourite_count: int
    supplied_name_count: int
    uncertainty_cues: int
    sheet_names: tuple[str, ...]


@dataclass
class CardScore:
    """Everything measured about one parsed (or unparsed) answer."""

    moment_id: str
    stratum: str
    repeat: int
    parse_ok: bool
    parse_error: str | None
    latency_seconds: float
    raw_chars: int
    summary_chars: int
    summary_invented_names: int
    summary_supplied_names: int
    summary_unsupported_relations: int
    summary_speculation: int
    all_invented_names: int
    all_unsupported_relations: int
    all_speculation: int
    hedged_fields: list[str] = field(default_factory=list)
    echoed_fields: list[str] = field(default_factory=list)
    bank_exact_match: bool | None = None


def _shape_json(shape: dict[str, str]) -> str:
    return json.dumps(shape, separators=(",", ":"))


def build_prompt(banked_prompt: str, schema: str) -> str:
    """Return the banked card prompt, optionally with the hedged-key schema."""
    if schema == "current":
        return banked_prompt
    old = _shape_json(CURRENT_SHAPE)
    if old not in banked_prompt:
        raise ValueError("banked prompt does not carry the current card shape")
    new = f"{_shape_json(FIXED_SHAPE)}\n{FIXED_SENTENCE}"
    return banked_prompt.replace(old, new, 1)


def parse_answer(raw: str, schema: str) -> dict[str, str]:
    """Validate one answer against the arm's exact envelope and return its fields."""
    payload = final_json_object(raw)
    if payload is None:
        raise ValueError("answer is not a JSON object")
    expected = set(CURRENT_SHAPE) if schema == "current" else set(FIXED_SHAPE)
    if set(payload) != expected:
        raise ValueError(f"envelope keys are {sorted(payload)}")
    if payload.get("schema_version") != CARD_SCHEMA:
        raise ValueError("wrong schema version")
    fields: dict[str, str] = {}
    for key in expected - {"schema_version"}:
        limit = MAX_CARD_CHARS if key == "summary" else 300
        value = bounded_model_text(payload.get(key), max_chars=limit)
        if value is None:
            raise ValueError(f"{key} is not safe bounded text")
        fields[key] = value
    return fields


def _supported_relations(people_metadata: Sequence[str]) -> set[str]:
    joined = " ".join(people_metadata).lower()
    return set(RELATION_TERM.findall(joined))


def _name_hits(text: str, names: Sequence[str]) -> int:
    return sum(text.count(name) for name in names)


def _unsupported_relations(text: str, supported: set[str]) -> int:
    return sum(1 for term in RELATION_TERM.findall(text) if term.lower() not in supported)


def score_card(
    *,
    moment: SampleMoment,
    card: dict[str, Any],
    fields: dict[str, str],
    known_names: Sequence[str],
    repeat: int,
    latency: float,
    raw: str,
) -> CardScore:
    """Measure fabrication, hedging and echo on one parsed answer."""
    supplied = [p.split(" [")[0].strip() for p in card.get("people_metadata") or ()]
    foreign = [name for name in known_names if name not in supplied]
    supported = _supported_relations(card.get("people_metadata") or ())
    summary = fields.get("summary", "")
    every = " ".join(fields.values())
    placeholders = FIXED_SHAPE if len(fields) > 1 else CURRENT_SHAPE
    return CardScore(
        moment_id=moment.moment_id,
        stratum=moment.stratum,
        repeat=repeat,
        parse_ok=True,
        parse_error=None,
        latency_seconds=round(latency, 2),
        raw_chars=len(raw),
        summary_chars=len(summary),
        summary_invented_names=_name_hits(summary, foreign),
        summary_supplied_names=_name_hits(summary, supplied),
        summary_unsupported_relations=_unsupported_relations(summary, supported),
        summary_speculation=len(SPECULATION.findall(summary)),
        all_invented_names=_name_hits(every, foreign),
        all_unsupported_relations=_unsupported_relations(every, supported),
        all_speculation=len(SPECULATION.findall(every)),
        hedged_fields=sorted(k for k, v in fields.items() if v.strip().lower().startswith(HEDGE)),
        echoed_fields=sorted(k for k, v in fields.items() if v.strip() == placeholders.get(k)),
    )


def known_library_names(cards: Sequence[dict[str, Any]]) -> tuple[str, ...]:
    """Every person name Immich supplied anywhere in the case."""
    names = {
        person.split(" [")[0].strip()
        for card in cards
        for person in card.get("people_metadata") or ()
    }
    return tuple(sorted(names, key=len, reverse=True))


def _uncertainty_cues(card: dict[str, Any]) -> int:
    return len(UNCERTAIN.findall(card.get("summary") or ""))


def _has_humans(card: dict[str, Any], names: Sequence[str]) -> bool:
    summary = card.get("summary") or ""
    return bool(PERSON_NOUN.search(summary)) or _name_hits(summary, names) > 0


def _sheet_names(sheets_dir: Path, moment_id: str) -> tuple[str, ...]:
    index = moment_id.lstrip("M")
    return tuple(sorted(p.name for p in sheets_dir.glob(f"fused-moment-{index}-*.jpg")))


def build_sample(
    cards: Sequence[dict[str, Any]],
    sheets_dir: Path,
    *,
    named: int,
    scenery: int,
    ambiguous: int,
) -> tuple[SampleMoment, ...]:
    """Stratify the banked cards into named / scenery / ambiguous replay moments."""
    names = known_library_names(cards)
    usable = [c for c in cards if _sheet_names(sheets_dir, c["moment_id"])]
    recognized = [c for c in usable if c.get("people_metadata")]
    anonymous = [c for c in usable if not c.get("people_metadata")]
    named_pool = sorted(
        (c for c in recognized if _name_hits(c["summary"], names) > 0),
        key=lambda c: (-c["visual_count"], c["moment_id"]),
    )
    no_humans = [c for c in anonymous if not _has_humans(c, names)]
    with_humans = sorted(anonymous, key=lambda c: (-_uncertainty_cues(c), c["moment_id"]))
    ambiguous_pool = [c for c in with_humans if _has_humans(c, names)]
    scenery_pool = no_humans + [c for c in reversed(ambiguous_pool) if _uncertainty_cues(c) == 0]
    chosen_ambiguous = ambiguous_pool[:ambiguous]
    taken = {c["moment_id"] for c in chosen_ambiguous}
    chosen_scenery = [c for c in scenery_pool if c["moment_id"] not in taken][:scenery]
    picks = [
        *((c, "named") for c in _spread(named_pool, named)),
        *((c, "scenery") for c in chosen_scenery),
        *((c, "ambiguous") for c in chosen_ambiguous),
    ]
    return tuple(
        SampleMoment(
            moment_id=card["moment_id"],
            stratum=stratum,
            visual_count=card["visual_count"],
            favourite_count=card["favourite_count"],
            supplied_name_count=len(card.get("people_metadata") or ()),
            uncertainty_cues=_uncertainty_cues(card),
            sheet_names=_sheet_names(sheets_dir, card["moment_id"]),
        )
        for card, stratum in picks
    )


def _spread(pool: Sequence[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """Take `count` items evenly across a sorted pool so sizes stay mixed."""
    if count >= len(pool):
        return list(pool)
    step = len(pool) / count
    return [pool[int(i * step)] for i in range(count)]


async def _ask_card(
    *,
    prompt: str,
    images: tuple[bytes, ...],
    llm_config: LLMConfig,
    timeout_seconds: int,
) -> tuple[str, float]:
    started = time.monotonic()
    raw = await query_llm(
        prompt,
        llm_config,
        temperature=0.0,
        max_tokens=MAX_OUTPUT_TOKENS,
        timeout_seconds=timeout_seconds,
        thinking=False,
        images=images,
        image_detail="high",
        require_complete=True,
    )
    return raw, time.monotonic() - started


async def run_arm(
    *,
    sample: Sequence[SampleMoment],
    cards_by_id: dict[str, dict[str, Any]],
    sheets_dir: Path,
    schema: str,
    llm_config: LLMConfig,
    repeats: int,
    concurrency: int,
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    """Replay every sampled moment `repeats` times and score each answer."""
    names = known_library_names(list(cards_by_id.values()))
    semaphore = asyncio.Semaphore(concurrency)
    done = 0
    total = len(sample) * repeats

    async def one(moment: SampleMoment, repeat: int) -> dict[str, Any]:
        nonlocal done
        card = cards_by_id[moment.moment_id]
        prompt = build_prompt(card["summary_call"]["prompt"], schema)
        images = tuple((sheets_dir / name).read_bytes() for name in moment.sheet_names)
        async with semaphore:
            try:
                raw, latency = await _ask_card(
                    prompt=prompt,
                    images=images,
                    llm_config=llm_config,
                    timeout_seconds=timeout_seconds,
                )
            except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                raw, latency = "", 0.0
                score = _failed_score(moment, repeat, f"call failed: {type(exc).__name__}")
                done += 1
                return {"score": asdict(score), "raw": raw}
        try:
            fields = parse_answer(raw, schema)
        except ValueError as exc:
            score = _failed_score(moment, repeat, str(exc))
            score.latency_seconds = round(latency, 2)
            score.raw_chars = len(raw)
        else:
            score = score_card(
                moment=moment,
                card=card,
                fields=fields,
                known_names=names,
                repeat=repeat,
                latency=latency,
                raw=raw,
            )
            if schema == "current" and repeat == 0:
                banked = (card["summary_call"].get("raw") or "").strip()
                score.bank_exact_match = raw.strip() == banked
        done += 1
        if done % 10 == 0 or done == total:
            print(f"  {done}/{total} answers", flush=True)
        return {"score": asdict(score), "raw": raw}

    jobs = [one(moment, repeat) for repeat in range(repeats) for moment in sample]
    return list(await asyncio.gather(*jobs))


def _failed_score(moment: SampleMoment, repeat: int, error: str) -> CardScore:
    return CardScore(
        moment_id=moment.moment_id,
        stratum=moment.stratum,
        repeat=repeat,
        parse_ok=False,
        parse_error=error,
        latency_seconds=0.0,
        raw_chars=0,
        summary_chars=0,
        summary_invented_names=0,
        summary_supplied_names=0,
        summary_unsupported_relations=0,
        summary_speculation=0,
        all_invented_names=0,
        all_unsupported_relations=0,
        all_speculation=0,
    )


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate scores overall and per stratum."""
    scores = [row["score"] for row in rows]
    strata = sorted({s["stratum"] for s in scores})
    by_stratum = {
        stratum: _aggregate([s for s in scores if s["stratum"] == stratum]) for stratum in strata
    }
    agreement = _self_agreement(rows)
    return {"overall": _aggregate(scores), "by_stratum": by_stratum, "self_agreement": agreement}


def _aggregate(scores: Sequence[dict[str, Any]]) -> dict[str, Any]:
    total = len(scores)
    ok = [s for s in scores if s["parse_ok"]]
    hedged = [s for s in ok if s["hedged_fields"]]
    matched = [s for s in ok if s.get("bank_exact_match") is not None]
    return {
        "answers": total,
        "parse_ok": len(ok),
        "cards_with_invented_name": sum(1 for s in ok if s["summary_invented_names"]),
        "cards_with_unsupported_relation": sum(1 for s in ok if s["summary_unsupported_relations"]),
        "cards_with_unsupported_relation_any_field": sum(
            1 for s in ok if s["all_unsupported_relations"]
        ),
        "cards_with_speculation": sum(1 for s in ok if s["summary_speculation"]),
        "invented_name_mentions": sum(s["summary_invented_names"] for s in ok),
        "supplied_name_mentions": sum(s["summary_supplied_names"] for s in ok),
        "cards_hedging": len(hedged),
        "hedged_field_counts": _field_counts(ok, "hedged_fields"),
        "echoed_field_counts": _field_counts(ok, "echoed_fields"),
        "mean_summary_chars": round(sum(s["summary_chars"] for s in ok) / max(1, len(ok)), 1),
        "mean_latency_seconds": round(sum(s["latency_seconds"] for s in ok) / max(1, len(ok)), 2),
        "bank_exact_match": sum(1 for s in matched if s["bank_exact_match"]),
        "bank_compared": len(matched),
    }


def _field_counts(scores: Sequence[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for score in scores:
        for name in score.get(key) or ():
            counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


def _self_agreement(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_moment: dict[str, list[str]] = {}
    for row in rows:
        by_moment.setdefault(row["score"]["moment_id"], []).append(row["raw"].strip())
    repeated = {mid: raws for mid, raws in by_moment.items() if len(raws) > 1}
    if not repeated:
        return {}
    identical = sum(1 for raws in repeated.values() if len(set(raws)) == 1)
    return {
        "moments_repeated": len(repeated),
        "repeats_each": min(len(r) for r in repeated.values()),
        "moments_byte_identical_across_repeats": identical,
        "distinct_answers_per_moment": {
            mid: len(set(raws)) for mid, raws in sorted(repeated.items())
        },
    }


def _llm_config(model: str, config_path: Path) -> LLMConfig:
    raw = yaml.safe_load(config_path.read_text())
    section = raw.get("llm") or (raw.get("advanced") or {}).get("llm")
    if not section:
        raise SystemExit("no llm section in config")
    return LLMConfig(**{**section, "model": model, "thinking": False})


def _load_sample(
    out_dir: Path, cards: Sequence[dict[str, Any]], sheets_dir: Path, args: argparse.Namespace
) -> tuple[SampleMoment, ...]:
    path = out_dir / "sample.json"
    if path.exists():
        return tuple(
            SampleMoment(**{**row, "sheet_names": tuple(row["sheet_names"])})
            for row in json.loads(path.read_text())["moments"]
        )
    sample = build_sample(
        cards, sheets_dir, named=args.named, scenery=args.scenery, ambiguous=args.ambiguous
    )
    path.write_text(
        json.dumps({"moments": [asdict(m) for m in sample]}, indent=1), encoding="utf-8"
    )
    return sample


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--schema", choices=("current", "fixed"), default="current")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--case", type=Path, default=DEFAULT_CASE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--config", type=Path, default=Path.home() / ".immich-memories/config.yaml")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--named", type=int, default=11)
    parser.add_argument("--scenery", type=int, default=7)
    parser.add_argument("--ambiguous", type=int, default=12)
    parser.add_argument("--only-stratum", default=None)
    parser.add_argument("--moments", default=None, help="comma-separated moment ids to replay")
    parser.add_argument("--tag", default=None, help="suffix for the result filename")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = args.out.expanduser().resolve()
    if not out_dir.is_relative_to(MATRIX_ROOT.resolve()):
        raise SystemExit("--out must be inside ~/.immich-memories-matrix")
    out_dir.mkdir(parents=True, exist_ok=True)
    case = args.case.expanduser().resolve()
    cards = json.loads((case / "cards.json").read_text())["cards"]
    sheets_dir = case / "fused-card-sheets"
    sample = _load_sample(out_dir, cards, sheets_dir, args)
    if args.only_stratum:
        sample = tuple(m for m in sample if m.stratum == args.only_stratum)
    if args.moments:
        wanted = {mid.strip() for mid in args.moments.split(",") if mid.strip()}
        sample = tuple(m for m in sample if m.moment_id in wanted)
    cards_by_id = {c["moment_id"]: c for c in cards}
    llm_config = _llm_config(args.model, args.config.expanduser())
    print(
        f"model={args.model} schema={args.schema} moments={len(sample)} repeats={args.repeats}",
        flush=True,
    )
    started = time.monotonic()
    rows = asyncio.run(
        run_arm(
            sample=sample,
            cards_by_id=cards_by_id,
            sheets_dir=sheets_dir,
            schema=args.schema,
            llm_config=llm_config,
            repeats=args.repeats,
            concurrency=args.concurrency,
            timeout_seconds=args.timeout,
        )
    )
    payload = {
        "model": args.model,
        "schema": args.schema,
        "repeats": args.repeats,
        "case": str(case),
        "wall_seconds": round(time.monotonic() - started, 1),
        "prompt_diff": {
            "current_shape_line": _shape_json(CURRENT_SHAPE),
            "fixed_shape_line": _shape_json(FIXED_SHAPE),
            "fixed_added_sentence": FIXED_SENTENCE,
        },
        "sample": [asdict(m) for m in sample],
        "summary": summarize(rows),
        "answers": rows,
    }
    slug = args.model.replace("/", "_")
    suffix = f"-{args.tag}" if args.tag else ""
    result = out_dir / f"card-probe-{slug}-{args.schema}{suffix}.json"
    result.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=1))
    print(f"wrote {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
