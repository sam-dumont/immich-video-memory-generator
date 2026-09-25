"""One closed question over a finished cut: which of these shots adds nothing to this film?

The production block vote asks it, reject-only, in both orders, exactly as the standing gate is
asked. What differs is the criterion and the company: every row is a shot the film currently
holds, the period's thesis sits above the block, and the answer is read against the whole cut
rather than against one story.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Collection, Mapping, MutableMapping, Sequence
from typing import Any

from immich_memories.analysis.editorial_block_votes import (
    balanced_groups,
    vote_blocks,
    weak_example,
)
from immich_memories.analysis.editorial_shot_kinds import TEXTURE
from immich_memories.analysis.editorial_story_replies import OF_THE_SUBJECT, close_family_on

THESIS_FIT_VERSION = "thesis-fit-v4-owner-relations"
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
# The vote read a partner in a month centred on a newborn as "unrelated to the main subject" and
# removed her only shot: a relation on a line said nothing about whose relation it was.
WHOSE_FILM = (
    "This is the library owner's film. Each person on a shot is named with their relation to "
    "the owner. The owner's close family (partner, child, parent) is part of the owner's life in "
    "every period: a shot of one of them is never unrelated to this film, even when the thesis "
    "centres on someone else."
)
# Said only in a block that holds one: a month film's question stays word for word what it was.
SUBJECTS_FAMILY = (
    "This film is about someone, and a shot may also name the close family of the film's "
    "subject (their partner, child, parent), though the owner is not related to them. A shot of "
    "one of them is never unrelated to this film either."
)
NO_SUBJECT = "the owner's own life over this period, and the people in it"

CloseFamily = Callable[[str], Mapping[str, str]]


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
    settled: Callable[[str, bool], bool] | None = None,
    subject: str = "",
    close_family: CloseFamily = close_family_on,
) -> tuple[dict[str, tuple[int, str]], list[dict]]:
    """Does each shot earn its place in THIS film? Named by both orders is a firm no.

    The thesis sits inside the question and therefore inside the block's bank key, so a film
    built on a different account of the period never replays these answers.

    Banked by the block, never by the row. This vote judges a shot against the company it is
    actually in, so a row's verdict cannot be lifted out of that company: with row reuse a
    second run re-packs only the rows the bank does not hold, and a leftover row is then asked
    on its own -- which a reject-only vote answers by naming it. Measured on June 2023, where
    the second run asked one shot alone and moved two of the film's ten.
    """
    label_of = {asset: f"P{number + 1:02d}" for number, asset in enumerate(pictures)}
    note_of = {asset: _family_note(close_family(line_of(asset))) for asset in pictures}
    # Only a note naming the subject's family enters the key, so a month film replays its bank.
    theirs = {asset: note for asset, note in note_of.items() if OF_THE_SUBJECT in note}
    whose = f"{WHOSE_FILM} {SUBJECTS_FAMILY}" if theirs else WHOSE_FILM

    def prompt_of(listing: str) -> str:
        # The block is last; everything above it is byte-identical for the run.
        return (
            f"{contract}\n\nTHE THESIS THIS FILM IS BUILT ON\n{thesis}\n\n"
            f"THE FILM'S SUBJECT\n{subject or NO_SUBJECT}\n\n{whose}\n\n"
            "Below are the shots currently in this film, one line each: when each was taken and "
            f"what it shows. Text only.\n\n{THESIS_FIT_CRITERION}\n\n"
            f"Answer with one JSON object only, on one line: {weak_example(listing)}"
            f"\n\nSHOTS\n{listing}"
        )

    def row_of(asset: str) -> str:
        # The row already opens with the shot's date and time and the cut is offered in its own
        # chronological order; the story alias is added so a repeat of a neighbour is visible.
        return f"{label_of[asset]}: [{story_of(asset) or '-'}]{note_of[asset]} {line_of(asset)}"

    votes, rounds = vote_blocks(
        judge,
        stage="thesis-fit",
        items=list(pictures),
        label_of=label_of,
        row_of=row_of,
        prompt_of=prompt_of,
        answer_key="weak",
        bank_key=lambda block: hashlib.sha256(
            (
                THESIS_FIT_VERSION + "|" + "|".join(line_of(a) + theirs.get(a, "") for a in block)
            ).encode()
        ).hexdigest(),
        bank=bank,
        save=save,
        max_tokens=700,
        rows_version=THESIS_FIT_VERSION,
        settled=settled,
    )
    if any(record["envelope"] in {"failed", "unreadable"} for record in rounds):
        raise ValueError("Thesis fit is incomplete; an unanswered vote is not a verdict")
    return votes, rounds


def vote_thesis_fit(
    judge, *, pictures: Sequence[str], protected: Collection[str] = (), **asked
) -> tuple[dict, list[dict]]:
    """The thesis-fit vote over balanced blocks; votes and rounds merged across them.

    Each block is asked in its source order, and in its hashed order only when the first named
    a shot the vote may move: one order's doubt is never a verdict (the reader's answers flip
    with the order of the rows), and an unnamed shot or a protected one is already decided. A
    block whose every shot the owner or the catalogue protects is not asked at all.
    """
    held = frozenset(protected)
    votes: dict[str, tuple[int, str]] = {}
    rounds: list[dict] = []
    for group in balanced_groups(list(pictures)):
        if held.issuperset(group):
            continue
        block_votes, block_rounds = judge_thesis_fit(
            judge,
            pictures=group,
            settled=lambda asset, named: asset in held or not named,
            **asked,
        )
        votes.update(block_votes)
        rounds.extend(block_rounds)
    return votes, rounds


def _family_note(close: Mapping[str, str]) -> str:
    relations = list(dict.fromkeys(close.values()))
    owners = [r for r in relations if not r.endswith(OF_THE_SUBJECT)]
    theirs = [r.removesuffix(OF_THE_SUBJECT) for r in relations if r.endswith(OF_THE_SUBJECT)]
    notes = [
        f"{whose} close family: {', '.join(found)}"
        for whose, found in (("the owner's", owners), ("the film's subject's", theirs))
        if found
    ]
    return f" ({'; '.join(notes)})" if notes else ""


def _whose(relation: str) -> str:
    if relation.endswith(OF_THE_SUBJECT):
        return f"the film's subject's {relation.removesuffix(OF_THE_SUBJECT)}"
    return f"the owner's {relation}"


def sole_family_shots(
    cut: Sequence[Mapping[str, Any]],
    line_of: Callable[[str], str],
    close_family: CloseFamily = close_family_on,
) -> dict[str, str]:
    """The shots that are the only one in the cut of some close family member, with the relation.

    Such a shot is how that person is in the film at all, so a vote alone never removes it; a gate
    still can. `close_family` says who counts: in a film about people, the subject's own family
    too. The names only tell two people of the same relation apart inside this call.
    """
    shots_of: dict[str, list[str]] = {}
    relation_of: dict[str, str] = {}
    for shot in cut:
        for name, relation in close_family(line_of(shot["asset_id"])).items():
            shots_of.setdefault(name, []).append(shot["asset_id"])
            relation_of[name] = relation
    sole: dict[str, str] = {}
    for name, assets in shots_of.items():
        if len(assets) == 1:
            sole.setdefault(assets[0], relation_of[name])
    return sole


def sole_era_shots(
    cut: Sequence[Mapping[str, Any]], era_of: Callable[[str], str | None] | None
) -> dict[str, str]:
    """The shots that are the only one in the cut of their partition (a year of a lifetime film).

    A film that promises every partition a voice (`voice_per_partition`) got that voice from the
    draft; a vote alone never takes it away, the same way it never takes a close family member's
    only shot. `era_of` maps a capture time to its partition, None for a film with no such promise.
    """
    if era_of is None:
        return {}
    shots_of: dict[str, list[str]] = {}
    for shot in cut:
        if (era := era_of(str(shot["taken"]))) is not None:
            shots_of.setdefault(era, []).append(shot["asset_id"])
    return {assets[0]: era for era, assets in shots_of.items() if len(assets) == 1}


def sole_texture_shots(
    cut: Sequence[Mapping[str, Any]], kind_of: Callable[[str], str | None] | None
) -> dict[str, str]:
    """Each story's only shot in the cut that is not a portrait (a place, a crowd, an event).

    The vote reads a portrait as the film's subject and a place or a race as filler, so left to
    itself it turns a story into posed portraits. It never removes the one shot that keeps a
    story from being only that; a gate still can. Maps the shot to its story.
    """
    if kind_of is None:
        return {}
    texture_of: dict[str, list[str]] = {}
    for shot in cut:
        if kind_of(shot["asset_id"]) == TEXTURE:
            story = str(shot.get("story_episode") or shot["asset_id"])
            texture_of.setdefault(story, []).append(shot["asset_id"])
    return {assets[0]: story for story, assets in texture_of.items() if len(assets) == 1}


def keep_every_voice(
    cut: Sequence[Mapping[str, Any]],
    verdicts: dict[str, dict[str, Any]],
    era_of: Callable[[str], str | None] | None,
) -> dict[str, dict[str, Any]]:
    """The verdicts with one shot kept in every partition whose every shot the vote named.

    Two shots of one year are neither of them its only shot, so `sole_era_shots` holds neither;
    named together, both would leave and the year would lose the voice the draft gave it. The
    one kept is the one the vote doubted least, the earlier on a tie.
    """
    if era_of is None:
        return verdicts
    shots_of: dict[str, list[Mapping[str, Any]]] = {}
    for shot in cut:
        if (era := era_of(str(shot["taken"]))) is not None:
            shots_of.setdefault(era, []).append(shot)
    for era, shots in shots_of.items():
        if any(verdicts[s["asset_id"]]["state"] != "bad" for s in shots):
            continue
        spared = min(shots, key=lambda s: (verdicts[s["asset_id"]]["named_by"], str(s["taken"])))
        verdicts[spared["asset_id"]] |= {
            "state": "kept",
            "protected": True,
            "held_by": f"the last shot of {era} in the film; the vote does not move it",
        }
    return verdicts


def is_protected(shot: Mapping[str, Any]) -> bool:
    """A star the owner put on it, or a record the catalogue holds: no vote moves the shot."""
    return bool(shot.get("favourite") or shot.get("notable_record"))


def classify_fit(
    cut: Sequence[Mapping[str, Any]],
    votes: Mapping[str, tuple[int, str]],
    family_held: Mapping[str, str] | None = None,
    era_held: Mapping[str, str] | None = None,
    texture_held: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Bad, weak or kept.

    A star the owner put on a picture, or a picture carrying a banked notable record, leaves the
    cut only when a gate refuses it. One order's doubt does not move it and neither does both
    orders': it keeps its place, no replacement slot is ever opened for it, and what the vote
    said is still written down so a run can be asked how often this fired.

    Everything else: an unprotected shot named by both orders is `bad` and leaves; by one order
    it is `weak` and is offered a same-story replacement whose place it gives up only once a
    candidate has passed. `family_held` maps a close family member's only shot to their relation:
    it is held the same way, and so is a partition's only shot (`era_held`, shot to partition)
    and a story's only shot that is not a portrait (`texture_held`, shot to story).
    """
    held = family_held or {}
    eras = dict(era_held or {})
    textures = texture_held or {}
    verdicts = {}
    for shot in cut:
        asset = shot["asset_id"]
        named, why = votes.get(asset, (0, ""))
        protected = is_protected(shot) or asset in held or asset in eras or asset in textures
        verdicts[asset] = {
            "state": _state(named, protected=protected),
            "named_by": named,
            "why": why,
            "protected": protected,
            "held_by": _held_by(shot, held, eras) if protected and named else "",
            "rule": "thesis-fit vote" if named else "",
        }
    return verdicts


def _held_by(
    shot: Mapping[str, Any], family_held: Mapping[str, str], era_held: Mapping[str, str]
) -> str:
    if is_protected(shot):
        return HELD_BY_THE_OWNER
    if shot["asset_id"] in era_held:
        return (
            f"the only shot of {era_held[shot['asset_id']]} in the film; the vote does not move it"
        )
    if shot["asset_id"] not in family_held:
        return "its story's only shot that is not a portrait; the vote does not move it"
    whose = _whose(family_held[shot["asset_id"]])
    return f"the only shot of {whose} in the film; the vote does not move it"


def _state(named: int, *, protected: bool) -> str:
    if protected or named == 0:
        return "kept"
    return "bad" if named >= 2 else "weak"
