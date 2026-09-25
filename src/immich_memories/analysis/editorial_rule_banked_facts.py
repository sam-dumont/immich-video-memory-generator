"""What a model already answered about this library, read by the reader that asks nothing.

The rules draft is built from indicators and head facts and asks no question. On an install
where a model HAS answered — an earlier cut of the same film, or the thin layer polishing last
week's draft — those answers are sitting in banks the draft never opened, so it kept offering
pictures a gate had already refused and the film came out short. This module opens those banks
read-only and answers four questions about a picture or an episode. It never asks anything, it
never writes, and a missing bank answers None, False or () everywhere, so a cold install draws
exactly the draft it drew before.

Keying. A bank answer is only an answer to the question it was asked, so each read is named the
way the writing side named it:

* Episode readings live in the annotation store under (group, producer, evidence). The producer
  is the reading contract; the evidence is a digest of the exact annotation lines. This module
  matches on group and evidence and ignores the producer, so a reading made by any reader of the
  same pictures is read. That is safe for a cull, which is a refusal, and for a representative,
  which is a nomination the rules order still has to rank: neither can admit a picture the
  current rules would refuse, and both only ever reorder or withhold within one episode.
* Audience verdicts are recorded per picture by the gate that cast them, in the cut they belong
  to, and the library's audience bank keeps the holds of every cut of every scope. Only
  refusals are read, and only for the same audience: a stale `share` never clears anything,
  which is the direction the owner asked for (a hold is never lifted by a later read). A bank
  hold the model cast from text under an older audience prompt no longer counts, exactly as
  the gate treats it.

A favourite is never withheld by anything read here. The owner's own choice outranks a banked
answer about it, exactly as it outranks the rules' own standing verdict.

Standing is not read here: the heads answer it on every tier (`editorial_standing_facts`), and a
model's banked standing vote agreed with a stronger reader less often than the heads do.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import Any, Protocol

from immich_memories.analysis.editorial_shareability import allowed
from immich_memories.analysis.editorial_structure_audience import (
    AUDIENCE_BANK_NAME,
    library_refusals,
)
from immich_memories.store.owner_decisions import CLEAR_HOLD, decisions

logger = logging.getLogger(__name__)

CUT_RECORD_NAME = "plan.private.json"


class BankedFacts(Protocol):
    """The four things the rules draft asks of answers it did not pay for."""

    def refused_for_audience(self, asset_id: str) -> bool: ...

    def episode_representatives(self, episode_key: str) -> tuple[str, ...]: ...

    def culled(self, asset_id: str) -> bool: ...

    def record_owning(self, episode_key: str) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class BankedAnswers:
    """Answers already banked for this film, resolved once when the banks are opened."""

    refused: frozenset[str]
    representatives: Mapping[str, tuple[str, ...]]
    culls: frozenset[str]

    def refused_for_audience(self, asset_id: str) -> bool:
        return asset_id in self.refused

    def episode_representatives(self, episode_key: str) -> tuple[str, ...]:
        return self.representatives.get(episode_key, ())

    def culled(self, asset_id: str) -> bool:
        return asset_id in self.culls

    def record_owning(self, episode_key: str) -> tuple[str, ...]:
        """The pictures that own an episode's notable record.

        A reading names them on `notable_moments`, a field the episode bank does not carry yet.
        Until it does this answers nothing, which costs the draft the ranking it would gain and
        nothing else; when the field lands, this returns those asset ids and the draft leads
        with them the way it already leads with a reading's representatives.
        """
        return ()

    def record(self) -> dict[str, Any]:
        """What the banks answered, so a draft that read nothing says so out loud.

        A silent no-op and a bank that was never opened look identical in a cut. These counts
        are the difference, and they are the first thing to grep when a warm library draws the
        cold film.
        """
        return {
            "audience_refusals": len(self.refused),
            "episodes_read": len(self.representatives),
            "banked_representatives": len(set(chain.from_iterable(self.representatives.values()))),
            "culled": len(self.culls),
        }


NO_BANKED_FACTS = BankedAnswers(frozenset(), {}, frozenset())
"""A library nothing has read yet: every question comes back unanswered."""


def configured_text_identity(llm_config: Any) -> str:
    """The reader this install is configured with, named the way a vote bank names it.

    A no-model run still has the configuration of whatever model read this library before, and
    that name is half of every banked answer's key. Without it nothing can be read back, which
    is the honest outcome for an install that has never had a reader.
    """
    if llm_config is None:
        return ""
    from immich_memories.analysis.llm_providers import resolved_llm_config
    from immich_memories.analysis.llm_text_identity import text_model_identity

    try:
        return text_model_identity(resolved_llm_config(llm_config), thinking=False)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        logger.debug("No configured reader identity (%s): banked answers stay closed", exc)
        return ""


def open_banked_facts(
    *,
    bank_dir: Path,
    attempts_dir: Path | None,
    store_path: Path | None,
    audience: str,
    episode_cards: Mapping[str, Any],
    own_producers: frozenset[str] = frozenset(),
) -> BankedAnswers:
    """Open this film's banks read-only and resolve what they already say.

    `episode_cards` maps a moment alias to the episode card the run carries, whose episode id and evidence key name the
    reading to look for. `own_producers` names the reading contracts this very run produced, so
    the draft is not handed its own answer back as if somebody else had given it. Anything
    unreadable is treated as unanswered rather than raised: a draft that cannot open a bank is
    the cold draft, which is always a valid film.
    """
    representatives, culls = _banked_readings(store_path, episode_cards, own_producers)
    refused = _refused_before(attempts_dir, audience=audience) | library_refusals(
        bank_dir.parent / AUDIENCE_BANK_NAME, audience
    )
    answers = BankedAnswers(
        refused=refused - _owner_cleared(store_path),
        representatives=representatives,
        culls=culls,
    )
    logger.debug(
        "banked facts: %d refused, %d read episodes, %d culled",
        len(answers.refused),
        len(answers.representatives),
        len(answers.culls),
    )
    return answers


def _owner_cleared(store_path: Path | None) -> frozenset[str]:
    """What the owner cleared by hand: a refusal banked before the clearance no longer holds."""
    if store_path is None:
        return frozenset()
    return frozenset(a for a, decision in decisions(store_path).items() if decision == CLEAR_HOLD)


def _refused_before(attempts_dir: Path | None, *, audience: str) -> frozenset[str]:
    """Every picture an earlier cut of this film refused for this same audience.

    A verdict is only read when the cut that cast it was made for the audience being cut for
    now, and only a refusal is carried over. The reverse — treating a banked `share` as a pass —
    would let one cut's clearance stand in for a check this cut never made.
    """
    if attempts_dir is None:
        return frozenset()
    refused: set[str] = set()
    for record in sorted(attempts_dir.glob(f"*/{CUT_RECORD_NAME}")):
        share = _cut_shareability(record)
        if share.get("audience") != audience:
            continue
        verdicts = share.get("verdicts")
        if not isinstance(verdicts, Mapping):
            continue
        refused.update(
            asset_id
            for asset_id, verdict in verdicts.items()
            if isinstance(verdict, Mapping) and not allowed(str(verdict.get("verdict")), audience)
        )
    return frozenset(refused)


def _cut_shareability(record: Path) -> Mapping[str, Any]:
    try:
        plan = json.loads(record.read_text())
    except (OSError, ValueError):
        return {}
    share = plan.get("shareability") if isinstance(plan, Mapping) else None
    return share if isinstance(share, Mapping) else {}


def _banked_readings(
    store_path: Path | None, episode_cards: Mapping[str, Any], own_producers: frozenset[str]
) -> tuple[dict[str, tuple[str, ...]], frozenset[str]]:
    """The representatives and culls of every episode ANOTHER reader has already read.

    The lookup ignores the producer column except to drop this run's own readings: the question
    is what was read about these exact pictures, not who read them, and a run being handed its
    own answer back would be reading its own mind. Membership and evidence match exactly.
    """
    wanted = {
        (card.episode_id, card.evidence_key)
        for card in episode_cards.values()
        if getattr(card, "evidence_key", "")
    }
    if store_path is None or not wanted:
        return {}, frozenset()
    read = _read_episode_rows(store_path, sorted(wanted), own_producers)
    representatives = {
        alias: read[(card.episode_id, card.evidence_key)][0]
        for alias, card in episode_cards.items()
        if (card.episode_id, getattr(card, "evidence_key", "")) in read
    }
    # Every read episode's refusals.
    culls = chain.from_iterable(culled for _reps, culled in read.values())
    return representatives, frozenset(culls)


def _read_episode_rows(
    store_path: Path, wanted: list[tuple[str, str]], own_producers: frozenset[str]
) -> dict[tuple[str, str], tuple[tuple[str, ...], tuple[str, ...]]]:
    """(representatives, culled) per (group, evidence), newest reading of each pair winning."""
    out: dict[tuple[str, str], tuple[tuple[str, ...], tuple[str, ...]]] = {}
    try:
        connection = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return out
    try:
        for start in range(0, len(wanted), 250):
            chunk = wanted[start : start + 250]
            marks = ",".join("(?, ?)" for _ in chunk)
            rows = connection.execute(
                "SELECT group_id, evidence_key, producer_key, representatives, cull_decisions "  # noqa: S608 -- generated placeholders; every value is bound
                "FROM editorial_episode_readings "
                f"WHERE (group_id, evidence_key) IN ({marks}) ORDER BY answered_at",
                tuple(chain.from_iterable(chunk)),
            )
            out.update(
                ((str(group), str(evidence)), _reading_lists(reps, culls))
                for group, evidence, producer, reps, culls in rows
                if str(producer) not in own_producers
            )
    except sqlite3.Error:
        return out
    finally:
        connection.close()
    return out


def _reading_lists(
    representatives: object, culls: object
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return _asset_ids(representatives), _asset_ids(culls)


def _asset_ids(payload: object) -> tuple[str, ...]:
    try:
        rows = json.loads(str(payload))
    except ValueError:
        return ()
    return tuple(
        str(row["asset_id"])
        for row in rows
        if isinstance(row, Mapping) and str(row.get("asset_id", "")).strip()
    )


def withheld_by_bank(
    banked: BankedFacts, *, favourite: Callable[[str], bool]
) -> Callable[[str], bool]:
    """Whether a picture should not be offered at all: already refused, or already culled."""

    def withheld(asset_id: str) -> bool:
        if favourite(asset_id):
            return False
        return banked.refused_for_audience(asset_id) or banked.culled(asset_id)

    return withheld


def banked_leaders(banked: BankedFacts, episode_keys: tuple[str, ...]) -> frozenset[str]:
    """The pictures a banked reading named for their own episode: its representatives, and the
    ones that own its record. A representative is only ever a member of the episode that names
    it, so one set over the whole film says the same thing as a set per episode."""
    return frozenset(
        asset_id
        for key in episode_keys
        for asset_id in (*banked.episode_representatives(key), *banked.record_owning(key))
    )
