"""Does each picture stand by itself? One answer per row, and a doubt is checked once.

The reject-only form ("name the weak ones") had the 30B name a few rows of every block whatever
the criterion said: quiet family pictures (a baby on a blanket, a family posing) came out weak
in both orders, and rewording the criterion moved nothing (09-24, #1205). The question now asks
every row for its own yes or no. Only the rows the first order calls weak are asked again, in a
block of their own; a picture is refused when both answers agree (#1184).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, MutableMapping, Sequence

from immich_memories.analysis.editorial_block_votes import (
    Settled,
    _judge_model_identity,
    _picks,
    _vote_cache_key,
    vote_blocks,
)

# v7: one yes/no per row instead of a reject-only list. Every v6 row expires once.
STANDING_PROMPT_VERSION = "picture-stands-v7-per-row"
STANDING_CRITERION = (
    "For each picture, answer whether it carries nothing worth showing. Answer yes only for a lone "
    "everyday object with nobody in it, an empty room or floor, a screen, a document, a test shot, an "
    "accidental or unusably blurred frame, or a close-up of a body part or an ailment. People in a real "
    "moment carry something, quiet or posed: answer no for them, and for a place worth seeing. A row that "
    "names a video, or a Live Photo whose motion plays, is footage: judge what happens across it, told by "
    "the sentence after its length. After a yes, say in at most 12 words why."
)
STANDING_MAX_TOKENS = 700
_ANSWER_KEY = "weak"
_OFFERED_LABEL = re.compile(r"^(P\d+): ", re.MULTILINE)
_YES_NO = re.compile(r"\s*(yes|no)\b[\s:,.;-]*(.*)", re.IGNORECASE | re.DOTALL)


def standing_pass_version(motion_identity: str) -> str:
    """The bank key for standing votes cast on these rows.

    The criterion is half the question; the shape of the row it judges is the other half. A
    moving picture's row now carries the motion sentence the caption seat banked at
    preparation, so the seat that wrote those sentences belongs in the key: another seat writes
    other sentences, and its predecessor's answers must not replay under them. #1064 settled
    the same thing for cull verdicts and the reading behind them.
    """
    if not motion_identity:
        return STANDING_PROMPT_VERSION
    return f"{STANDING_PROMPT_VERSION}/{motion_identity}"


def _row_example(listing: str) -> str:
    labels = _OFFERED_LABEL.findall(listing)[:2]
    shown = dict(zip(labels, ("no", "yes: why"), strict=False))
    return json.dumps({_ANSWER_KEY: shown}, separators=(",", ":"))


def standing_prompt(listing: str, subject: str = "") -> str:
    """The whole standing question over one listing of rows.

    Whether a picture stands by itself does not depend on which film offers it: a mug on a table
    is as weak in May as in the year around it. The question therefore names no period and no
    film contract, so one answer serves every cut that reaches the picture. The one exception is
    a memory bound to a subject (a custom memory's topic, a person): there a picture of the
    subject is the point, so the subject is part of the question and of its bank name.
    """
    about = (
        f"This memory is about: {subject}. A picture that shows it, or a stage of it, is worth "
        "showing.\n\n"
        if subject
        else ""
    )
    # Pictures last: see judge_worthiness.prompt_of (#981).
    return (
        f"{about}Below are single pictures, one line each: when it was taken and what it shows. "
        f"Text only.\n\n{STANDING_CRITERION}\n\n"
        "Answer with one JSON object only, on one line, with every label exactly once: "
        f"{_row_example(listing)}\n\nPICTURES\n{listing}"
    )


def read_row_answers(raw: str, answer_key: str, allowed: set[str]) -> tuple[dict[str, str], str]:
    """The labels answered yes, with their reasons, and the envelope the answer came in.

    A "no" stands. A label left out has not been called weak, which is what the reject-only
    form meant by leaving it out, so a reader that still answers that way is read the same. A
    value that is neither yes nor no is a reason, and a reason is a yes.
    """
    picked, envelope = _picks(raw, answer_key, allowed)
    weak = {}
    for label, answer in picked.items():
        said = _YES_NO.match(answer)
        if said and said.group(1).casefold() == "no":
            continue
        weak[label] = said.group(2) if said else answer
    return weak, envelope


def _standing_row_key(asset_id: str, row: str, *, subject: str, version: str) -> str:
    # One picture's own question: the whole prompt except the company it was offered in, the
    # picture it was asked about, and the row that described it (a new caption is a new row).
    question = version + "|" + standing_prompt("", subject) + "|"
    return hashlib.sha256(f"{asset_id}\x00{question}{row}".encode()).hexdigest()


def standing_row_name(
    asset_id: str, row: str, *, identity: str, subject: str = "", motion_identity: str = ""
) -> str:
    """The name one picture's standing answer lives under in the library's per-row store.

    A reader that wants to know what was already answered about a picture has to name that
    answer exactly as the asking side named it: the criterion, the motion seat behind a moving
    row, the model that replied, the picture and the row's own text. Both sides derive the name
    here, so neither can drift away from the other. No film scope is part of it.
    """
    version = standing_pass_version(motion_identity)
    return _vote_cache_key(
        {"row": _standing_row_key(asset_id, row, subject=subject, version=version)},
        identity,
        STANDING_MAX_TOKENS,
        _ANSWER_KEY,
        rows_version=version,
    )


def _ask_once(
    judge, *, stage: str, pictures: Sequence[str], label_of, line_of, subject, version, bank, save
):
    """One order over balanced blocks of these pictures: which of them it called weak, and why."""

    def bank_key(block: Sequence[str]) -> str:
        return hashlib.sha256(
            (
                version + "|" + stage + "|" + subject + "|" + "|".join(line_of(a) for a in block)
            ).encode()
        ).hexdigest()

    votes, _rounds = vote_blocks(
        judge,
        stage=stage,
        items=pictures,
        label_of=label_of,
        row_of=lambda a: f"{label_of[a]}: {line_of(a)}",
        prompt_of=lambda listing: standing_prompt(listing, subject),
        answer_key=_ANSWER_KEY,
        bank_key=bank_key,
        bank=bank,
        save=save,
        max_tokens=STANDING_MAX_TOKENS,
        # The check pass may put a lone doubt in a block of one, byte-identical to the first
        # question: its own bank name keeps the first answer from standing in for the second.
        rows_version=version if stage == "standing" else f"{version}/{stage}",
        settled=lambda _asset, _named: True,
        balanced=True,
        picks=read_row_answers,
    )
    return {a: why for a, (n, why) in votes.items() if n}


def judge_standing(
    judge,
    *,
    pictures: Sequence[str],
    line_of: Callable[[str], str],
    subject: str = "",
    bank: MutableMapping[str, dict] | None = None,
    save: Callable[[], None] | None = None,
    model_identity: str | None = None,
    motion_identity: str = "",
    settled: Settled | None = None,
) -> dict[str, tuple[int, str]]:
    """Does each picture stand by itself? Score per asset id: 2 = the first order said it stands,
    1 = one order called it weak, 0 = both did.

    Every row is answered in its source company. A row called weak is asked again among the
    other rows called weak, in their hashed order, unless `settled` says one weak answer already
    decides it. A decided row is banked under its own name (`standing_row_name`), so a later cut
    that reaches the picture is not asked again. `motion_identity` names the seat whose
    sentences a moving picture's row carries, so its rows expire with it.
    """
    version = standing_pass_version(motion_identity)
    identity = _judge_model_identity(judge, model_identity) if bank is not None else None
    rows = bank.setdefault("rows", {}) if bank is not None and identity is not None else None
    names = (
        {
            a: standing_row_name(
                a, line_of(a), identity=identity, subject=subject, motion_identity=motion_identity
            )
            for a in pictures
        }
        if rows is not None and identity is not None
        else {}
    )
    scores = _banked(rows or {}, names)
    # One label per picture across both passes, so a doubt keeps the name it was doubted under.
    label_of = {a: f"P{i + 1:02d}" for i, a in enumerate(pictures)}
    asked = {
        "judge": judge,
        "label_of": label_of,
        "line_of": line_of,
        "subject": subject,
        "version": version,
        "bank": bank,
        "save": save,
    }
    pending = [a for a in pictures if a not in scores]
    fresh = _ask_and_tally(pending, settled, asked) if pending else {}
    scores |= {a: (2 - vote["votes"], vote["why"]) for a, vote in fresh.items()}
    fresh = {a: vote for a, vote in fresh.items() if vote["decided"]}
    if rows is not None and fresh:
        rows.update({names[a]: {"votes": v["votes"], "why": v["why"]} for a, v in fresh.items()})
        if save is not None:
            save()
    return scores


def _ask_and_tally(pending: Sequence[str], settled: Settled | None, asked) -> dict[str, dict]:
    """Each pending picture's weak votes (0-2), its first reason, and whether it is decided: a
    picture one weak answer settles is not asked again, and is not banked as if it had been."""
    first = _ask_once(stage="standing", pictures=pending, **asked)
    doubted = [a for a in pending if a in first and (settled is None or not settled(a, True))]
    doubted.sort(key=lambda a: hashlib.sha256((a + asked["version"]).encode()).hexdigest())
    second = _ask_once(stage="standing-check", pictures=doubted, **asked) if doubted else {}
    return {
        a: {
            "votes": (a in first) + (a in second),
            "why": first.get(a, ""),
            "decided": a not in first or a in doubted,
        }
        for a in pending
    }


def _banked(rows: Mapping[str, object], names: Mapping[str, str]) -> dict[str, tuple[int, str]]:
    return {
        a: (2 - int(row["votes"]), str(row.get("why", "")))
        for a, name in names.items()
        if isinstance(row := rows.get(name), Mapping) and "votes" in row
    }
