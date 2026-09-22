"""One closed question over a finished cut: which of these shots adds nothing to this film?

The production block vote asks it, reject-only, in both orders, exactly as the standing gate is
asked. What differs is the criterion and the company: every row is a shot the film currently
holds, the period's thesis sits above the block, and the answer is read against the whole cut
rather than against one story.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from typing import Any

from immich_memories.analysis.editorial_block_votes import vote_blocks

THESIS_FIT_VERSION = "thesis-fit-v2"
THESIS_FIT_CRITERION = (
    "Name the shots that add nothing to THIS film: filler, a lone everyday object or appliance, "
    "a meaningless interior, an accidental frame, or a view that merely repeats its neighbour "
    "without adding anything. Judge each shot against the film's thesis above: a shot that shows "
    "something the thesis is about belongs, however plain it looks, and a well-made picture that "
    "carries none of it is filler. A factual notable-record note can explain why an "
    "ordinary-looking detail belongs; accept that meaning only when the supplied evidence "
    "supports it. A row that names a video, or a Live Photo whose motion plays, is footage: judge "
    "what happens across it, not whether one still frame would make a good photograph. "
    "Name only the weak ones; say for each in at most 12 words why."
)
HELD_BY_THE_OWNER = "the owner starred it or the catalogue records it; the vote does not move it"


def balanced_groups(items: Sequence[str], size: int = 12, minimum: int = 4) -> list[list[str]]:
    """Blocks of at most twelve, never leaving a block of one or two behind.

    The block vote cuts its items into fixed twelves. A reject-only vote whose last block holds a
    single row has no company to judge it against, and both orders then name that lone row.
    Rebalancing the same items into equal blocks keeps the production question and gives every
    row neighbours to be compared with.
    """
    items = list(items)
    if len(items) <= size:
        return [items]
    count = math.ceil(len(items) / size)
    if len(items) % size and len(items) % size < minimum:
        count = max(count, math.ceil(len(items) / (size - 1)))
    step = math.ceil(len(items) / count)
    return [items[index : index + step] for index in range(0, len(items), step)]


def judge_thesis_fit(
    judge,
    *,
    pictures: Sequence[str],
    line_of: Callable[[str], str],
    thesis: str,
    contract: str,
    story_of: Callable[[str], str] = lambda _asset: "",
    bank: MutableMapping[str, dict] | None = None,
    save: Callable[[], None] | None = None,
) -> tuple[dict[str, tuple[int, str]], list[dict]]:
    """Does each shot earn its place in THIS film? Named by both orders is a firm no.

    The thesis sits inside the question and therefore inside every row's bank key, so a film
    built on a different account of the period never replays these answers.
    """
    label_of = {asset: f"P{number + 1:02d}" for number, asset in enumerate(pictures)}

    def prompt_of(listing: str) -> str:
        # The block is last; everything above it is byte-identical for the run.
        return (
            f"{contract}\n\nTHE THESIS THIS FILM IS BUILT ON\n{thesis}\n\n"
            "Below are the shots currently in this film, one line each: when each was taken and "
            f"what it shows. Text only.\n\n{THESIS_FIT_CRITERION}\n\n"
            'Answer with one JSON object only, on one line: {"weak":{"P03":"why","P07":"why"}}'
            f"\n\nSHOTS\n{listing}"
        )

    def row_of(asset: str) -> str:
        # The row already opens with the shot's date and time and the cut is offered in its own
        # chronological order; the story alias is added so a repeat of a neighbour is visible.
        return f"{label_of[asset]}: [{story_of(asset) or '-'}] {line_of(asset)}"

    question = THESIS_FIT_VERSION + "|" + prompt_of("") + "|"
    votes, rounds = vote_blocks(
        judge,
        stage="thesis-fit",
        items=list(pictures),
        label_of=label_of,
        row_of=row_of,
        prompt_of=prompt_of,
        answer_key="weak",
        bank_key=lambda block: hashlib.sha256(
            (THESIS_FIT_VERSION + "|" + "|".join(line_of(a) for a in block)).encode()
        ).hexdigest(),
        bank=bank,
        save=save,
        max_tokens=700,
        row_key=lambda asset: hashlib.sha256((question + row_of(asset)).encode()).hexdigest(),
        rows_version=THESIS_FIT_VERSION,
    )
    if any(record["envelope"] in {"failed", "unreadable"} for record in rounds):
        raise ValueError("Thesis fit is incomplete; an unanswered vote is not a verdict")
    return votes, rounds


def vote_thesis_fit(judge, *, pictures: Sequence[str], **asked) -> tuple[dict, list[dict]]:
    """The thesis-fit vote over balanced blocks; votes and rounds merged across them."""
    votes: dict[str, tuple[int, str]] = {}
    rounds: list[dict] = []
    for group in balanced_groups(list(pictures)):
        block_votes, block_rounds = judge_thesis_fit(judge, pictures=group, **asked)
        votes.update(block_votes)
        rounds.extend(block_rounds)
    return votes, rounds


def classify_fit(
    cut: Sequence[Mapping[str, Any]], votes: Mapping[str, tuple[int, str]]
) -> dict[str, dict[str, Any]]:
    """Bad, weak or kept.

    A star the owner put on a picture, or a picture carrying a banked notable record, leaves the
    cut only when a gate refuses it. One order's doubt does not move it and neither does both
    orders': it keeps its place, no replacement slot is ever opened for it, and what the vote
    said is still written down so a run can be asked how often this fired.

    Everything else: an unprotected shot named by both orders is `bad` and leaves; by one order
    it is `weak` and is offered a same-story replacement whose place it gives up only once a
    candidate has passed.
    """
    verdicts = {}
    for shot in cut:
        asset = shot["asset_id"]
        named, why = votes.get(asset, (0, ""))
        protected = bool(shot.get("favourite") or shot.get("notable_record"))
        verdicts[asset] = {
            "state": _state(named, protected=protected),
            "named_by": named,
            "why": why,
            "protected": protected,
            "held_by": HELD_BY_THE_OWNER if protected and named else "",
            "rule": "thesis-fit vote" if named else "",
        }
    return verdicts


def _state(named: int, *, protected: bool) -> str:
    if protected or named == 0:
        return "kept"
    return "bad" if named >= 2 else "weak"
