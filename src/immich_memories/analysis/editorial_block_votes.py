"""Two-order block votes: the judgment shape that held across the matrix.

A block of at most twelve rows is shown twice, in source order and in a hashed order. A row
picked both times is a firm yes, once is a maybe, never is a no. Position bias cancels and the
model never sees a list long enough to lose its place. Two questions are asked this way: the
memory-worthy gate per happening (the v44 form, restored verbatim) and the picture-standing
gate per candidate carrier.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from typing import NamedTuple

from immich_memories.analysis.editorial_reader_concurrency import reader_map
from immich_memories.analysis.editorial_structure_json import _first_object

logger = logging.getLogger(__name__)

BLOCK_SIZE = 12
BLOCK_CACHE_VERSION = "block-votes-v2-exact-model-question"

WORTH_PROMPT_VERSION = "memory-worthy-v2-contract"
WORTH_CRITERION_V44 = (
    "Pick the happenings worth making a memory of. Judge what HAPPENED, not how many pictures there are. "
    "A quiet happening with something remarkable in it beats a busy ordinary one: a hundred pictures of a normal "
    "afternoon at home is not memory-worthy, and six pictures of something that happened once is. Ordinary domestic "
    "life, however well photographed, is the baseline this is measured against. If a happening looks ordinary, "
    "leave it out. Say for each pick in at most 12 words what stands out."
)
WORTH_SUBJECT_CRITERION = (
    "Pick the happenings that belong in THIS memory: the ones that show its subject, a stage of it, its "
    "beginning state or its result. Judge each happening by whether it shows the subject, not by how lively "
    "or how well photographed it is: an ordinary-looking picture that shows the subject is worthy, and a lively "
    "day that does not show it is background, however many pictures it has. Say for each pick in at most 12 "
    "words what it shows of the subject.\nSubject: {subject}"
)

STANDING_PROMPT_VERSION = "picture-stands-v3-reject-only"
STANDING_CRITERION = (
    "Name the pictures that do NOT stand by themselves: pictures nobody would show on their own because they show "
    "nothing worth showing. A close-up of a body part or an ailment, a screen, a document, a lone everyday object "
    "with nobody in it, an empty room, a test shot, an accidental or unflattering frame. Judge what a picture shows, "
    "not whether its subject is comfortable: people in a real moment stand whatever the setting, and so does a "
    "place worth seeing. Name only the weak ones; say for each in at most 12 words why."
)


TRIP_WORTH_CRITERION = (
    "Pick the parts that tell THIS TRIP as a journey: distinct legs and places, visits and activities, "
    "local architecture, landscapes, food and atmosphere that establish where the travellers went "
    "and what they experienced. Exploring somewhere is the subject of a trip; it does not need to "
    "be an exceptional life event to belong. Use the literal scene and known context, not an invented "
    "episode milestone. An interchangeable portrait or uninformative transit frame does not earn a "
    "place merely because it was taken away from home. Repeated views without a distinct contribution "
    "remain background. Picture count and favourites alone do not establish worthiness. Say for each "
    "pick in at most 12 words what it contributes to the journey."
)


def worth_criterion_v44(product: str, subject: str | None) -> tuple[str, str]:
    """The question the v44 gate asked, and its bank marker. Only a custom memory with a bound subject
    changes the question; every other product is judged against ordinary domestic life."""
    if product == "custom" and subject:
        return WORTH_SUBJECT_CRITERION.format(subject=subject), "subject-v1"
    if product == "trip":
        # Exploring somewhere is the subject of a trip; the general criterion's baseline ("ordinary
        # domestic life") read ten of thirteen travel days as background and the film came out at a fifth.
        return TRIP_WORTH_CRITERION, "trip-journey-v1"
    return WORTH_CRITERION_V44, ""


def _answer_map(obj: object, answer_key: str, allowed: set[str]) -> tuple[object, str]:
    """The mapping the contract asked for and the envelope it came in, whether or not the model
    kept its wrapper key.

    The instruction to wrap the picks in one key sits at the end of a nine-thousand-character
    prompt, and a reader that loses it returns the same picks bare. Measured on February 2024:
    nine of glm-5.3-flash's ten worthy replies were flat, the gate read them as "nothing
    remarkable", and the run said nothing. A bare object is read as the answer only when every
    key in it is a label this very block offered, which an answer to another question cannot be.
    """
    if not isinstance(obj, Mapping):
        return None, "unreadable"
    if answer_key in obj:
        return obj[answer_key], "wrapped"
    return (obj, "flat") if obj and allowed.issuperset(obj) else (None, "unreadable")


def _picks(raw: str, answer_key: str, allowed: set[str]) -> tuple[dict[str, str], str]:
    """The labels named with their reasons, and the envelope the answer arrived in."""
    try:
        obj, _tail = _first_object(raw)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, AttributeError):
        return {}, "unreadable"
    value, envelope = _answer_map(obj, answer_key, allowed)
    if isinstance(value, list):
        value = dict.fromkeys((v for v in value if isinstance(v, str)), "")
    if not isinstance(value, Mapping):
        return {}, "unreadable"
    return (
        {k: " ".join(str(v).split()[:12]) for k, v in value.items() if k in allowed},
        envelope,
    )


def _judge_model_identity(judge, supplied: str | None) -> str | None:
    """Only reuse a bank when the adapter's effective model settings are known."""
    if supplied is not None:
        if not supplied.strip():
            raise ValueError("vote bank model identity cannot be empty")
        return supplied
    config = getattr(getattr(judge, "config", None), "llm", None)
    if config is None:
        return None
    from immich_memories.analysis.llm_providers import resolved_llm_config
    from immich_memories.analysis.llm_text_identity import text_model_identity

    return text_model_identity(resolved_llm_config(config), thinking=False)


def _block_orders(block: list[str], seed: str) -> tuple[tuple[str, list[str]], ...]:
    """The established pair: source order, then an order hashed with the block's own seed."""
    return (
        ("source", block),
        ("hashed", sorted(block, key=lambda x: hashlib.sha256((x + seed).encode()).hexdigest())),
    )


def _vote_cache_key(
    prompts: dict[str, str], identity: str | None, max_tokens: int, answer_key: str
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "version": BLOCK_CACHE_VERSION,
                "model": identity,
                "prompts": prompts,
                "max_tokens": max_tokens,
                "answer_key": answer_key,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _ask_orders(
    judge,
    *,
    stage: str,
    orders: tuple[tuple[str, list[str]], ...],
    prompts: Mapping[str, str],
    answer_key: str,
    label_of: Mapping[str, str],
    max_tokens: int,
) -> dict[str, dict[str, str]]:
    votes: dict[str, dict[str, str]] = {}
    for order_name, order in orders:
        try:
            raw = judge.ask(f"{stage}-{order_name}", prompts[order_name], max_tokens=max_tokens)
        except ValueError as exc:  # TextCompletionFailure: this order abstains, the other votes
            votes[order_name] = {}
            votes[f"{order_name}_failed"] = {"__error__": str(exc)[:200]}
            continue
        picked, envelope = _picks(raw, answer_key, {label_of[x] for x in order})
        votes[order_name] = picked
        votes[f"{order_name}_envelope"] = {"shape": envelope}
    return votes


class _Block(NamedTuple):
    """One block of twelve, its two orders, and the cache key covering both prompts."""

    items: list[str]
    key: str
    stage: str
    orders: tuple[tuple[str, list[str]], ...]
    prompts: dict[str, str]


def vote_blocks(
    judge,
    *,
    stage: str,
    items: Sequence[str],
    label_of: Mapping[str, str],
    row_of: Callable[[str], str],
    prompt_of: Callable[[str], str],
    answer_key: str,
    bank_key: Callable[[Sequence[str]], str],
    bank: MutableMapping[str, dict] | None = None,
    save: Callable[[], None] | None = None,
    max_tokens: int = 400,
    model_identity: str | None = None,
) -> tuple[dict[str, tuple[int, str]], list[dict]]:
    """Votes per item (0, 1 or 2) with the first reason given, and one record per asked round.
    `row_of` renders the whole listing row including its label; `prompt_of` wraps a listing.
    `bank_key` seeds the established order; the separate cache key covers the exact two questions
    and their model identity."""
    votes_of: dict[str, tuple[int, str]] = {}
    rounds: list[dict] = []
    identity = _judge_model_identity(judge, model_identity) if bank is not None else None
    reusable = bank if identity is not None else None
    blocks = [list(items[i : i + BLOCK_SIZE]) for i in range(0, len(items), BLOCK_SIZE)]
    pending = []
    for bi, block in enumerate(blocks):
        orders = _block_orders(block, bank_key(block))
        prompts = {name: prompt_of("\n".join(row_of(x) for x in order)) for name, order in orders}
        pending.append(
            _Block(
                block,
                _vote_cache_key(prompts, identity, max_tokens, answer_key),
                f"{stage}-{bi + 1}",
                orders,
                prompts,
            )
        )

    def read(child: object, asked: _Block) -> dict[str, dict[str, str]]:
        if reusable is not None and asked.key in reusable:
            return reusable[asked.key]
        return _ask_orders(
            child,
            stage=asked.stage,
            orders=asked.orders,
            prompts=asked.prompts,
            answer_key=answer_key,
            label_of=label_of,
            max_tokens=max_tokens,
        )

    for asked, votes in zip(pending, reader_map(judge, read, pending), strict=True):
        if reusable is not None and asked.key not in reusable:
            reusable[asked.key] = votes
            if save is not None:
                save()
        names = [name for name, _order in asked.orders]
        votes_of.update(_tally(asked.items, label_of, votes, names))
        rounds.extend(_round_records(asked.stage, asked.orders, votes))
    return votes_of, rounds


def _round_records(
    stage: str,
    orders: tuple[tuple[str, list[str]], ...],
    votes: Mapping[str, dict[str, str]],
) -> list[dict]:
    """What each order of one block was offered, what it named, and in which envelope.

    A bank written before the envelope was recorded replays as "banked": the counts still say
    whether the round read anything, which is the question a silent zero has to answer.
    """
    return [
        {
            "round": f"{stage}-{name}",
            "offered": len(order),
            "picked": len(votes.get(name, {})),
            "envelope": (
                "failed"
                if f"{name}_failed" in votes
                else votes.get(f"{name}_envelope", {}).get("shape", "banked")
            ),
        }
        for name, order in orders
    ]


def _tally(
    block: list[str],
    label_of: Mapping[str, str],
    votes: Mapping[str, dict[str, str]],
    names: Sequence[str],
) -> dict[str, tuple[int, str]]:
    """A row named by both orders is a firm yes, by one a maybe; a failed order does not vote."""
    seen = [votes[n] for n in names if n in votes and f"{n}_failed" not in votes]
    return {
        x: (
            sum(1 for o in seen if label_of[x] in o),
            next((o[label_of[x]] for o in seen if label_of[x] in o), ""),
        )
        for x in block
    }


def judge_worthiness(
    judge,
    *,
    happenings: Sequence[str],
    label_of: Mapping[str, str],
    text_of: Callable[[str], str],
    near_home: Callable[[str], bool | None],
    contract: str,
    contract_key: str,
    criterion: str,
    marker: str,
    period_label: str,
    bank: MutableMapping[str, dict] | None = None,
    save: Callable[[], None] | None = None,
    model_identity: str | None = None,
) -> tuple[dict[str, int], dict[str, str], list[dict]]:
    """The v44 memory-worthy gate: tier 0 remarkable, 1 maybe, 2 background, per happening.
    `text_of` is the anchor row without its label (day, count, what the pictures show).
    The third return is one record per asked round, so a gate that read nothing says so."""

    def prompt_of(listing: str) -> str:
        # The block is last and everything above it is byte-identical for the run, so a
        # server reusing a prefix reads the contract and the criterion once per block pair
        # instead of once per call (#981).
        return (
            f"{contract}\n\nBelow are happenings from one period ({period_label}), each with the day, how many "
            f"pictures, and what the pictures show. Text only.\n\n{criterion}\n\n"
            'Return one JSON object with "worthy": a mapping from each picked happening label '
            "to what stands out in at most 12 words. Use only labels from the offered happenings. "
            "Return an empty mapping if none qualify."
            f"\n\nHAPPENINGS\n{listing}"
        )

    def bank_key(block: Sequence[str]) -> str:
        return hashlib.sha256(
            (
                WORTH_PROMPT_VERSION
                + ("|" + marker if marker else "")
                + "|"
                + contract_key
                + "|"
                + "|".join(f"{label_of[f]}: {text_of(f)}" for f in block)
            ).encode()
        ).hexdigest()

    votes, rounds = vote_blocks(
        judge,
        stage="worthy",
        items=happenings,
        label_of=label_of,
        row_of=lambda f: f"{label_of[f]}{' (near home)' if near_home(f) else ''}: {text_of(f)}",
        prompt_of=prompt_of,
        answer_key="worthy",
        bank_key=bank_key,
        bank=bank,
        save=save,
        model_identity=model_identity,
    )
    _warn_on_empty_rounds(rounds)
    tier = {f: (0 if n == 2 else 1 if n == 1 else 2) for f, (n, _r) in votes.items()}
    reason = {f: r for f, (_n, r) in votes.items()}
    return tier, reason, rounds


def _warn_on_empty_rounds(rounds: Sequence[Mapping[str, object]]) -> None:
    """A worthy round that names nobody is either a real "none of these" or a reply the reader
    could not open, and the two are indistinguishable downstream. Say which, out loud, per round:
    the gate's silence is the failure mode that let a whole month grade background."""
    for entry in rounds:
        if entry["picked"] or entry["envelope"] == "failed":
            continue
        logger.warning(
            "worthy round %s read 0 of %s offered happenings (envelope=%s)",
            entry["round"],
            entry["offered"],
            entry["envelope"],
        )


def judge_standing(
    judge,
    *,
    pictures: Sequence[str],
    line_of: Callable[[str], str],
    contract: str,
    period_label: str,
    bank: MutableMapping[str, dict] | None = None,
    save: Callable[[], None] | None = None,
    model_identity: str | None = None,
) -> dict[str, tuple[int, str]]:
    """Does each picture stand by itself? Reject-only: the model names the weak ones. Score per asset
    id: 2 = named by neither order, 1 = by one, 0 = by both."""
    label_of = {a: f"P{i + 1:02d}" for i, a in enumerate(pictures)}

    def prompt_of(listing: str) -> str:
        # Pictures last: see judge_worthiness.prompt_of (#981).
        return (
            f"{contract}\n\nBelow are single pictures from one period ({period_label}), one line each: when it "
            f"was taken and what it shows. Text only.\n\n{STANDING_CRITERION}\n\n"
            'Answer with one JSON object only, on one line: {"weak":{"P03":"why","P07":"why"}}'
            f"\n\nPICTURES\n{listing}"
        )

    def bank_key(block: Sequence[str]) -> str:
        return hashlib.sha256(
            (
                STANDING_PROMPT_VERSION
                + "|"
                + contract[:64]
                + "|"
                + "|".join(line_of(a) for a in block)
            ).encode()
        ).hexdigest()

    rejections, _rounds = vote_blocks(
        judge,
        stage="standing",
        items=pictures,
        label_of=label_of,
        row_of=lambda a: f"{label_of[a]}: {line_of(a)}",
        prompt_of=prompt_of,
        answer_key="weak",
        bank_key=bank_key,
        bank=bank,
        save=save,
        max_tokens=700,
        model_identity=model_identity,
    )
    return {a: (2 - n, why) for a, (n, why) in rejections.items()}
