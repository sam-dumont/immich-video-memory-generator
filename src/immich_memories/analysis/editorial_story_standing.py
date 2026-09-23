"""Does a picture stand by itself, and may it serve as context inside its story?

One question over one picture at a time, asked of whichever reader the run has: a model
votes on the rows the gate renders, and a rules reader answers from the facts on the line.
The answer is what the carrier admission and the depth pass both spend, so it lives beside
neither of them.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from immich_memories.analysis.editorial_block_votes import judge_standing
from immich_memories.analysis.editorial_story_pick_contract import (
    carries_motion,
    moving_picture_row,
)
from immich_memories.security import write_secret_file

WEIGHED_STORY_WEIGHTS = ("dominant", "major", "minor")
STANDING_BANK_NAME = "picture-stands.private.json"


def standing_bank_path(case_bank_dir: Path) -> Path:
    """The library's standing bank, beside every film's own banks rather than inside one.

    The question names no film, so neither does the file: a month and the year around it read
    and write the same answers.
    """
    return case_bank_dir.parent / STANDING_BANK_NAME


class StandingBankFile:
    """The library's standing answers as one film's gate reads and extends them.

    Several films may be cut at once over the same library. A save therefore folds this film's
    answers into whatever is on disk by then instead of replacing it: an answer is keyed by its
    whole question, so two films can only ever add different names or the same answer twice.
    """

    def __init__(self, path: Path, entries: dict) -> None:
        self.path = path
        self.entries = entries

    @classmethod
    def open(cls, case_bank_dir: Path) -> StandingBankFile:
        path = standing_bank_path(case_bank_dir)
        return cls(path, _read_entries(path))

    def save(self) -> None:
        merged = _read_entries(self.path)
        rows = _rows(merged) | _rows(self.entries)
        merged.update(self.entries)
        merged["rows"] = rows
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        write_secret_file(self.path, json.dumps(merged, indent=1))


def _rows(entries: dict) -> dict:
    rows = entries.get("rows")
    return rows if isinstance(rows, dict) else {}


def _read_entries(path: Path) -> dict:
    try:
        entries = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return entries if isinstance(entries, dict) else {}


def standing_row(
    line: str,
    unit: Mapping[str, Any] | None,
    motion_line: Callable[[Mapping[str, Any]], str] | None,
) -> str:
    """The row the standing question is asked about: a still's own line; a video's, or a Live
    Photo whose motion plays, says what it is, how long it runs and what happens across it.

    Cut-time speech detection guides timing, not this picture's standing. Keep it out of both
    the source marker and the motion observer's plain-facts fallback.
    """
    if not line or unit is None:
        return line
    without_speech = dict(unit)
    without_speech.pop("speech_regions", None)
    return moving_picture_row(line, without_speech, motion_line)


class StandingGate:
    """Does a picture stand by itself, and may it serve as context inside its story?"""

    def __init__(
        self,
        judge,
        *,
        line_of: Callable[[str], str],
        life: Callable[[str], bool],
        unit_by_asset: Mapping[str, Any],
        pictures_of: Mapping[str, int],
        bank: dict | None,
        save: Callable[[], None] | None,
        calls: dict[str, int],
        score_of: Callable[[str], int] | None = None,
        motion_line: Callable[[Mapping[str, Any]], str] | None = None,
        motion_identity: str = "",
        subject: str = "",
    ) -> None:
        self._judge = judge
        self._score_of = score_of
        self._subject = subject
        self._line_of = line_of
        self._life = life
        self._unit_by_asset = unit_by_asset
        self._pictures_of = pictures_of
        self._bank = bank
        self._save = save
        self._calls = calls
        self._motion_line = motion_line
        self._motion_identity = motion_identity
        self.scores: dict[str, int] = {}
        self.context_rejected: set[tuple[str, str]] = set()

    def row_of(self, asset: str) -> str:
        """The row the gate judges, rendered exactly as any other reader of these answers does."""
        entry = self._unit_by_asset.get(asset)
        return standing_row(
            self._line_of(asset), None if entry is None else entry[1], self._motion_line
        )

    def ensure(self, assets: Sequence[str], needs: Mapping[str, int] | None = None) -> None:
        """Score every picture not yet scored.

        With `needs` (from `needs()`), a picture no answer can move is not asked at all, and one
        that turns on a single approval is asked in a second order only when the first names it
        weak: its score is then out of one order, which decides it exactly as two would.
        """
        unknown = [
            a
            for a in dict.fromkeys(assets)
            if a not in self.scores and self._line_of(a) and (needs is None or needs.get(a, 2))
        ]
        if not unknown:
            return
        if self._score_of is not None:
            self.scores.update({a: self._score_of(a) for a in unknown})
            return
        self._calls["standing_rounds"] += 1
        votes = judge_standing(
            self._judge,
            pictures=unknown,
            line_of=self.row_of,
            subject=self._subject,
            bank=self._bank,
            save=self._save,
            motion_identity=self._motion_identity,
            settled=None if needs is None else _settled_by(needs),
        )
        for a, (n, _why) in votes.items():
            self.scores[a] = n
        for a in unknown:
            self.scores.setdefault(a, 0)

    def needs(self, asset: str, weight: str, story_key: str = "") -> int:
        """How many orders' approval this picture's standing turns on, as `stands` reads it.

        0 when no answer changes whether it stands (it has no context to serve, or it is a still
        with life in a dominant or major story); 1 when one approval decides it; 2 when it must
        stand by both orders.
        """
        if asset not in self._unit_by_asset:
            return 2
        if not self._context_allowed(asset, weight, story_key):
            return 0
        lively, starred = self._life(asset), self._starred(asset)
        if weight == "glimpse" or self.thin(story_key):
            return 2
        if not lively and not starred and weight == "minor":
            return 2
        moving = carries_motion(self._unit_by_asset[asset][1])
        return 0 if lively and weight in ("dominant", "major") and not moving else 1

    def _starred(self, asset: str) -> bool:
        return (
            bool(self._unit_by_asset[asset][1].get("favourite"))
            if asset in self._unit_by_asset
            else False
        )

    def thin(self, story_key: str) -> bool:
        """A story of one or two pictures has no context for a weak picture to serve."""
        return self._pictures_of.get(story_key, 0) <= 2

    def rejected_motion(self, asset: str) -> bool:
        """Playing motion cannot override missing or unanimously weak standing evidence."""
        return carries_motion(self._unit_by_asset[asset][1]) and self.scores.get(asset, 0) == 0

    def _context_allowed(self, asset: str, weight: str, story_key: str) -> bool:
        starred = bool(self._unit_by_asset[asset][1].get("favourite"))
        return (
            self._life(asset)
            or starred
            or (weight in WEIGHED_STORY_WEIGHTS and self._pictures_of.get(story_key, 0) > 2)
        )

    def has_required_context(self, asset: str, weight: str, story_key: str) -> bool:
        """The existing context requirement is eligibility, not a recoverable weak vote."""
        allowed = self._context_allowed(asset, weight, story_key)
        if not allowed:
            self.context_rejected.add((story_key, asset))
        return allowed

    def stands(self, asset: str, weight: str, story_key: str = "") -> bool:
        """A glimpse, or a story of one or two pictures, has no context to serve, so its picture
        must stand entirely alone (named weak by neither order). Inside a dominant or major story a
        picture with people or animals in it serves its purpose with context and is only ORDERED by
        the gate, never removed; a lifeless one (a room, an object) needs one order's approval. In a
        minor story a picture with life needs one order, a lifeless one both. Moving clips always
        need at least one standing approval; their media kind cannot override two weak votes."""
        score = self.scores.get(asset, 0)
        if self.rejected_motion(asset):
            return False
        lively = self._life(asset)
        thin = self.thin(story_key)
        if not self.has_required_context(asset, weight, story_key):
            return False
        if not lively and not self._starred(asset) and weight == "minor":
            # Context pictures in a minor story still need both standing votes.
            return score == 2
        if weight == "glimpse" or thin:
            return score == 2
        if weight in ("dominant", "major"):
            return lively or score >= 1
        return score >= 1


def _settled_by(needs: Mapping[str, int]) -> Callable[[str, bool], bool]:
    """One order settles a picture that turns on one approval and was not named weak by it.

    A picture one order named is always asked again: the reader's reject-only answers flip with
    the order of the rows, so one order's doubt never refuses a picture on its own.
    """
    return lambda asset, named: needs.get(asset, 2) == 1 and not named
