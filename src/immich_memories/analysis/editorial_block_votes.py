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
import math
import re
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from functools import partial
from typing import NamedTuple

from immich_memories.analysis.editorial_page_recovery import read_page_answer
from immich_memories.analysis.editorial_reader_concurrency import reader_map
from immich_memories.analysis.editorial_structure_json import _first_object
from immich_memories.analysis.strict_json import named_keys

logger = logging.getLogger(__name__)

BLOCK_SIZE = 12
BLOCK_CACHE_VERSION = "block-votes-v3-validated-labels"
# Rows whose block was asked in one order only. They answer a caller whose rule one order
# settles and nobody else: a reader of the whole-answer store never sees them.
ONE_ORDER_ROWS = "rows-one-order"

# Which rows one order's answer already decides, given the row and whether that order named
# it. A caller that passes none asks both orders of every block, as the vote always has.
Settled = Callable[[str, bool], bool]

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


def balanced_groups(items: Sequence[str], size: int = 12, minimum: int = 4) -> list[list[str]]:
    """Blocks of at most twelve, never leaving a block of one or two behind.

    The block vote cuts its items into fixed twelves. A reject-only vote whose last block holds a
    single row has no company to judge it against, and both orders then name that lone row.
    Rebalancing the same items into equal blocks keeps the production question and gives every
    row neighbours to be compared with.
    """
    items = list(items)
    if len(items) <= size:
        return [items] if items else []
    count = math.ceil(len(items) / size)
    if len(items) % size and len(items) % size < minimum:
        count = max(count, math.ceil(len(items) / (size - 1)))
    step = math.ceil(len(items) / count)
    return [items[index : index + step] for index in range(0, len(items), step)]


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
    obj, _tail = _first_object(raw)
    value, envelope = _answer_map(obj, answer_key, allowed)
    if isinstance(value, list):
        if not all(isinstance(v, str) for v in value):
            raise ValueError("vote lists must contain only offered labels")
        value = dict.fromkeys(value, "")
    if not isinstance(value, Mapping) or not allowed.issuperset(value):
        unoffered = set(value) - allowed if isinstance(value, Mapping) else set()
        raise ValueError(
            f"Return a {answer_key!r} mapping using only these exact labels: "
            + ", ".join(sorted(allowed))
            + "; context such as '(near home)' is not part of a label"
            + (f"; labels nobody offered: {named_keys(unoffered)}" if unoffered else "")
        )
    return (
        {k: " ".join(str(v).split()[:12]) for k, v in value.items()},
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
    prompts: dict[str, str],
    identity: str | None,
    max_tokens: int,
    answer_key: str,
    rows_version: str = "",
) -> str:
    """The name one asked question lives under.

    `rows_version` names what produced the rows rather than what is written in them: evidence a
    row carries can come from a bank of its own, and two producers of it ask different
    questions out of identical text. A caller that has no such producer leaves the key out, so
    its existing entries keep their names.
    """
    payload = {
        "version": BLOCK_CACHE_VERSION,
        "model": identity,
        "prompts": prompts,
        "max_tokens": max_tokens,
        "answer_key": answer_key,
    }
    if rows_version:
        payload["rows_version"] = rows_version
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
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
    picks: Callable[..., tuple[dict[str, str], str]] = _picks,
) -> dict[str, dict[str, str]]:
    votes: dict[str, dict[str, str]] = {}
    for order_name, order in orders:
        picked, envelope = read_page_answer(
            judge,
            stage=f"{stage}-{order_name}",
            prompt=prompts[order_name],
            max_tokens=max_tokens,
            read=partial(picks, answer_key=answer_key, allowed={label_of[x] for x in order}),
        )
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


def _banked_rows(
    bank: MutableMapping[str, dict] | None, row_key: Callable[[str], str] | None
) -> dict[str, dict] | None:
    """The per-row store inside the block bank, or None when rows are not banked.

    Block entries are keyed by a 64-hex digest, so the literal "rows" key cannot collide with
    one. The trade-off this buys: a banked row's vote was cast in one block's company and is
    reused in another's. The standing score is already kept per asset in `gate.scores` and read
    from there wherever a picture is weighed, so the bank now agrees with that model instead of
    with the twelve rows a picture happened to be asked beside.
    """
    if bank is None or row_key is None:
        return None
    rows = bank.setdefault("rows", {})
    return rows if isinstance(rows, dict) else None


def _row_names(
    items: Sequence[str],
    row_key: Callable[[str], str] | None,
    identity: str | None,
    max_tokens: int,
    answer_key: str,
    rows_version: str = "",
) -> dict[str, str]:
    """Each row's name in the bank: its own question, scoped by the model identity and the exact
    answer asked for, the way a block's cache key is. A row banked under one reader is not
    replayed for another."""
    if row_key is None:
        return {}
    return {
        x: _vote_cache_key({"row": row_key(x)}, identity, max_tokens, answer_key, rows_version)
        for x in items
    }


def _rows_from_bank(
    rows: Mapping[str, dict], names_of: Mapping[str, str]
) -> dict[str, tuple[int, str]]:
    """The votes the bank already holds for these rows, under each row's own name."""
    return {
        x: (int(row["votes"]), str(row.get("why", "")))
        for x, name in names_of.items()
        if isinstance(row := rows.get(name), Mapping) and "votes" in row
    }


def _one_order_rows(
    bank: MutableMapping[str, dict] | None, rows: dict[str, dict] | None, settled: Settled | None
) -> dict[str, dict] | None:
    """The store of rows answered in one order, when this caller can use such an answer."""
    if bank is None or rows is None or settled is None:
        return None
    partial = bank.setdefault(ONE_ORDER_ROWS, {})
    return partial if isinstance(partial, dict) else None


def _settled_from_bank(
    partial: Mapping[str, dict], names_of: Mapping[str, str], settled: Settled
) -> dict[str, tuple[int, str]]:
    """The one-order answers this caller's rule accepts as final, under each row's own name.

    A row whose first answer settled it for one film may matter more to another: there it is
    not taken from here, and is asked again in a block of its own company.
    """
    held = _rows_from_bank(partial, names_of)
    return {x: vote for x, vote in held.items() if settled(x, vote[0] > 0)}


def _pack_blocks(
    items: Sequence[str],
    *,
    stage: str,
    bank_key: Callable[[Sequence[str]], str],
    row_of: Callable[[str], str],
    prompt_of: Callable[[str], str],
    identity: str | None,
    max_tokens: int,
    answer_key: str,
    rows_version: str = "",
    balanced: bool = False,
) -> list[_Block]:
    """The rows still to ask, cut into twelves, each block with its two prompts and cache key.

    `balanced` spreads the rows over equal blocks instead, so none is left with one or two rows.
    """
    blocks = (
        balanced_groups(list(items))
        if balanced
        else [list(items[i : i + BLOCK_SIZE]) for i in range(0, len(items), BLOCK_SIZE)]
    )
    packed = []
    for index, block in enumerate(blocks):
        orders = _block_orders(block, bank_key(block))
        prompts = {name: prompt_of("\n".join(row_of(x) for x in order)) for name, order in orders}
        packed.append(
            _Block(
                block,
                _vote_cache_key(prompts, identity, max_tokens, answer_key, rows_version),
                f"{stage}-{index + 1}",
                orders,
                prompts,
            )
        )
    return packed


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
    row_key: Callable[[str], str] | None = None,
    rows_version: str = "",
    settled: Settled | None = None,
    balanced: bool = False,
    picks: Callable[..., tuple[dict[str, str], str]] = _picks,
) -> tuple[dict[str, tuple[int, str]], list[dict]]:
    """Votes per item (0, 1 or 2) with the first reason given, and one record per asked round.
    `row_of` renders the whole listing row including its label; `prompt_of` wraps a listing.
    `bank_key` seeds the established order; the separate cache key covers the exact two questions
    and their model identity. `row_key` names one row's own question: rows already answered under
    that name are taken from the bank and never packed into a block.

    With `settled`, a block is asked in its source order first and in its hashed order only when
    one of its rows is not settled by that first answer; such a row's count is then out of one.
    `picks` reads one order's answer into the labels it named, with their reasons.
    """
    identity = _judge_model_identity(judge, model_identity) if bank is not None else None
    reusable = bank if identity is not None else None
    rows = _banked_rows(reusable, row_key)
    partial = _one_order_rows(reusable, rows, settled)
    names_of = (
        _row_names(items, row_key, identity, max_tokens, answer_key, rows_version)
        if rows is not None
        else {}
    )
    banked = (
        _settled_from_bank(partial, names_of, settled) if partial and settled is not None else {}
    ) | (_rows_from_bank(rows, names_of) if rows else {})
    pending = _pack_blocks(
        [x for x in items if x not in banked],
        stage=stage,
        bank_key=bank_key,
        row_of=row_of,
        prompt_of=prompt_of,
        identity=identity,
        max_tokens=max_tokens,
        answer_key=answer_key,
        rows_version=rows_version,
        balanced=balanced,
    )

    def ask(child: object, asked: _Block, orders, votes) -> dict[str, dict[str, str]]:
        missing = tuple(order for order in orders if order[0] not in votes)
        if not missing:
            return {}
        return _ask_orders(
            child,
            stage=asked.stage,
            orders=missing,
            prompts=asked.prompts,
            answer_key=answer_key,
            label_of=label_of,
            max_tokens=max_tokens,
            picks=picks,
        )

    def read(child: object, asked: _Block) -> dict[str, dict[str, str]]:
        votes = dict(reusable.get(asked.key) or {}) if reusable is not None else {}
        votes |= ask(child, asked, asked.orders if settled is None else asked.orders[:1], votes)
        if settled is not None and not _decided(asked, votes, label_of, settled):
            votes |= ask(child, asked, asked.orders[1:], votes)
        return votes

    votes_of, rounds = _harvest(
        pending,
        reader_map(judge, read, pending),
        label_of=label_of,
        reusable=reusable,
        stores=(rows, partial),
        names_of=names_of,
        save=save,
    )
    return banked | votes_of, rounds


def _decided(
    asked: _Block, votes: Mapping[str, dict[str, str]], label_of: Mapping[str, str], settled
) -> bool:
    """Whether the block's first order has already answered everything its rows need."""
    first = asked.orders[0][0]
    if first not in votes or f"{first}_failed" in votes:
        return False
    return all(settled(x, label_of[x] in votes[first]) for x in asked.items)


def _harvest(
    pending: Sequence[_Block],
    answers: Sequence[dict[str, dict[str, str]]],
    *,
    label_of: Mapping[str, str],
    reusable: MutableMapping[str, dict] | None,
    stores: tuple[dict[str, dict] | None, dict[str, dict] | None],
    names_of: Mapping[str, str],
    save: Callable[[], None] | None,
) -> tuple[dict[str, tuple[int, str]], list[dict]]:
    """Tally each answered block, write what it settled back into the bank, and record the round.

    A row goes to the whole-answer store only when every order of its block answered; a row one
    order settled goes to the one-order store, which only a caller with the same rule reads.
    """
    votes_of: dict[str, tuple[int, str]] = {}
    rounds: list[dict] = []
    for asked, votes in zip(pending, answers, strict=True):
        fresh = False
        if reusable is not None and reusable.get(asked.key) != votes:
            reusable[asked.key] = votes
            fresh = True
        names = [name for name, _o in asked.orders]
        tallied = _tally(asked.items, label_of, votes, names)
        votes_of.update(tallied)
        answered = sum(1 for name in names if name in votes and f"{name}_failed" not in votes)
        store = stores[0] if answered == len(names) else stores[1]
        if store is not None:
            store.update({names_of[x]: {"votes": n, "why": w} for x, (n, w) in tallied.items()})
        if save is not None and (fresh or store is not None):
            save()
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
        if name in votes or f"{name}_failed" in votes
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
        row_of=lambda f: f"{label_of[f]}: {'(near home) ' if near_home(f) else ''}{text_of(f)}",
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


_OFFERED_LABEL = re.compile(r"^(P\d+): ", re.MULTILINE)


def weak_example(listing: str) -> str:
    """The answer shape a reject-only vote is shown, named with the block's own first labels.

    A fixed example of P03 and P07 was copied as the answer by a reader shown P97 to P108,
    three times over, and a vote naming labels it was not offered ends the film. The example
    now only ever names labels the block holds: its first two, or its one.
    """
    labels = _OFFERED_LABEL.findall(listing)[:2]
    return json.dumps({"weak": dict.fromkeys(labels, "why")}, separators=(",", ":"))
