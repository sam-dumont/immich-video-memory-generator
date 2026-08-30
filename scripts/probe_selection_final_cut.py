"""Prototype text contract for the asset cut inside selected moment reservoirs."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from immich_memories.analysis.selection_final_duplicates import DOCUMENT_ARTIFACT_WORDS
from immich_memories.analysis.strict_json import bounded_model_text, final_json_object

FINAL_ASSET_CUT_SCHEMA = "description-final-asset-cut-v1"
FINAL_ASSET_AUDIT_SCHEMA = "description-final-asset-audit-v1"
FINAL_VISUAL_ASSET_AUDIT_SCHEMA = "visual-final-wall-audit-v1"
FINAL_VISUAL_POOL_RECONSIDERATION_SCHEMA = "visual-final-pool-reconsideration-v1"
FINAL_VISUAL_POOL_GLOBAL_VALIDATION_SCHEMA = "visual-final-pool-global-validation-v1"
FINAL_ASSET_RECONSIDERATION_SCHEMA = "description-final-asset-reconsideration-v1"
FINAL_ASSET_DELTA_VALIDATION_SCHEMA = "description-final-asset-delta-validation-v1"
FINAL_SEQUENCE_REVIEW_SCHEMA = "description-final-sequence-review-v1"
FINAL_SEQUENCE_REVIEW_CUT_ONLY_SCHEMA = "description-final-sequence-review-v2"
_MAX_REASON_CHARS = 500
# Provisional attention cue from the v6 wall that hid seven near-redundant hiking
# frames. It never changes a verdict. Validate its prompt-focus precision across
# the 20-25 acceptance walls before carrying it into production.
_AUDIT_FOCUS_WINDOW = timedelta(days=7)
_AUDIT_FOCUS_MIN_ASSETS = 5
_AUDIT_FOCUS_MIN_MOMENT_ASSETS = 3
_AUDIT_FOCUS_MIN_EPISODE_ASSETS = 3
_VISUAL_AUDIT_WHOLE_WALL_MAX_ASSETS = 24
_VISUAL_AUDIT_MAX_GROUPS = 8
# A 170-tile reconsideration group went out as two 108-tile sheets inside ONE vision request.
# The local 27B never answered inside 600s, the gateway retried, and the refinement loop
# re-attempted the same group five times: 60 of a 74-minute run. The hosted same model answered
# 21 small audit calls in 39s. Set-level judgment holds to about a dozen tiles; multi-image
# position bias and cross-image leakage both worsen as the tile count grows.
AUDIT_MAX_TILES_PER_REQUEST = 12
# Six requests is 72 tiles of one focus, past which the group has stopped being a bounded check.
AUDIT_MAX_REQUESTS_PER_GROUP = 6
_VISUAL_POOL_MAX_FINDINGS = 12
_FINAL_MOMENT_ASSET_CAP = 2
# The v28 camp ran 08-05 to 08-16 and the cut reached five of the ten days the
# wall carried, so the floor has to fire at exactly half, not below it. Four
# photographed days is the shortest span where "the trip" is a thing the viewer
# can lose days out of; a single unphotographed rest day never ends it.
_OCCASION_MIN_PHOTOGRAPHED_DAYS = 4
_OCCASION_DAY_GAP = timedelta(days=2)
# The owner eyeballed a final wall where one friend held two tiles and the friend
# they have known longest held none, with both in the reservoir. Five reservoir
# assets is where a person stops being the bystander of a single frame; two wall
# tiles is where the cut has visibly chosen someone to compare that absence to.
_PERSON_MIN_RESERVOIR_ASSETS = 5
_PERSON_MIN_WALL_ASSETS = 2
_PERSON_COVERAGE_MAX_FINDINGS = 3
# Two near-black tiles survived to the same wall at mean preview luminance 30-55
# while their own moments held brighter frames of the same beat; 45 caught that
# pair, but later walls still carried tiles the owner flagged as dark measuring
# 50-60. 60 sits above that band and under a dim-but-legible interior; 1.5x is
# the smallest gap that reads as a different exposure rather than
# preview-decode noise.
_DARK_FRAME_LUMINANCE = 60
_DARK_FRAME_BRIGHTER_RATIO = 1.5
_DARK_FRAME_MAX_FINDINGS = 4
# The review's keep/cut verdict on a single-asset memento moment measured nondeterministic
# across cold runs on the same inputs -- cut in one run, kept in the next. A single-asset
# moment has no lived sibling to swap in the way a dark frame does, so this finding never
# proposes an alternative; it only forces the keep to be judged consciously every run
# instead of by decode luck. Substring match on the card text, like OUTDOOR_SETTING_WORDS.
_DOCUMENT_ARTIFACT_MAX_FINDINGS = 2
# One occasion reached the wall as faces only, while the corpus held a people-free open
# place in a moment the cut rejects run after run. The primary signal is structural: the
# fused card hedges people away, or no candidate row of the moment carries any people
# context. A rejected moment never opens a reservoir, so this reads what the run record
# still has for it -- the card's summary, its hedged people field, and the moment's
# candidate rows -- and never a preview.
#
# The word list only CORROBORATES that the people-free card is an outdoor setting rather
# than, say, an indoor still life. It is deliberately broad across seasons and terrains,
# no member is load-bearing, and it decides nothing on its own. Substring match on the
# card text, like DOCUMENT_ARTIFACT_WORDS.
OUTDOOR_SETTING_WORDS = (
    "beach",
    "cliff",
    "coastline",
    "desert",
    "dune",
    "field",
    "forest",
    "glacier",
    "harbour",
    "hillside",
    "horizon",
    "island",
    "lake",
    "landscape",
    "meadow",
    "moorland",
    "mountain",
    "panorama",
    "pasture",
    "plain",
    "ridge",
    "river",
    "shoreline",
    "skyline",
    "snow-covered",
    "summit",
    "terrain",
    "valley",
    "waterfall",
    "woodland",
)
_CARD_PEOPLE_HEDGE = "insufficient evidence"
_PLACE_WITHOUT_LANDSCAPE_MAX_FINDINGS = 2
# What the run may offer back per dropped moment, and how many moments it may open at all.
# Every offered row costs a preview decode and a tile, and the findings cap is 2 a run, so
# the pool only has to be wide enough for the ranking to have a real choice.
PLACE_PROPOSAL_MAX_ASSETS = 4
PLACE_PROPOSAL_MAX_MOMENTS = 8
# One October evening reached the wall with four tiles: two moments of two assets each,
# legal under the per-moment cap and every cap above it. The owner reads a day, not a
# moment -- "too much pics from the party". A favourite is never dropped, but it still
# holds one of the day's three slots: the viewer counts what the day shows, not how many
# of them are starred.
_FINAL_DAY_ASSET_CEILING = 3
_STRAY_STRUCTURAL_QUOTE = re.compile(r'(?m)^([ \t]*)"}(,?)[ \t]*$')
_MEDIA_PRIORITY_GUIDANCE = """When two candidates make the same editorial contribution, prefer
video, then meaningful live-motion, then photo. This is a weighted tie-breaker, not a quota: never
keep redundant or weak motion merely because it moves. A Live Photo labelled photo has no
demonstrated motion advantage; only supplied motion evidence can earn the live-motion label."""
_UNGROUNDED_INTERACTION_GUIDANCE = """When an interaction has no supplied people context, treat
kissing, hugging, or physical closeness as visible event atmosphere, not evidence of relationship
importance, closeness, or a turning point. Never invent identity from the interaction. It may still
earn runtime when it uniquely carries the event atmosphere, action, or emotional state; otherwise
prefer a distinct contribution grounded by recurring, inner-circle, partner, or family context."""
_FINAL_AUDIT_FINDING_KINDS = frozenset(
    {
        "repetition",
        "subject_or_pose_overweight",
        "missing_place_or_progression",
        "missing_action_or_event",
        "missing_relationship_contribution",
        "event_family_repetition",
        "weak_evidence",
        "sequence_gap",
    }
)


@dataclass(frozen=True)
class FineCutCandidate:
    """One described asset available to the final duration cut."""

    alias: str
    asset_id: str
    moment_id: str
    taken_at: datetime
    media_kind: str
    favourite: bool
    description: str
    context: tuple[str, ...] = ()
    episode_id: str | None = None
    people_context: tuple[str, ...] = ()
    motion_contribution: str | None = None
    motion_reason: str | None = None
    source_media_kind: str | None = None
    motion_observed: bool = False
    render_mode: str = "still"
    render_frame_seconds: float | None = None
    luminance: int | None = None
    closes_memory: bool = False
    # A row the moment cut dropped, offered back to the visual arm as a proposal. It is
    # never part of the current film and the text arm never sees it until it is adopted.
    proposed_from_rejected: bool = False

    def taken_at_field(self) -> str:
        """Render the timestamp cell every wall shares, with its optional luminance datum."""
        luminance = f" lum={self.luminance}" if self.luminance is not None else ""
        return f"{self.taken_at.isoformat()}{luminance}"

    def structural_field(self) -> str:
        """Render the structural markers every wall shares for one row."""
        markers = (
            (self.closes_memory, "closes-memory"),
            (self.proposed_from_rejected, "proposed-from-unkept-moment"),
        )
        return "".join(f" {name}" for flag, name in markers if flag)

    def wall_line(self) -> str:
        favourite = " | FAVOURITE" if self.favourite else ""
        context = f" | context {' ; '.join(self.context)}" if self.context else ""
        episode = f" | episode {self.episode_id}" if self.episode_id else ""
        people = f" | people {' ; '.join(self.people_context)}" if self.people_context else ""
        motion = f" | motion {self.motion_contribution}" if self.motion_contribution else ""
        if motion and self.motion_reason:
            motion += f": {self.motion_reason}"
        return (
            f"{self.alias}{self.structural_field()} | moment {self.moment_id} | "
            f"{self.taken_at_field()} | {self.media_kind}{favourite}{episode}{people}"
            f"{context}{motion} | {self.description}"
        )


def final_asset_cut_prompt(
    candidates: Sequence[FineCutCandidate],
    *,
    memory_label: str,
    memory_type: str,
    editorial_brief: str,
    thesis: dict[str, Any],
    capacity: int,
    required_aliases: Sequence[str] = (),
    local_story_evidence: dict[str, Any] | None = None,
) -> str:
    """Ask for the real asset cut, after the moment reservoirs were opened."""
    required = tuple(dict.fromkeys(required_aliases))
    valid = {candidate.alias for candidate in candidates}
    if not set(required) <= valid:
        raise ValueError("required final-cut assets must come from the candidate wall")
    remaining = capacity - len(required)
    if remaining < 0:
        raise ValueError("required final-cut assets exceed duration capacity")
    wall = "\n".join(candidate.wall_line() for candidate in candidates)
    shape = {
        "schema_version": FINAL_ASSET_CUT_SCHEMA,
        "keep": [{"asset_id": "A001", "reason": "why this visual earns runtime"}],
        "comparisons": [
            {
                "kept_asset_id": "A001",
                "rejected_asset_id": "A002",
                "reason": "why the retained visual is stronger",
            }
        ],
        "overall_reason": "how the actual visual sequence carries the thesis",
    }
    local_story_block = ""
    if local_story_evidence:
        local_story_block = f"""LOCAL CHAPTER EVIDENCE
{json.dumps(local_story_evidence, ensure_ascii=False, separators=(",", ":"))}
This grounded evidence records why the complete-wall reading and moment cut admitted this material.
Its absence from the condensed global thesis is not evidence that the chapter is irrelevant. It is
still not a quota: retain only pictures whose visible contribution earns runtime, and prefer lived
scenes over documents or setup that merely prove the same event happened.

"""
    return f"""You are making {memory_label}, a {memory_type}.

This is the final ASSET cut. The moments were a tentative shortlist whose complete reservoirs are
now open. They are not a one-visual-per-moment quota. You may retain several genuinely different
assets from one rich moment and retain none from a weaker shortlisted moment. Choose at most
{capacity} assets total, including the {len(required)} assets already admitted below. Therefore
return at most {remaining} additional assets. Keep the sequence chronological.

REQUIRED ASSETS ALREADY ADMITTED
{json.dumps(required, separators=(",", ":"))}
They consume capacity. Do not return them again and do not compare against them as rejected.

EDITORIAL BRIEF
{editorial_brief}

THESIS
{json.dumps(thesis, ensure_ascii=False, separators=(",", ":"))}

{local_story_block}Choose visible scenes that carry the thesis, relationships, change, place, action, expression, or
atmosphere. Reject near-duplicates, weaker framings, arbitrary objects, household inventory,
screens, documents, and setup evidence when a lived scene carries the same fact. A second asset
from one moment must add a genuinely different beat or useful visual progression. Do not fill a
quota merely because room exists. A closer view, alternate framing, readable title, or clearer
object detail does not create a second beat when the action and relationship are unchanged. A
shortfall is allowed when the remaining material is redundant or evidentiary. Before answering,
compare the weakest optional keep with the strongest rejected alternative.

{_MEDIA_PRIORITY_GUIDANCE}

{_UNGROUNDED_INTERACTION_GUIDANCE}

A favourite is direct owner evidence, not automatic admission of its whole moment. You may drop a
shortlisted moment entirely. If you retain any asset from a moment that contains favourites, at
least one retained asset from that moment must be a favourite.

ASSET WALL
{wall}

Return only one complete JSON object with exactly these keys:
{json.dumps(shape, separators=(",", ":"))}
The schema_version value must be exactly {FINAL_ASSET_CUT_SCHEMA}."""


def read_final_asset_cut(
    raw: str,
    candidates: Sequence[FineCutCandidate],
    *,
    capacity: int,
    required_aliases: Sequence[str] = (),
    project_favourites: bool = False,
) -> dict[str, Any]:
    """Validate and merge the model's optional assets with runtime admissions."""
    payload = final_json_object(raw)
    if payload is None or set(payload) != {
        "schema_version",
        "keep",
        "comparisons",
        "overall_reason",
    }:
        raise ValueError("final asset cut has the wrong envelope")
    if payload.get("schema_version") != FINAL_ASSET_CUT_SCHEMA:
        raise ValueError("final asset cut has the wrong schema version")

    by_alias = {candidate.alias: candidate for candidate in candidates}
    if len(by_alias) != len(candidates):
        raise ValueError("final asset cut aliases must be unique")
    required = tuple(dict.fromkeys(required_aliases))
    if not set(required) <= set(by_alias):
        raise ValueError("required final-cut assets are not in the wall")
    room = capacity - len(required)
    if room < 0:
        raise ValueError("required final-cut assets exceed duration capacity")

    raw_keep = payload.get("keep")
    if not isinstance(raw_keep, list):
        raise ValueError("final asset keep rows must be a list")
    optional: list[dict[str, str]] = []
    seen = set(required)
    discarded_required_echoes = 0
    discarded_duplicate_keeps = 0
    for row in raw_keep:
        if not isinstance(row, dict) or set(row) != {"asset_id", "reason"}:
            raise ValueError("final asset keep row has the wrong shape")
        alias = row.get("asset_id")
        reason = bounded_model_text(row.get("reason"), max_chars=_MAX_REASON_CHARS)
        if alias not in by_alias or reason is None:
            raise ValueError("final asset keep row is not grounded")
        if alias in required:
            discarded_required_echoes += 1
            continue
        if alias in seen:
            discarded_duplicate_keeps += 1
            continue
        seen.add(alias)
        optional.append({"asset_id": alias, "reason": reason})
    if len(optional) > room:
        raise ValueError("final asset cut exceeds remaining duration capacity")

    projected_favourite_assets = 0
    if project_favourites:
        projected_favourite_assets = _project_favourite_representation(
            by_alias,
            seen,
            optional,
            required=set(required),
            capacity=capacity,
        )
    _require_favourite_representation(by_alias, seen)

    comparisons, discarded_comparisons = _read_comparisons(
        payload.get("comparisons"), by_alias, seen, set(required)
    )
    overall = bounded_model_text(payload.get("overall_reason"), max_chars=_MAX_REASON_CHARS)
    if overall is None:
        raise ValueError("final asset cut overall reason is unsafe")

    reasons = {row["asset_id"]: row["reason"] for row in optional}
    reasons.update(
        dict.fromkeys(required, "Admitted by the runtime before the optional asset cut.")
    )
    ordered = [
        {"asset_id": candidate.alias, "reason": reasons[candidate.alias]}
        for candidate in candidates
        if candidate.alias in seen
    ]
    return {
        "keep": ordered,
        "required_asset_ids": list(required),
        "discarded_required_echoes": discarded_required_echoes,
        "discarded_duplicate_keeps": discarded_duplicate_keeps,
        "projected_favourite_assets": projected_favourite_assets,
        "comparisons": comparisons,
        "discarded_comparisons": discarded_comparisons,
        "overall_reason": overall,
    }


def mark_closing_candidate(
    candidates: Sequence[FineCutCandidate],
    *,
    kept_aliases: Sequence[str],
) -> tuple[FineCutCandidate, ...]:
    """Mark the chronologically last kept row so every wall says where the memory ends."""
    by_alias = {candidate.alias: candidate for candidate in candidates}
    kept = tuple(dict.fromkeys(kept_aliases))
    if len(by_alias) != len(candidates) or not set(kept) <= set(by_alias):
        raise ValueError("closing marker aliases are not grounded")
    cleared = tuple(replace(candidate, closes_memory=False) for candidate in candidates)
    if not kept:
        return cleared
    closer = max(
        (candidate for candidate in cleared if candidate.alias in set(kept)),
        key=lambda candidate: (candidate.taken_at, candidate.alias),
    )
    return tuple(
        replace(candidate, closes_memory=True) if candidate.alias == closer.alias else candidate
        for candidate in cleared
    )


def _closer_alternative(
    reservoir: Sequence[FineCutCandidate],
    pick: FineCutCandidate,
    *,
    kept: set[str],
    required: set[str],
) -> tuple[FineCutCandidate, str] | None:
    """Choose the closer's replacement: a favourite first, then the brightest peer."""
    # "Within 10% of the pick on the other recorded merits" collapses to "no downgrade":
    # the only other recorded merit is the three-step media ladder, where one step down
    # already costs a third. Favourites never yield to a non-favourite, so a dark
    # favourite closer can only be swapped for another favourite of its own moment.
    eligible = [
        candidate
        for candidate in reservoir
        if candidate.alias not in kept
        and candidate.alias not in required
        and candidate.luminance is not None
        and _media_priority(candidate) >= _media_priority(pick)
        and (candidate.favourite or not pick.favourite)
    ]
    order = sorted(eligible, key=lambda item: (-(item.luminance or 0), item.taken_at, item.alias))
    favourites = [candidate for candidate in order if candidate.favourite]
    if favourites:
        return favourites[0], "The closing moment holds a favourite the dark pick passed over."
    brighter = [
        candidate for candidate in order if (candidate.luminance or 0) > (pick.luminance or 0)
    ]
    if brighter:
        return brighter[0], "A brighter sibling of the closing moment carries the same merits."
    return None


def apply_closer_luminance_swap(
    candidates: Sequence[FineCutCandidate],
    cut: dict[str, Any],
) -> dict[str, Any]:
    """Replace a below-median-luminance closing pick with the best peer of its own moment."""
    by_alias = {candidate.alias: candidate for candidate in candidates}
    rows = cut.get("keep")
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("reason"), str) for row in rows
    ):
        raise ValueError("closer swap needs a reasoned keep list")
    aliases = [row["asset_id"] for row in rows]
    if (
        len(by_alias) != len(candidates)
        or len(set(aliases)) != len(aliases)
        or not set(aliases) <= set(by_alias)
    ):
        raise ValueError("closer swap aliases are not grounded")
    required = set(cut.get("required_asset_ids", ()))
    if not aliases:
        return {**cut, "closer_swap": None}
    pick = max(
        (by_alias[alias] for alias in aliases),
        key=lambda candidate: (candidate.taken_at, candidate.alias),
    )
    reservoir = [candidate for candidate in candidates if candidate.moment_id == pick.moment_id]
    lit = sorted(candidate.luminance for candidate in reservoir if candidate.luminance is not None)
    if pick.alias in required or pick.luminance is None or len(lit) < 2:
        return {**cut, "closer_swap": None}
    median = (lit[(len(lit) - 1) // 2] + lit[len(lit) // 2]) / 2
    if pick.luminance >= median:
        return {**cut, "closer_swap": None}
    chosen = _closer_alternative(
        reservoir,
        pick,
        kept=set(aliases),
        required=required,
    )
    if chosen is None:
        return {**cut, "closer_swap": None}
    replacement, reason = chosen
    kept_aliases = {alias for alias in aliases if alias != pick.alias} | {replacement.alias}
    reordered = [candidate.alias for candidate in candidates if candidate.alias in kept_aliases]
    reason_by_alias = {row["asset_id"]: row["reason"] for row in rows}
    reason_by_alias[replacement.alias] = reason
    return {
        **cut,
        "keep": [{"asset_id": alias, "reason": reason_by_alias[alias]} for alias in reordered],
        "closer_swap": {
            "moment_id": pick.moment_id,
            "reservoir_median_luminance": median,
            "before": {"asset_id": pick.alias, "luminance": pick.luminance},
            "after": {"asset_id": replacement.alias, "luminance": replacement.luminance},
            "reason": reason,
        },
    }


def apply_final_moment_cap(
    candidates: Sequence[FineCutCandidate],
    cut: dict[str, Any],
    *,
    max_per_moment: int = 2,
) -> dict[str, Any]:
    """Project an asset cut under the anti-domination cap without refilling it."""
    if not isinstance(max_per_moment, int) or isinstance(max_per_moment, bool):
        raise ValueError("final moment cap must be an integer")
    if max_per_moment < 1:
        raise ValueError("final moment cap must be positive")
    by_alias = {candidate.alias: candidate for candidate in candidates}
    if len(by_alias) != len(candidates):
        raise ValueError("final moment cap aliases must be unique")
    rows = cut.get("keep")
    if not isinstance(rows, list):
        raise ValueError("final moment cap needs a keep list")
    aliases = [row.get("asset_id") for row in rows if isinstance(row, dict)]
    if (
        len(aliases) != len(rows)
        or any(not isinstance(alias, str) or alias not in by_alias for alias in aliases)
        or len(set(aliases)) != len(aliases)
    ):
        raise ValueError("final moment cap keep rows are not grounded")
    required = tuple(dict.fromkeys(cut.get("required_asset_ids", ())))
    if any(not isinstance(alias, str) for alias in required) or not set(required) <= set(aliases):
        raise ValueError("final moment cap required aliases are not selected")

    rows_by_moment: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_moment.setdefault(by_alias[row["asset_id"]].moment_id, []).append(row)
    required_set = set(required)
    survivors: set[str] = set()
    overfull_moments = 0
    required_overflow_moments: list[str] = []
    for moment_id, moment_rows in rows_by_moment.items():
        if len(moment_rows) <= max_per_moment:
            survivors.update(row["asset_id"] for row in moment_rows)
            continue
        overfull_moments += 1
        required_rows = [row for row in moment_rows if row["asset_id"] in required_set]
        survivors.update(row["asset_id"] for row in required_rows)
        if len(required_rows) >= max_per_moment:
            if len(required_rows) > max_per_moment:
                required_overflow_moments.append(moment_id)
            continue
        optional_rows = [row for row in moment_rows if row["asset_id"] not in required_set]
        optional_rows.sort(
            key=lambda row: (
                -_media_priority(by_alias[row["asset_id"]]),
                -int(by_alias[row["asset_id"]].favourite),
                by_alias[row["asset_id"]].taken_at,
                row["asset_id"],
            )
        )
        survivors.update(
            row["asset_id"] for row in optional_rows[: max_per_moment - len(required_rows)]
        )

    removed = [alias for alias in aliases if alias not in survivors]
    capped = {
        **cut,
        "keep": [row for row in rows if row["asset_id"] in survivors],
        "moment_cap": {
            "max_per_moment": max_per_moment,
            "overfull_moments": overfull_moments,
            "removed_asset_ids": removed,
            "required_overflow_moments": required_overflow_moments,
        },
    }
    comparisons = cut.get("comparisons")
    if isinstance(comparisons, list):
        capped["comparisons"] = [
            row
            for row in comparisons
            if isinstance(row, dict) and row.get("kept_asset_id") in survivors
        ]
    return capped


def _day_ceiling_reason(
    candidate: FineCutCandidate,
    *,
    day: str,
    held: int,
    max_per_day: int,
) -> str:
    exposure = (
        f"mean preview luminance {candidate.luminance}"
        if candidate.luminance is not None
        else "no recorded luminance"
    )
    return (
        f"The day {day} held {held} final assets over its {max_per_day}-asset ceiling, and this "
        f"non-favourite frame ranked weakest on exposure then keep order ({exposure})."
    )


def _day_ceiling_removals(
    day_rows: Sequence[dict[str, Any]],
    *,
    by_alias: dict[str, FineCutCandidate],
    day: str,
    max_per_day: int,
    exempt: set[str],
) -> tuple[list[dict[str, Any]], bool]:
    """Choose one day's weakest droppable keeps, and say whether exemptions still overfill it."""
    overflow = len(day_rows) - max_per_day
    if overflow <= 0:
        return [], False
    order = {row["asset_id"]: index for index, row in enumerate(day_rows)}
    droppable = sorted(
        (row for row in day_rows if row["asset_id"] not in exempt),
        key=lambda row: (
            by_alias[row["asset_id"]].luminance is None,
            by_alias[row["asset_id"]].luminance or 0,
            -order[row["asset_id"]],
        ),
    )
    return [
        {
            "asset_id": row["asset_id"],
            "day": day,
            "reason": _day_ceiling_reason(
                by_alias[row["asset_id"]],
                day=day,
                held=len(day_rows),
                max_per_day=max_per_day,
            ),
        }
        for row in droppable[:overflow]
    ], len(droppable) < overflow


def apply_final_day_ceiling(
    candidates: Sequence[FineCutCandidate],
    cut: dict[str, Any],
    *,
    max_per_day: int = _FINAL_DAY_ASSET_CEILING,
) -> dict[str, Any]:
    """Hold each calendar day to a ceiling of final assets, without refilling the freed room.

    Favourites and runtime obligations are never dropped, but they still occupy the day's
    slots, so a day carrying two favourites keeps exactly one other asset. Freed duration
    shrinks to the material; nothing is promoted to replace what the ceiling sheds.
    """
    if not isinstance(max_per_day, int) or isinstance(max_per_day, bool) or max_per_day < 1:
        raise ValueError("final day ceiling must be a positive integer")
    by_alias = {candidate.alias: candidate for candidate in candidates}
    rows = cut.get("keep")
    if len(by_alias) != len(candidates) or not isinstance(rows, list):
        raise ValueError("final day ceiling needs a keep list")
    aliases = [row.get("asset_id") for row in rows if isinstance(row, dict)]
    if (
        len(aliases) != len(rows)
        or any(not isinstance(alias, str) or alias not in by_alias for alias in aliases)
        or len(set(aliases)) != len(aliases)
    ):
        raise ValueError("final day ceiling keep rows are not grounded")
    required = set(cut.get("required_asset_ids", ()))
    if not required <= set(aliases):
        raise ValueError("final day ceiling required aliases are not selected")

    exempt = required | {alias for alias in aliases if by_alias[alias].favourite}
    rows_by_day: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_day.setdefault(by_alias[row["asset_id"]].taken_at.date(), []).append(row)
    removed: list[dict[str, Any]] = []
    overfull_days = 0
    favourite_overflow_days: list[str] = []
    for day in sorted(rows_by_day):
        if len(rows_by_day[day]) <= max_per_day:
            continue
        overfull_days += 1
        day_removed, still_overfull = _day_ceiling_removals(
            rows_by_day[day],
            by_alias=by_alias,
            day=day.isoformat(),
            max_per_day=max_per_day,
            exempt=exempt,
        )
        removed.extend(day_removed)
        if still_overfull:
            favourite_overflow_days.append(day.isoformat())

    dropped = {row["asset_id"] for row in removed}
    survivors = [row for row in rows if row["asset_id"] not in dropped]
    held = {
        **cut,
        "keep": survivors,
        "day_ceiling": {
            "max_per_day": max_per_day,
            "overfull_days": overfull_days,
            "removed_asset_ids": [row["asset_id"] for row in removed],
            "removed": removed,
            "favourite_overflow_days": favourite_overflow_days,
        },
    }
    comparisons = cut.get("comparisons")
    if isinstance(comparisons, list):
        kept = {row["asset_id"] for row in survivors}
        held["comparisons"] = [
            row
            for row in comparisons
            if isinstance(row, dict) and row.get("kept_asset_id") in kept
        ]
    return held


def _record_asset_ids(record: Any, field: str) -> tuple[str, ...]:
    """Read one trim pass record's asset-id list, whether it holds aliases or reasoned rows."""
    rows = record.get(field) if isinstance(record, dict) else None
    if not isinstance(rows, list):
        return ()
    aliases = [row.get("asset_id") if isinstance(row, dict) else row for row in rows]
    return tuple(alias for alias in aliases if isinstance(alias, str))


def _deliberation_removed_aliases(record: Any) -> tuple[str, ...]:
    rows = record.get("iterations") if isinstance(record, dict) else None
    if not isinstance(rows, list):
        return ()
    return tuple(
        alias
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("removed"), list)
        for alias in row["removed"]
        if isinstance(alias, str)
    )


def _erasing_trim_pass(cut: dict[str, Any]) -> dict[str, str]:
    """Name, per alias, the last trim pass whose own record shows it leaving the cut.

    Only the passes that write a removal record can be attributed. An asset the final asset
    cut never chose is attributed to nothing, which is itself the answer: no pass decided it.
    """
    erased: dict[str, str] = {}
    for name, field in (
        ("moment_cap", "removed_asset_ids"),
        ("day_ceiling", "removed_asset_ids"),
        ("initial_global_review", "cut"),
        ("global_review", "cut"),
    ):
        for alias in _record_asset_ids(cut.get(name), field):
            erased[alias] = name
    closer = cut.get("closer_swap")
    before = closer.get("before") if isinstance(closer, dict) else None
    if isinstance(before, dict) and isinstance(before.get("asset_id"), str):
        erased[before["asset_id"]] = "closer_swap"
    for alias in _deliberation_removed_aliases(cut.get("deliberation")):
        erased[alias] = "deliberation"
    return erased


def _floor_restoration(
    pool: Sequence[FineCutCandidate],
    *,
    originally_cut: set[str],
) -> tuple[FineCutCandidate, str]:
    """Choose a moment's one representative: star, then the cut's own pick, then exposure."""

    def lit(candidate: FineCutCandidate) -> int:
        recorded = candidate.luminance or 0
        return recorded if recorded >= _DARK_FRAME_LUMINANCE else 0

    pick = min(
        pool,
        key=lambda candidate: (
            not candidate.favourite,
            candidate.alias not in originally_cut,
            -lit(candidate),
            candidate.taken_at,
            candidate.alias,
        ),
    )
    if pick.favourite:
        return pick, "favourite"
    if pick.alias in originally_cut:
        return pick, "original-pick"
    if (pick.luminance or 0) >= _DARK_FRAME_LUMINANCE:
        return pick, "brightest"
    return pick, "earliest"


def _floor_keep_rows(
    cut: dict[str, Any],
    *,
    by_alias: dict[str, FineCutCandidate],
) -> tuple[list[dict[str, Any]], list[str]]:
    rows = cut.get("keep")
    if len(by_alias) != len(set(by_alias)) or not isinstance(rows, list):
        raise ValueError("kept moment floor needs a keep list")
    aliases = [row.get("asset_id") for row in rows if isinstance(row, dict)]
    if (
        len(aliases) != len(rows)
        or any(not isinstance(alias, str) or alias not in by_alias for alias in aliases)
        or len(set(aliases)) != len(aliases)
        or any(not isinstance(row.get("reason"), str) for row in rows)
    ):
        raise ValueError("kept moment floor keep rows are not grounded")
    return rows, aliases


def _floor_ceiling_conflicts(
    restored: Sequence[dict[str, Any]],
    *,
    kept_aliases: Sequence[str],
    by_alias: dict[str, FineCutCandidate],
    max_per_day: int,
) -> list[dict[str, Any]]:
    """Say which restorations push their day past the ceiling that already ran above.

    The ceiling yields: it may shed a day's weakest frame, but it may not be the reason a
    moment the chapter cut kept shows nothing at all. One asset per erased moment stands.
    """
    held_by_day: dict[Any, int] = {}
    for alias in kept_aliases:
        day = by_alias[alias].taken_at.date()
        held_by_day[day] = held_by_day.get(day, 0) + 1
    return [
        {
            "moment_id": row["moment_id"],
            "asset_id": row["asset_id"],
            "day": by_alias[row["asset_id"]].taken_at.date().isoformat(),
            "max_per_day": max_per_day,
            "held": held_by_day[by_alias[row["asset_id"]].taken_at.date()],
        }
        for row in restored
        if held_by_day[by_alias[row["asset_id"]].taken_at.date()] > max_per_day
    ]


def apply_kept_moment_floor(
    candidates: Sequence[FineCutCandidate],
    cut: dict[str, Any],
    *,
    kept_moment_ids: Sequence[str],
    waived_aliases: Sequence[str] = (),
    max_per_day: int = _FINAL_DAY_ASSET_CEILING,
) -> dict[str, Any]:
    """Give back one asset to every moment the chapter cut kept and the trim stack erased.

    The chapter cut is the editorial authority over which moments the film contains: a
    moment it kept may only leave when a pass cuts the MOMENT and records why. The trim
    stack below it is reject-only and asset-level, so a moment can vanish from the wall
    with no moment-level decision written anywhere. Restoration is deterministic and
    bounded to one asset per erased moment. The two correctness cuts -- an anti-resurrection
    refusal and a same-picture dedup -- are the only ones that may erase a moment silently,
    and the caller names their aliases here.
    """
    if not isinstance(max_per_day, int) or isinstance(max_per_day, bool) or max_per_day < 1:
        raise ValueError("kept moment floor day ceiling must be a positive integer")
    by_alias = {candidate.alias: candidate for candidate in candidates}
    if len(by_alias) != len(candidates):
        raise ValueError("kept moment floor aliases must be unique")
    rows, aliases = _floor_keep_rows(cut, by_alias=by_alias)
    moments = tuple(dict.fromkeys(str(moment_id) for moment_id in kept_moment_ids))
    waived = set(waived_aliases)
    represented = {by_alias[alias].moment_id for alias in aliases}
    erased_by = _erasing_trim_pass(cut)
    originally_cut = set(aliases) | set(erased_by)
    by_moment: dict[str, list[FineCutCandidate]] = {}
    for candidate in candidates:
        by_moment.setdefault(candidate.moment_id, []).append(candidate)

    reason_by_alias = {row["asset_id"]: row["reason"] for row in rows}
    restored: list[dict[str, Any]] = []
    waived_rows: list[dict[str, Any]] = []
    for moment_id in moments:
        members = by_moment.get(moment_id, [])
        if moment_id in represented or not members:
            continue
        pool = [candidate for candidate in members if candidate.alias not in waived]
        cut_here = [candidate.alias for candidate in members if candidate.alias in erased_by]
        if not pool:
            waived_rows.append(
                {
                    "moment_id": moment_id,
                    "asset_ids": [candidate.alias for candidate in members],
                    "reason": (
                        "Every asset of this moment was removed by a correctness pass -- an "
                        "anti-resurrection refusal or a same-picture dedup -- which no floor "
                        "may undo."
                    ),
                }
            )
            continue
        pick, basis = _floor_restoration(pool, originally_cut=originally_cut)
        restored.append(
            {
                "moment_id": moment_id,
                "asset_id": pick.alias,
                "erased_by": erased_by[cut_here[0]] if cut_here else "never-selected",
                "basis": basis,
            }
        )
        reason_by_alias[pick.alias] = (
            f"The chapter cut kept {moment_id} and the trim stack left it with no asset; "
            f"this is its {basis} frame."
        )

    kept = set(aliases) | {row["asset_id"] for row in restored}
    ordered = [candidate.alias for candidate in candidates if candidate.alias in kept]
    return {
        **cut,
        "keep": [{"asset_id": alias, "reason": reason_by_alias[alias]} for alias in ordered],
        "moment_floor": {
            "kept_moments": len(moments),
            "max_per_day": max_per_day,
            "restored": restored,
            "waived": waived_rows,
            "ceiling_yielded": _floor_ceiling_conflicts(
                restored,
                kept_aliases=ordered,
                by_alias=by_alias,
                max_per_day=max_per_day,
            ),
        },
    }


def final_asset_audit_prompt(
    candidates: Sequence[FineCutCandidate],
    *,
    current_aliases: Sequence[str],
    editorial_brief: str,
    thesis: dict[str, Any],
    review_focus: Sequence[dict[str, Any]] = (),
) -> str:
    """Ask for a neutral, evidence-gated audit of the assembled current film."""
    by_alias = {candidate.alias: candidate for candidate in candidates}
    current = tuple(dict.fromkeys(current_aliases))
    if len(by_alias) != len(candidates) or len(current) != len(tuple(current_aliases)):
        raise ValueError("final asset audit needs unique aliases")
    if not set(current) <= set(by_alias):
        raise ValueError("final asset audit aliases are not grounded")
    focus_aliases = {str(alias) for focus in review_focus for alias in focus.get("asset_ids", ())}
    if not focus_aliases <= set(current):
        raise ValueError("final asset audit focus aliases are not grounded")
    wall = "\n".join(
        candidate.wall_line() for candidate in candidates if candidate.alias in current
    )
    focus_block = ""
    if review_focus:
        focus_block = f"""

MECHANICAL REVIEW FOCUS
{json.dumps(tuple(review_focus), ensure_ascii=False, separators=(",", ":"))}
These are mechanical attention cues, not defects.
Each cited group is an attention cue, not itself a defect. Assess every cited focus against the
current sequence, but do not assume that every focus needs a finding.
Inspect whether its visuals repeat the same contribution. Return stable when their setup, action,
payoff, relationship, place, or visible-progression beats are genuinely distinct."""
    shape = {
        "schema_version": FINAL_ASSET_AUDIT_SCHEMA,
        "verdict": "stable or revise",
        "findings": [
            {
                "kind": "one allowed finding kind",
                "asset_ids": ["A001"],
                "visible_defect": "what the cited current visuals specifically repeat or fail to carry",
                "missing_contribution": "the contribution the current film lacks, if available",
            }
        ],
        "overall_reason": "why the current corpus is stable or crosses the revision threshold",
    }
    return f"""You are verifying the assembled draft of a personal memory against its own visible
evidence. This is a neutral audit, not a request to invent improvements. Stable is the default
verdict. Return revise only when a specific visible defect crosses the threshold below.

EDITORIAL BRIEF
{editorial_brief}

THESIS
{json.dumps(thesis, ensure_ascii=False, separators=(",", ":"))}
{focus_block}

Every revision finding must cite exact current aliases and state both the observed defect and the
contribution the current film fails to carry. Allowed kinds are:
{json.dumps(sorted(_FINAL_AUDIT_FINDING_KINDS), separators=(",", ":"))}

Ground findings only in the descriptions and metadata below. Do not presume that a better reservoir
candidate exists. A missing contribution is a conditional search target, not evidence that an image
showing it exists. Do not flag a face, selfie, landscape, quiet record, ordinary scene, or favourite
merely because of its class. Repetition requires the same contribution, not just the same person.
An environmental visual matters when place, route, weather, scale, arrival, or atmosphere is part of
the memory; it is not a quota. A quiet record may uniquely establish consequential change.

Use revise only for grounded repetition, subject or pose overweight, missing place or progression,
missing action or event, missing relationship contribution, weak explanatory evidence, or a visible
sequence gap. General wishes such as more variety, more beauty, or better coverage are invalid. Do
not infer identity, relationship, causality, or significance beyond the supplied evidence.

CURRENT WHOLE SEQUENCE
{wall}

When verdict is stable, findings must be an empty list. When verdict is revise, return one to eight
findings. Findings are concise audit evidence, not hidden chain-of-thought. Return only one complete
JSON object with exactly these keys:
{json.dumps(shape, separators=(",", ":"))}
The schema_version value must be exactly {FINAL_ASSET_AUDIT_SCHEMA}."""


def runtime_final_asset_audit_findings(
    candidates: Sequence[FineCutCandidate],
    *,
    current_aliases: Sequence[str],
) -> list[dict[str, Any]]:
    """Return non-verdict attention cues for mechanically concentrated selections."""
    by_alias = {candidate.alias: candidate for candidate in candidates}
    current = tuple(dict.fromkeys(current_aliases))
    if len(by_alias) != len(candidates) or len(current) != len(tuple(current_aliases)):
        raise ValueError("runtime final asset audit needs unique aliases")
    if not set(current) <= set(by_alias):
        raise ValueError("runtime final asset audit aliases are not grounded")
    selected = sorted(
        (by_alias[alias] for alias in current),
        key=lambda candidate: (candidate.taken_at, candidate.alias),
    )
    focus_groups: list[dict[str, Any]] = []
    by_moment: dict[str, list[FineCutCandidate]] = {}
    for candidate in selected:
        by_moment.setdefault(candidate.moment_id, []).append(candidate)
    for moment in by_moment.values():
        if len(moment) < _AUDIT_FOCUS_MIN_MOMENT_ASSETS:
            continue
        focus_groups.append(
            {
                "focus_kind": "same_moment",
                "asset_ids": [candidate.alias for candidate in moment],
                "observation": (f"{len(moment)} selected assets come from one production moment."),
                "review_question": (
                    "Do they carry distinct setup, action, payoff, place, relationship, or "
                    "visible-progression beats, or repeat one contribution?"
                ),
            }
        )
    focused_alias_sets = {frozenset(focus["asset_ids"]) for focus in focus_groups}

    by_episode: dict[str, list[FineCutCandidate]] = {}
    for candidate in selected:
        if candidate.episode_id is not None:
            by_episode.setdefault(candidate.episode_id, []).append(candidate)
    for episode in by_episode.values():
        if len(episode) < _AUDIT_FOCUS_MIN_EPISODE_ASSETS:
            continue
        episode_aliases = [candidate.alias for candidate in episode]
        if frozenset(episode_aliases) in focused_alias_sets:
            continue
        focus_groups.append(
            {
                "focus_kind": "same_episode",
                "asset_ids": episode_aliases,
                "observation": (
                    f"{len(episode)} selected assets come from one production episode."
                ),
                "review_question": (
                    "Do they provide useful variation within the event or trip, or repeat the "
                    "same subject, pose, action, place, or narrative contribution?"
                ),
            }
        )
        focused_alias_sets.add(frozenset(episode_aliases))

    left = 0
    while left < len(selected):
        right = left
        while (
            right + 1 < len(selected)
            and selected[right + 1].taken_at - selected[left].taken_at <= _AUDIT_FOCUS_WINDOW
        ):
            right += 1
        window = tuple(selected[left : right + 1])
        if len(window) < _AUDIT_FOCUS_MIN_ASSETS:
            left += 1
            continue
        cited = [candidate.alias for candidate in window[:8]]
        cited_set = frozenset(cited)
        if cited_set in focused_alias_sets:
            left = right + 1
            continue
        focus_groups.append(
            {
                "focus_kind": "dense_window",
                "asset_ids": cited,
                "observation": (
                    f"{len(window)} assets occur inside one seven-day window in the current draft."
                ),
                "review_question": (
                    "Do they repeat the same contribution, or do they carry distinct setup, "
                    "action, payoff, relationship, place, or visible-progression beats?"
                ),
            }
        )
        focused_alias_sets.add(cited_set)
        left = right + 1
    return [
        {"focus_id": f"R{index:03d}", **focus} for index, focus in enumerate(focus_groups, start=1)
    ]


def visual_final_asset_audit_groups(
    candidates: Sequence[FineCutCandidate],
    *,
    current_aliases: Sequence[str],
    review_focus: Sequence[dict[str, Any]],
    max_groups: int = _VISUAL_AUDIT_MAX_GROUPS,
) -> tuple[dict[str, Any], ...]:
    """Choose bounded visual walls without declaring any of them defective."""
    by_alias = {candidate.alias: candidate for candidate in candidates}
    current = tuple(dict.fromkeys(current_aliases))
    if len(by_alias) != len(candidates) or len(current) != len(tuple(current_aliases)):
        raise ValueError("visual final asset audit needs unique aliases")
    if not set(current) <= set(by_alias):
        raise ValueError("visual final asset audit aliases are not grounded")
    if not 1 <= max_groups <= _VISUAL_AUDIT_MAX_GROUPS:
        raise ValueError("visual final asset audit group cap must be between one and eight")
    if len(current) < 2:
        return ()
    if len(current) <= _VISUAL_AUDIT_WHOLE_WALL_MAX_ASSETS or not review_focus:
        return (
            {
                "group_id": "V001",
                "focus_kind": "whole_sequence",
                "asset_ids": list(current),
                "observation": f"The complete current cut contains {len(current)} assets.",
                "review_question": (
                    "Does the whole visible sequence contain a concrete repetition or coverage "
                    "defect?"
                ),
            },
        )

    current_order = {alias: index for index, alias in enumerate(current)}
    unique_sets: set[frozenset[str]] = set()
    eligible: list[tuple[int, dict[str, Any]]] = []
    for focus in review_focus:
        raw_aliases = focus.get("asset_ids")
        if not isinstance(raw_aliases, list) or any(
            not isinstance(alias, str) for alias in raw_aliases
        ):
            raise ValueError("visual final asset audit focus aliases are invalid")
        aliases = tuple(dict.fromkeys(raw_aliases))
        if len(aliases) != len(raw_aliases) or not set(aliases) <= set(current):
            raise ValueError("visual final asset audit focus aliases are not grounded")
        alias_set = frozenset(aliases)
        if len(aliases) < 2 or alias_set in unique_sets:
            continue
        unique_sets.add(alias_set)
        ordered_aliases = sorted(aliases, key=current_order.__getitem__)
        eligible.append(
            (
                current_order[ordered_aliases[0]],
                {
                    "focus_kind": str(focus.get("focus_kind", "mechanical_focus")),
                    "asset_ids": ordered_aliases,
                    "observation": str(focus.get("observation", "Mechanical attention cue.")),
                    "review_question": str(
                        focus.get(
                            "review_question",
                            "Do these visuals repeat one contribution or carry distinct beats?",
                        )
                    ),
                },
            )
        )
    if not eligible:
        return visual_final_asset_audit_groups(
            candidates,
            current_aliases=current,
            review_focus=(),
            max_groups=max_groups,
        )
    chosen = sorted(eligible, key=lambda item: (-len(item[1]["asset_ids"]), item[0]))[:max_groups]
    chosen.sort(key=lambda item: item[0])
    return tuple(
        {"group_id": f"V{index:03d}", **group}
        for index, (_position, group) in enumerate(chosen, start=1)
    )


def visual_final_asset_audit_prompt(
    candidates: Sequence[FineCutCandidate],
    *,
    current_aliases: Sequence[str],
    group: dict[str, Any],
    tile_mapping: Sequence[tuple[int, str]],
    editorial_brief: str,
    thesis: dict[str, Any],
    local_story_evidence: Sequence[dict[str, Any]] = (),
    complete_local_story: bool = False,
) -> str:
    """Ask for a neutral pixel-grounded audit of one bounded final-wall group."""
    by_alias = {candidate.alias: candidate for candidate in candidates}
    current = tuple(dict.fromkeys(current_aliases))
    group_aliases = tuple(group.get("asset_ids", ()))
    mapped_aliases = tuple(alias for _number, alias in tile_mapping)
    if (
        len(by_alias) != len(candidates)
        or len(current) != len(tuple(current_aliases))
        or not set(current) <= set(by_alias)
        or not group_aliases
        or len(set(group_aliases)) != len(group_aliases)
        or not set(group_aliases) <= set(current)
        or mapped_aliases != group_aliases
        or len({number for number, _alias in tile_mapping}) != len(tile_mapping)
    ):
        raise ValueError("visual final asset audit group or tile mapping is not grounded")
    mapping = "\n".join(f"tile {number} = {alias}" for number, alias in tile_mapping)
    wall = "\n".join(by_alias[alias].wall_line() for alias in group_aliases)
    evidence_heading = "LOCAL STORY EVIDENCE" if local_story_evidence else "THESIS"
    story_evidence: Any = local_story_evidence if local_story_evidence else thesis
    scope = (
        "the assembled draft of one complete chapter"
        if complete_local_story
        else "one bounded part of the assembled draft"
    )
    attention_cue = (
        ""
        if complete_local_story
        else (
            "\nMECHANICAL ATTENTION CUE\n"
            + json.dumps(group, ensure_ascii=False, separators=(",", ":"))
            + "\nThis cue selects what to inspect; it is not itself a defect.\n"
        )
    )
    inspection = (
        "Inspect this chapter sequence as a whole."
        if complete_local_story
        else "Inspect the visible group as part of one film."
    )
    shape = {
        "schema_version": FINAL_VISUAL_ASSET_AUDIT_SCHEMA,
        "verdict": "stable or revise",
        "findings": [
            {
                "kind": "one allowed finding kind",
                "asset_ids": ["A001"],
                "visible_defect": "what the cited tiles visibly repeat or fail to carry",
                "missing_contribution": "the grounded visual contribution that is absent",
            }
        ],
        "overall_reason": "why the visible group is stable or crosses the revision threshold",
    }
    return f"""You are visually auditing {scope} of a personal memory. This is neutral verification,
not a request to invent improvements. Stable is the default verdict. Return revise only for a
specific defect visible in the attached numbered contact sheet and grounded by the supplied thesis
or metadata.

EDITORIAL BRIEF
{editorial_brief}

{evidence_heading}
{json.dumps(story_evidence, ensure_ascii=False, separators=(",", ":"))}
{attention_cue}

TILE MAPPING
{mapping}

CURRENT GROUP METADATA
{wall}

{inspection} Repeated faces are not automatically a defect and
recurring people may be the story. A landscape is not a quota. Return revise only when repeated
subject, pose, framing, or event-family contribution visibly has diminishing returns; or when a
place-led, travel, outdoor, family, or special-event thread named by the evidence lacks a necessary
carrier of setting, route, action, progression, scale, or atmosphere. Multiple visuals from a long
trip are useful when they show genuinely different phases. Do not infer identity, relationship,
causality, or significance.

Allowed kinds are:
{json.dumps(sorted(_FINAL_AUDIT_FINDING_KINDS), separators=(",", ":"))}

When stable, findings must be empty. When revise, cite exact aliases from the tile mapping and give
one to eight concise findings. Findings are audit evidence, not hidden chain-of-thought. Return only
one complete JSON object with exactly these keys:
{json.dumps(shape, separators=(",", ":"))}
The schema_version must be exactly {FINAL_VISUAL_ASSET_AUDIT_SCHEMA}."""


def read_final_asset_audit(
    raw: str,
    candidates: Sequence[FineCutCandidate],
    *,
    current_aliases: Sequence[str],
) -> dict[str, Any]:
    """Read a neutral audit whose default valid result is an empty stable verdict."""
    return _read_final_asset_audit(
        raw,
        candidates,
        current_aliases=current_aliases,
        schema_version=FINAL_ASSET_AUDIT_SCHEMA,
    )


def read_visual_final_asset_audit(
    raw: str,
    candidates: Sequence[FineCutCandidate],
    *,
    current_aliases: Sequence[str],
) -> dict[str, Any]:
    """Read the same grounded audit contract under a pixel-specific cache identity."""
    return _read_final_asset_audit(
        raw,
        candidates,
        current_aliases=current_aliases,
        schema_version=FINAL_VISUAL_ASSET_AUDIT_SCHEMA,
    )


def _read_final_asset_audit(
    raw: str,
    candidates: Sequence[FineCutCandidate],
    *,
    current_aliases: Sequence[str],
    schema_version: str,
) -> dict[str, Any]:
    by_alias = {candidate.alias: candidate for candidate in candidates}
    current = tuple(dict.fromkeys(current_aliases))
    if len(by_alias) != len(candidates) or len(current) != len(tuple(current_aliases)):
        raise ValueError("final asset audit needs unique aliases")
    if not set(current) <= set(by_alias):
        raise ValueError("final asset audit aliases are not grounded")
    payload = final_json_object(raw)
    if (
        payload is None
        or set(payload) != {"schema_version", "verdict", "findings", "overall_reason"}
        or payload.get("schema_version") != schema_version
    ):
        raise ValueError("final asset audit has the wrong envelope")
    verdict = payload.get("verdict")
    raw_findings = payload.get("findings")
    if verdict not in {"stable", "revise"} or not isinstance(raw_findings, list):
        raise ValueError("final asset audit verdict or findings are invalid")
    if len(raw_findings) > 8 or (verdict == "stable") != (not raw_findings):
        raise ValueError("stable final asset audit must have no findings")
    findings: list[dict[str, Any]] = []
    for index, row in enumerate(raw_findings, start=1):
        if not isinstance(row, dict) or set(row) != {
            "kind",
            "asset_ids",
            "visible_defect",
            "missing_contribution",
        }:
            raise ValueError("final asset audit finding has the wrong shape")
        kind = row.get("kind")
        asset_ids = row.get("asset_ids")
        visible_defect = bounded_model_text(row.get("visible_defect"), max_chars=_MAX_REASON_CHARS)
        missing_contribution = bounded_model_text(
            row.get("missing_contribution"), max_chars=_MAX_REASON_CHARS
        )
        if (
            kind not in _FINAL_AUDIT_FINDING_KINDS
            or not isinstance(asset_ids, list)
            or not asset_ids
            or len(asset_ids) > 8
            or any(not isinstance(alias, str) for alias in asset_ids)
            or len(set(asset_ids)) != len(asset_ids)
            or not set(asset_ids) <= set(current)
            or visible_defect is None
            or missing_contribution is None
        ):
            raise ValueError("final asset audit finding is not grounded")
        findings.append(
            {
                "finding_id": f"F{index:03d}",
                "kind": kind,
                "asset_ids": asset_ids,
                "visible_defect": visible_defect,
                "missing_contribution": missing_contribution,
            }
        )
    overall = bounded_model_text(payload.get("overall_reason"), max_chars=_MAX_REASON_CHARS)
    if overall is None:
        raise ValueError("final asset audit overall reason is unsafe")
    return {"verdict": verdict, "findings": findings, "overall_reason": overall}


def merge_final_asset_audits(
    audits: Sequence[dict[str, Any]],
    *,
    max_findings: int = 8,
) -> dict[str, Any]:
    """Merge independent grounded audits without treating disagreement as a forced edit."""
    if not 1 <= max_findings <= 8:
        raise ValueError("merged final asset audits allow one to eight findings")
    rows = tuple(audits)
    if not rows:
        raise ValueError("merged final asset audits need at least one source")
    findings: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for audit in rows:
        verdict = audit.get("verdict")
        source_findings = audit.get("findings")
        if verdict not in {"stable", "revise"} or not isinstance(source_findings, list):
            raise ValueError("merged final asset audit source is invalid")
        if (verdict == "stable") != (not source_findings):
            raise ValueError("merged stable final asset audit must have no findings")
        for finding in source_findings:
            if not isinstance(finding, dict):
                raise ValueError("merged final asset audit finding is invalid")
            key = (
                finding.get("kind"),
                tuple(finding.get("asset_ids", ())),
                finding.get("visible_defect"),
                finding.get("missing_contribution"),
            )
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                {
                    "kind": finding.get("kind"),
                    "asset_ids": list(finding.get("asset_ids", ())),
                    "visible_defect": finding.get("visible_defect"),
                    "missing_contribution": finding.get("missing_contribution"),
                }
            )
    discarded = max(0, len(findings) - max_findings)
    findings = findings[:max_findings]
    numbered = [
        {"finding_id": f"F{index:03d}", **finding}
        for index, finding in enumerate(findings, start=1)
    ]
    return {
        "verdict": "revise" if numbered else "stable",
        "findings": numbered,
        "overall_reason": (
            "At least one grounded visual or metadata audit found a concrete revision target."
            if numbered
            else "All grounded visual and metadata audits found the current cut stable."
        ),
        "source_audits": len(rows),
        "discarded_findings": discarded,
    }


def _occasion_day_runs(
    candidates: Sequence[FineCutCandidate],
    *,
    chapter_by_moment: dict[str, str],
) -> tuple[tuple[FineCutCandidate, ...], ...]:
    """Group the reservoir into occasions: photographed days a rest day cannot split."""
    ordered = sorted(candidates, key=lambda candidate: (candidate.taken_at, candidate.alias))
    runs: list[list[FineCutCandidate]] = []
    for candidate in ordered:
        previous = runs[-1][-1] if runs else None
        if (
            previous is not None
            and candidate.taken_at.date() - previous.taken_at.date() <= _OCCASION_DAY_GAP
            and chapter_by_moment.get(candidate.moment_id)
            == chapter_by_moment.get(previous.moment_id)
        ):
            runs[-1].append(candidate)
        else:
            runs.append([candidate])
    return tuple(tuple(run) for run in runs)


def _occasion_day_coverage(
    occasion: tuple[FineCutCandidate, ...],
    *,
    current_set: set[str],
) -> dict[str, Any] | None:
    """Report a multi-day occasion whose cut reaches at most half its photographed days."""
    days = sorted({candidate.taken_at.date() for candidate in occasion})
    represented = {
        candidate.taken_at.date() for candidate in occasion if candidate.alias in current_set
    }
    if len(days) < _OCCASION_MIN_PHOTOGRAPHED_DAYS or 2 * len(represented) > len(days):
        return None
    dark_days = [day for day in days if day not in represented]
    moments_by_day: dict[Any, dict[str, list[FineCutCandidate]]] = {}
    for candidate in occasion:
        moments_by_day.setdefault(candidate.taken_at.date(), {}).setdefault(
            candidate.moment_id, []
        ).append(candidate)
    # One moment per dark day keeps the reopened wall bounded on a 60-moment trip;
    # the day's densest moment is its most grounded sample of what was lost.
    targets = [
        max(
            moments_by_day[day].items(),
            key=lambda row: (len(row[1]), -row[1][0].taken_at.timestamp(), row[0]),
        )
        for day in dark_days
    ]
    aliases = [candidate.alias for _moment_id, members in targets for candidate in members]
    return {
        "focus_kind": "occasion_day_coverage",
        "moment_ids": [moment_id for moment_id, _members in targets],
        "asset_ids": aliases,
        "current_asset_ids": [],
        "selection_limit": _FINAL_MOMENT_ASSET_CAP,
        "owner_evidence": {
            "photographed_days": len(days),
            "represented_days": len(represented),
            "unrepresented_days": [day.isoformat() for day in dark_days],
            "favourite_assets": sum(candidate.favourite for candidate in occasion),
        },
        "observation": (
            f"One continuous occasion spans {len(days)} photographed days in the reservoir "
            f"while the current cut reaches {len(represented)} of them."
        ),
        "review_question": (
            "Does any candidate from an unreached day of this occasion add a distinct "
            "necessary occasion or progression beat, rather than merely filling unused duration?"
        ),
    }


def _person_tokens(candidate: FineCutCandidate) -> tuple[str, ...]:
    """Read the per-run person tokens a wall row carries as `P01:tier=...;relationship=...`."""
    return tuple(
        dict.fromkeys(
            token for row in candidate.people_context if (token := row.split(":", 1)[0].strip())
        )
    )


def _person_coverage_finding(
    token: str,
    members: Sequence[FineCutCandidate],
    *,
    by_moment: dict[str, list[FineCutCandidate]],
    current_set: set[str],
    busiest: int,
) -> dict[str, Any]:
    """Point one unrepresented person at the reservoir moment that photographed them most."""
    appearances: dict[str, int] = {}
    for candidate in members:
        appearances[candidate.moment_id] = appearances.get(candidate.moment_id, 0) + 1
    moment_id = max(
        appearances,
        key=lambda key: (appearances[key], -by_moment[key][0].taken_at.timestamp(), key),
    )
    aliases = [candidate.alias for candidate in by_moment[moment_id]]
    return {
        "focus_kind": "person_coverage",
        "moment_ids": [moment_id],
        "asset_ids": aliases,
        "current_asset_ids": [alias for alias in aliases if alias in current_set],
        "selection_limit": _FINAL_MOMENT_ASSET_CAP,
        "owner_evidence": {
            "person": token,
            "reservoir_assets": len(members),
            "reservoir_moments": len(appearances),
            "wall_assets": 0,
            "busiest_person_wall_assets": busiest,
            "favourite_assets": sum(candidate.favourite for candidate in members),
        },
        "observation": (
            f"Person {token} appears in {len(members)} reservoir assets across "
            f"{len(appearances)} moments and in no final asset, while another person "
            f"holds {busiest} final assets."
        ),
        "review_question": (
            "Does any candidate here add a distinct necessary occasion or relationship beat, "
            "which would also reach a person the current cut never shows, rather than merely "
            "filling unused duration?"
        ),
    }


def _person_coverage(
    by_moment: dict[str, list[FineCutCandidate]],
    *,
    current_set: set[str],
) -> tuple[dict[str, Any], ...]:
    """Report recurring people the reservoir carries while no final asset shows them."""
    reservoir: dict[str, list[FineCutCandidate]] = {}
    on_wall: dict[str, int] = {}
    for moment in by_moment.values():
        for candidate in moment:
            for token in _person_tokens(candidate):
                reservoir.setdefault(token, []).append(candidate)
                on_wall[token] = on_wall.get(token, 0) + int(candidate.alias in current_set)
    busiest = max(on_wall.values(), default=0)
    if busiest < _PERSON_MIN_WALL_ASSETS:
        return ()
    absent = sorted(
        (
            (token, members)
            for token, members in reservoir.items()
            if not on_wall[token] and len(members) >= _PERSON_MIN_RESERVOIR_ASSETS
        ),
        key=lambda row: (-len(row[1]), row[0]),
    )
    return tuple(
        _person_coverage_finding(
            token,
            members,
            by_moment=by_moment,
            current_set=current_set,
            busiest=busiest,
        )
        for token, members in absent[:_PERSON_COVERAGE_MAX_FINDINGS]
    )


def _dark_frame_swap(
    moment: Sequence[FineCutCandidate],
    *,
    current_set: set[str],
) -> dict[str, Any] | None:
    """Report kept near-black frames whose own moment reservoir holds a far brighter sibling."""
    lit = [
        (candidate, candidate.luminance) for candidate in moment if candidate.luminance is not None
    ]
    brightest = max((value for _row, value in lit), default=0)
    dark = [
        (candidate, value)
        for candidate, value in lit
        if candidate.alias in current_set
        and value < _DARK_FRAME_LUMINANCE
        and brightest > value
        and brightest >= value * _DARK_FRAME_BRIGHTER_RATIO
    ]
    if not dark:
        return None
    aliases = [candidate.alias for candidate in moment]
    darkest = ", ".join(f"{candidate.alias} at {value}" for candidate, value in dark)
    return {
        "focus_kind": "dark_frame",
        "moment_ids": [moment[0].moment_id],
        "asset_ids": aliases,
        "current_asset_ids": [alias for alias in aliases if alias in current_set],
        "selection_limit": _FINAL_MOMENT_ASSET_CAP,
        "owner_evidence": {
            "dark_asset_ids": [candidate.alias for candidate, _value in dark],
            "dark_luminance": [value for _candidate, value in dark],
            "brightest_sibling_luminance": brightest,
            "luminance_floor": _DARK_FRAME_LUMINANCE,
            "favourite_assets": sum(candidate.favourite for candidate in moment),
        },
        "observation": (
            f"Final assets {darkest} read near black in mean preview luminance while this "
            f"moment's reservoir holds a frame at {brightest}."
        ),
        "review_question": (
            "Is the near-black frame the strongest visible carrier of this beat, or does a "
            "brighter sibling of the same moment show the same thing legibly?"
        ),
    }


def _document_artifact_finding(
    moment: Sequence[FineCutCandidate],
    *,
    current_set: set[str],
) -> dict[str, Any] | None:
    """Report a kept single-asset moment whose only candidate reads as a document, not a scene.

    Reject-only: a single-asset moment has no lived sibling to offer instead, so unlike
    dark_frame this never proposes an alternative -- it only asks whether the memento
    earns its own tile.
    """
    if len(moment) != 1:
        return None
    candidate = moment[0]
    if candidate.alias not in current_set:
        return None
    lowered = candidate.description.lower()
    if not any(word in lowered for word in DOCUMENT_ARTIFACT_WORDS):
        return None
    return {
        "focus_kind": "document_artifact",
        "reject_only": True,
        "moment_ids": [candidate.moment_id],
        "asset_ids": [candidate.alias],
        "current_asset_ids": [candidate.alias],
        "selection_limit": _FINAL_MOMENT_ASSET_CAP,
        "owner_evidence": {
            "favourite_assets": int(candidate.favourite),
        },
        "observation": (
            f"Final asset {candidate.alias} is the only candidate its moment ever held, and "
            "its card reads as a document or memento rather than a lived scene."
        ),
        "review_question": (
            "Does this memento earn its tile against the rest of the wall, or is it a "
            "document about the memory rather than a moment of it?"
        ),
    }


def outdoor_setting_words(text: str) -> tuple[str, ...]:
    """Name the outdoor-setting words a card or description carries, if any.

    Only a corroborator: it never decides a finding on its own, and the wiring uses it to
    avoid opening previews for dropped moments no place finding could ever reach.
    """
    lowered = text.lower()
    return tuple(word for word in OUTDOOR_SETTING_WORDS if word in lowered)


def _rejected_moment_rows(
    rejected_moments: Sequence[dict[str, Any]],
    *,
    by_alias: dict[str, FineCutCandidate],
    current_set: set[str],
) -> dict[str, dict[str, Any]]:
    """Index the run record's rejected-moment rows against the pool that carries them.

    A rejected moment is not in any reservoir, so the caller supplies what the record
    still holds for it: the fused card's summary and hedged `people` field, the rejection
    reason, and the aliases of its candidate rows. Those aliases must be in the reopened
    pool, or the finding they produce could not be grounded downstream.
    """
    rows: dict[str, dict[str, Any]] = {}
    for row in rejected_moments:
        moment_id = row.get("moment_id") if isinstance(row, dict) else None
        aliases = row.get("asset_ids") if isinstance(row, dict) else None
        if (
            not isinstance(moment_id, str)
            or moment_id in rows
            or not isinstance(row, dict)
            or not isinstance(row.get("summary"), str)
            or not isinstance(aliases, list)
            or not aliases
            or len(set(aliases)) != len(aliases)
            or any(not isinstance(alias, str) or alias not in by_alias for alias in aliases)
            or {by_alias[alias].moment_id for alias in aliases} != {moment_id}
            or not all(by_alias[alias].proposed_from_rejected for alias in aliases)
            or set(aliases) & current_set
        ):
            raise ValueError("rejected moment rows are not grounded")
        rows[moment_id] = row
    return rows


def _card_reads_people_free(row: dict[str, Any], members: Sequence[FineCutCandidate]) -> bool:
    """Whether a rejected moment's card hedges people away, or its rows never carry any."""
    people = row.get("people")
    if isinstance(people, str) and people.strip():
        return people.strip().casefold() == _CARD_PEOPLE_HEDGE
    return not any(candidate.people_context for candidate in members)


def _people_free_signal(row: dict[str, Any]) -> str:
    """Name which structural reading made the card people-free, for the review record."""
    people = row.get("people")
    if isinstance(people, str) and people.strip():
        return "hedged-card-people"
    return "no-people-context"


def _place_without_landscape_finding(
    occasion: Sequence[FineCutCandidate],
    *,
    current_set: set[str],
    rejected_rows: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Report a people-only occasion whose rejected material still holds its place."""
    kept = [candidate for candidate in occasion if candidate.alias in current_set]
    if (
        not kept
        or not all(candidate.people_context for candidate in kept)
        or any(outdoor_setting_words(candidate.description) for candidate in kept)
    ):
        return None
    members_by_moment: dict[str, list[FineCutCandidate]] = {}
    for candidate in occasion:
        members_by_moment.setdefault(candidate.moment_id, []).append(candidate)
    # Structural first: the card has to be people-free before any word is read. The words
    # then only corroborate that the people-free moment is an outdoor setting.
    people_free = [
        (moment_id, row, members_by_moment[moment_id])
        for moment_id in members_by_moment
        if (row := rejected_rows.get(moment_id)) is not None
        and _card_reads_people_free(row, members_by_moment[moment_id])
    ]
    outdoors = [
        (moment_id, row, words, members)
        for moment_id, row, members in people_free
        if (words := outdoor_setting_words(row["summary"]))
    ]
    if not outdoors:
        return None
    # One corroborating word is one word. "Resting on a plain light-colored surface" made a
    # kitchen sponge the only place row a whole year offered, while a snow-covered hillside
    # sat one moment later in the same occasion. Rank on how much of the card reads as open
    # place, then on how much of the moment is on offer; occasion order only breaks ties.
    outdoors.sort(key=lambda row: (-len(row[2]), -len(row[3]), row[3][0].taken_at, row[0]))
    moment_id, row, words, members = outdoors[0]
    # No reservoir opened for this moment, so the only merits on record are the star and
    # the media ladder the wall row already carries.
    best = min(
        members,
        key=lambda candidate: (
            not candidate.favourite,
            -_media_priority(candidate),
            candidate.taken_at,
            candidate.alias,
        ),
    )
    days = sorted({candidate.taken_at.date() for candidate in occasion})
    return {
        "focus_kind": "place_without_landscape",
        "moment_ids": [moment_id],
        "asset_ids": [candidate.alias for candidate in members],
        "current_asset_ids": [],
        "selection_limit": _FINAL_MOMENT_ASSET_CAP,
        "owner_evidence": {
            "occasion_days": [day.isoformat() for day in days],
            "people_dense_wall_assets": len(kept),
            "proposed_asset_id": best.alias,
            "people_free_signal": _people_free_signal(row),
            "corroborating_outdoor_words": list(words),
            "card_summary": row["summary"],
            "rejection_reason": row.get("reason"),
            "favourite_assets": sum(candidate.favourite for candidate in members),
        },
        "observation": (
            f"Every final asset of this occasion shows people, while the moment cut rejected "
            f"{moment_id} of the same days, whose card reads as open place and names no one."
        ),
        "review_question": (
            "Does this rejected place frame carry the setting of an occasion the cut shows "
            "only as faces, rather than merely filling unused duration?"
        ),
    }


def _place_without_landscape(
    candidates: Sequence[FineCutCandidate],
    *,
    chapter_by_moment: dict[str, str],
    current_set: set[str],
    rejected_rows: dict[str, dict[str, Any]],
) -> tuple[tuple[int, datetime, str, dict[str, Any]], ...]:
    """Rank the occasions the wall reaches only through faces, bounded per run."""
    findings = [
        (occasion, finding)
        for occasion in _occasion_day_runs(candidates, chapter_by_moment=chapter_by_moment)
        if (
            finding := _place_without_landscape_finding(
                occasion,
                current_set=current_set,
                rejected_rows=rejected_rows,
            )
        )
        is not None
    ]
    return tuple(
        (2, occasion[0].taken_at, str(finding["moment_ids"][0]), finding)
        for occasion, finding in findings[:_PLACE_WITHOUT_LANDSCAPE_MAX_FINDINGS]
    )


def _per_asset_pool_findings(
    by_moment: dict[str, list[FineCutCandidate]],
    *,
    current_set: set[str],
) -> tuple[tuple[int, datetime, str, dict[str, Any]], ...]:
    """Rank the checks the per-moment loop cannot see: dark final frames, absent people, and
    single-asset moments whose kept frame reads as a document."""
    dark = sorted(
        (
            finding
            for moment in by_moment.values()
            if (finding := _dark_frame_swap(moment, current_set=current_set)) is not None
        ),
        key=lambda finding: (
            min(finding["owner_evidence"]["dark_luminance"]),
            finding["moment_ids"][0],
        ),
    )
    documents = sorted(
        (
            finding
            for moment in by_moment.values()
            if (finding := _document_artifact_finding(moment, current_set=current_set))
            is not None
        ),
        key=lambda finding: finding["moment_ids"][0],
    )
    findings = (
        *((0, finding) for finding in dark[:_DARK_FRAME_MAX_FINDINGS]),
        *((1, finding) for finding in _person_coverage(by_moment, current_set=current_set)),
        *((2, finding) for finding in documents[:_DOCUMENT_ARTIFACT_MAX_FINDINGS]),
    )
    return tuple(
        (
            priority,
            by_moment[finding["moment_ids"][0]][0].taken_at,
            str(finding["moment_ids"][0]),
            finding,
        )
        for priority, finding in findings
    )


def runtime_final_pool_findings(
    candidates: Sequence[FineCutCandidate],
    *,
    current_aliases: Sequence[str],
    cap_removed_aliases: Sequence[str] = (),
    chapter_readings: Sequence[dict[str, Any]] = (),
    rejected_moments: Sequence[dict[str, Any]] = (),
    max_findings: int = _VISUAL_POOL_MAX_FINDINGS,
) -> tuple[dict[str, Any], ...]:
    """Choose bounded pool checks from cap projections, dark moments and trip days, dark
    final frames, people the cut never shows, and occasions it shows only as faces."""
    by_alias = {candidate.alias: candidate for candidate in candidates}
    current = tuple(dict.fromkeys(current_aliases))
    cap_removed = tuple(dict.fromkeys(cap_removed_aliases))
    if (
        len(by_alias) != len(candidates)
        or len(current) != len(tuple(current_aliases))
        or len(cap_removed) != len(tuple(cap_removed_aliases))
        or not set(current) | set(cap_removed) <= set(by_alias)
        or not isinstance(max_findings, int)
        or isinstance(max_findings, bool)
        or not 1 <= max_findings <= 64
    ):
        raise ValueError("runtime final pool findings are not grounded")
    current_set = set(current)
    cap_removed_set = set(cap_removed) - current_set
    rejected_rows = _rejected_moment_rows(
        rejected_moments,
        by_alias=by_alias,
        current_set=current_set,
    )
    # Every check below reasons about what the moment cut RETAINED; a rejected moment
    # only ever reaches the place check, which knows it never opened a reservoir.
    reservoir = [
        candidate for candidate in candidates if candidate.moment_id not in rejected_rows
    ]
    current_episodes = {
        by_alias[alias].episode_id
        for alias in current
        if by_alias[alias].episode_id is not None
    }
    by_moment: dict[str, list[FineCutCandidate]] = {}
    for candidate in reservoir:
        by_moment.setdefault(candidate.moment_id, []).append(candidate)

    ranked: list[tuple[int, datetime, str, dict[str, Any]]] = []
    for moment_id, moment in by_moment.items():
        aliases = [candidate.alias for candidate in moment]
        selected = [alias for alias in aliases if alias in current_set]
        cap_removed_here = [alias for alias in aliases if alias in cap_removed_set]
        favourite_assets = sum(candidate.favourite for candidate in moment)
        calendar_anchor = any(
            candidate.taken_at.month == 1 and candidate.taken_at.day == 1
            for candidate in moment
        )
        episodes = {candidate.episode_id for candidate in moment if candidate.episode_id is not None}
        isolated = not episodes or episodes.isdisjoint(current_episodes)
        if selected and cap_removed_here:
            priority = 0
            focus_kind = "moment_cap_projection"
            observation = (
                "The arithmetic moment cap removed candidates from this retained moment."
            )
            review_question = (
                "Are the two current survivors the strongest distinct visible beats, or should "
                "one be swapped for a capped alternative?"
            )
        elif not selected:
            if isolated:
                focus_kind = "unrepresented_isolated_moment"
                priority = 1 if favourite_assets or calendar_anchor else 2
                observation = (
                    "A moment retained by the moment cut has no final asset and shares no "
                    "production episode with the current film."
                )
            else:
                focus_kind = "unrepresented_episode_sibling"
                priority = 3 if favourite_assets or calendar_anchor else 4
                observation = (
                    "A moment retained by the moment cut has no final asset, while another "
                    "moment from its production episode is represented."
                )
            review_question = (
                "Does any candidate add a distinct necessary occasion or progression beat, "
                "rather than merely filling unused duration?"
            )
        else:
            continue
        ranked.append(
            (
                priority,
                moment[0].taken_at,
                moment_id,
                {
                    "focus_kind": focus_kind,
                    "moment_ids": [moment_id],
                    "asset_ids": aliases,
                    "current_asset_ids": selected,
                    "selection_limit": _FINAL_MOMENT_ASSET_CAP,
                    "owner_evidence": {
                        "favourite_assets": favourite_assets,
                        "calendar_anchor": calendar_anchor,
                    },
                    "observation": observation,
                    "review_question": review_question,
                },
            )
        )
    chapter_by_moment = {
        str(moment_id): str(reading["chapter_id"])
        for reading in chapter_readings
        for moment_id in reading.get("moment_ids", ())
    }
    for occasion in _occasion_day_runs(reservoir, chapter_by_moment=chapter_by_moment):
        coverage = _occasion_day_coverage(occasion, current_set=current_set)
        if coverage is not None:
            ranked.append((0, occasion[0].taken_at, occasion[0].moment_id, coverage))
    if rejected_rows:
        ranked.extend(
            _place_without_landscape(
                candidates,
                chapter_by_moment=chapter_by_moment,
                current_set=current_set,
                rejected_rows=rejected_rows,
            )
        )
    ranked.extend(_per_asset_pool_findings(by_moment, current_set=current_set))
    ranked.sort(key=lambda row: row[:3])
    return tuple(
        {"focus_id": f"R{index:03d}", **row[3]}
        for index, row in enumerate(ranked[:max_findings], start=1)
    )


def visual_final_pool_groups(
    candidates: Sequence[FineCutCandidate],
    *,
    current_aliases: Sequence[str],
    chapter_readings: Sequence[dict[str, Any]],
    review_focus: Sequence[dict[str, Any]] = (),
) -> tuple[dict[str, Any], ...]:
    """Partition every reopened candidate by story chapter, with current assets first."""
    by_alias = {candidate.alias: candidate for candidate in candidates}
    current = tuple(dict.fromkeys(current_aliases))
    if len(by_alias) != len(candidates) or len(current) != len(tuple(current_aliases)):
        raise ValueError("visual final pool groups need unique aliases")
    if not set(current) <= set(by_alias):
        raise ValueError("visual final pool groups are not grounded")

    if review_focus:
        reading_by_moment: dict[str, dict[str, Any]] = {}
        for reading in chapter_readings:
            if not isinstance(reading, dict):
                raise ValueError("visual final pool chapter reading is invalid")
            moment_ids = reading.get("moment_ids")
            if (
                not isinstance(reading.get("chapter_id"), str)
                or not isinstance(reading.get("label"), str)
                or not isinstance(moment_ids, list)
                or any(not isinstance(moment_id, str) for moment_id in moment_ids)
                or len(set(moment_ids)) != len(moment_ids)
                or any(moment_id in reading_by_moment for moment_id in moment_ids)
            ):
                raise ValueError("visual final pool chapter reading is incomplete")
            reading_by_moment.update(dict.fromkeys(moment_ids, reading))

        evidence_keys = (
            "chapter_id",
            "label",
            "thesis",
            "turning_points",
            "sustained_threads",
            "ordinary_texture",
        )
        focused_groups: list[dict[str, Any]] = []
        for focus in review_focus:
            if not isinstance(focus, dict):
                raise ValueError("visual final pool focus is invalid")
            raw_aliases = focus.get("asset_ids")
            raw_moments = focus.get("moment_ids")
            if (
                not isinstance(raw_aliases, list)
                or not raw_aliases
                or any(not isinstance(alias, str) for alias in raw_aliases)
                or len(set(raw_aliases)) != len(raw_aliases)
                or not set(raw_aliases) <= set(by_alias)
                or not isinstance(raw_moments, list)
                or not raw_moments
                or any(not isinstance(moment_id, str) for moment_id in raw_moments)
                or {by_alias[alias].moment_id for alias in raw_aliases} != set(raw_moments)
            ):
                raise ValueError("visual final pool focus is not grounded")
            focus_aliases = [candidate.alias for candidate in candidates if candidate.alias in raw_aliases]
            focus_current = [alias for alias in focus_aliases if alias in current]
            alternatives = [alias for alias in focus_aliases if alias not in current]
            # A reject-only focus (document_artifact) has no lived sibling to offer instead --
            # it challenges a kept asset on its own, so it never needs an alternative to ground.
            if not alternatives and not focus.get("reject_only"):
                continue
            readings = {
                reading_by_moment[moment_id]["chapter_id"]: reading_by_moment[moment_id]
                for moment_id in raw_moments
                if moment_id in reading_by_moment
            }
            if len(readings) > 1:
                raise ValueError("visual final pool focus crosses chapters")
            reading = next(iter(readings.values()), None)
            if reading is None:
                chapter_id = "whole-memory"
                label = "whole-memory"
                validation_current = list(current)
                evidence: dict[str, Any] = {}
            else:
                chapter_id = str(reading["chapter_id"])
                label = str(reading["label"])
                chapter_moments = set(reading["moment_ids"])
                validation_current = [
                    alias for alias in current if by_alias[alias].moment_id in chapter_moments
                ]
                evidence = {key: reading[key] for key in evidence_keys if key in reading}
            focused_groups.append(
                {
                    "group_id": f"P{len(focused_groups) + 1:03d}",
                    "chapter_id": chapter_id,
                    "label": label,
                    "current_asset_ids": focus_current,
                    "validation_current_asset_ids": validation_current,
                    "asset_ids": [*focus_current, *alternatives],
                    "story_evidence": evidence,
                    "focus_kind": str(focus.get("focus_kind", "mechanical_focus")),
                    "target_moment_ids": list(raw_moments),
                    "selection_limit": focus.get("selection_limit"),
                    "observation": str(focus.get("observation", "Mechanical attention cue.")),
                    "review_question": str(
                        focus.get(
                            "review_question",
                            "Does the bounded pool contain a distinct necessary contribution?",
                        )
                    ),
                }
            )
        return tuple(focused_groups)

    groups: list[dict[str, Any]] = []
    mapped_moments: set[str] = set()
    evidence_keys = (
        "chapter_id",
        "label",
        "thesis",
        "turning_points",
        "sustained_threads",
        "ordinary_texture",
    )
    for reading in chapter_readings:
        if not isinstance(reading, dict):
            raise ValueError("visual final pool chapter reading is invalid")
        chapter_id = reading.get("chapter_id")
        label = reading.get("label")
        moment_ids = reading.get("moment_ids")
        if (
            not isinstance(chapter_id, str)
            or not isinstance(label, str)
            or not isinstance(moment_ids, list)
            or any(not isinstance(moment_id, str) for moment_id in moment_ids)
            or len(set(moment_ids)) != len(moment_ids)
            or mapped_moments.intersection(moment_ids)
        ):
            raise ValueError("visual final pool chapter reading is incomplete")
        mapped_moments.update(moment_ids)
        pool = [candidate.alias for candidate in candidates if candidate.moment_id in moment_ids]
        current_pool = [alias for alias in pool if alias in current]
        if not current_pool or len(pool) == len(current_pool):
            continue
        groups.append(
            {
                "group_id": f"P{len(groups) + 1:03d}",
                "chapter_id": chapter_id,
                "label": label,
                "current_asset_ids": current_pool,
                "asset_ids": [*current_pool, *(alias for alias in pool if alias not in current)],
                "story_evidence": {key: reading[key] for key in evidence_keys if key in reading},
            }
        )

    unmapped_pool = [
        candidate.alias for candidate in candidates if candidate.moment_id not in mapped_moments
    ]
    unmapped_current = [alias for alias in unmapped_pool if alias in current]
    if unmapped_current and len(unmapped_pool) > len(unmapped_current):
        groups.append(
            {
                "group_id": f"P{len(groups) + 1:03d}",
                "chapter_id": "whole-memory",
                "label": "whole-memory",
                "current_asset_ids": unmapped_current,
                "asset_ids": [
                    *unmapped_current,
                    *(alias for alias in unmapped_pool if alias not in current),
                ],
                "story_evidence": {},
            }
        )
    return tuple(groups)


def visual_final_pool_request_groups(
    candidates: Sequence[FineCutCandidate],
    group: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Split one reconsideration group into vision requests of at most twelve tiles.

    A group already inside the budget is returned unchanged, so nothing about a small
    request moves. Above it, the group's current tiles anchor every slice — each request
    can still propose a swap and reads its own alternatives against what the cut holds —
    and the alternatives are chunked in wall order, which is chronological. Slices past
    the request bound are dropped, keeping the ones richest in the group's focus moments.
    """
    aliases = tuple(group.get("asset_ids", ()))
    if len(aliases) <= AUDIT_MAX_TILES_PER_REQUEST:
        return (group,)
    by_alias = {candidate.alias: candidate for candidate in candidates}
    if len(by_alias) != len(candidates) or not set(aliases) <= set(by_alias):
        raise ValueError("visual final pool request split is not grounded")
    group_current = tuple(dict.fromkeys(group.get("current_asset_ids", ())))
    anchor = group_current[: AUDIT_MAX_TILES_PER_REQUEST - 1]
    anchored = set(anchor)
    stride = AUDIT_MAX_TILES_PER_REQUEST - len(anchor)
    rest = [alias for alias in aliases if alias not in anchored]
    slices = [(*anchor, *rest[offset : offset + stride]) for offset in range(0, len(rest), stride)]
    targets = set(group.get("target_moment_ids", ()))
    kept = sorted(
        sorted(
            range(len(slices)),
            key=lambda index: (
                -sum(by_alias[alias].moment_id in targets for alias in slices[index]),
                index,
            ),
        )[:AUDIT_MAX_REQUESTS_PER_GROUP]
    )
    current_set = set(group_current)
    return tuple(
        {
            **group,
            "group_id": f"{group.get('group_id', 'P001')}s{position:02d}",
            "asset_ids": [
                *(alias for alias in slices[index] if alias in current_set),
                *(alias for alias in slices[index] if alias not in current_set),
            ],
            "current_asset_ids": [alias for alias in slices[index] if alias in current_set],
        }
        for position, index in enumerate(kept, start=1)
    )


def visual_final_pool_reconsideration_prompt(
    candidates: Sequence[FineCutCandidate],
    *,
    current_aliases: Sequence[str],
    group: dict[str, Any],
    tile_mapping: Sequence[tuple[int, str]],
    editorial_brief: str,
    thesis: dict[str, Any],
    capacity: int,
    required_aliases: Sequence[str],
) -> str:
    """Ask one chapter-level visual search to propose only grounded pool deltas."""
    by_alias = {candidate.alias: candidate for candidate in candidates}
    current = tuple(dict.fromkeys(current_aliases))
    required = tuple(dict.fromkeys(required_aliases))
    group_aliases = tuple(group.get("asset_ids", ()))
    group_current = tuple(group.get("current_asset_ids", ()))
    target_moments = tuple(
        group.get("target_moment_ids")
        or dict.fromkeys(by_alias[alias].moment_id for alias in group_aliases)
    )
    selection_limit = group.get("selection_limit", len(group_aliases))
    mapped_aliases = tuple(alias for _number, alias in tile_mapping)
    if (
        len(by_alias) != len(candidates)
        or len(current) != len(tuple(current_aliases))
        or len(required) != len(tuple(required_aliases))
        or not set(required) <= set(current) <= set(by_alias)
        or not isinstance(capacity, int)
        or isinstance(capacity, bool)
        or capacity < len(current)
        or not group_aliases
        or len(set(group_aliases)) != len(group_aliases)
        or not set(group_aliases) <= set(by_alias)
        or len(set(group_current)) != len(group_current)
        or not set(group_current) <= set(current) & set(group_aliases)
        or not target_moments
        or any(not isinstance(moment_id, str) for moment_id in target_moments)
        or not isinstance(selection_limit, int)
        or isinstance(selection_limit, bool)
        or selection_limit < 1
        or mapped_aliases != group_aliases
        or len({number for number, _alias in tile_mapping}) != len(tile_mapping)
    ):
        raise ValueError("visual final pool prompt is not grounded")
    mapping = "\n".join(f"tile {number} = {alias}" for number, alias in tile_mapping)
    pool_candidates = tuple(by_alias[alias] for alias in group_aliases)
    evidence = group.get("story_evidence") or thesis
    structural_focus = {
        "focus_kind": group.get("focus_kind"),
        "target_moment_ids": target_moments,
        "selection_limit": selection_limit,
        "observation": group.get("observation"),
        "review_question": group.get("review_question"),
    }
    shape = {
        "schema_version": FINAL_VISUAL_POOL_RECONSIDERATION_SCHEMA,
        "verdict": "stable or revise",
        "changes": [
            {
                "add_asset_ids": ["A002"],
                "remove_asset_ids": ["A001"],
                "visible_gain": "specific contribution visibly added by the proposed tile",
                "displaced_contribution": "what removed tiles repeat or why none is removed",
            }
        ],
        "overall_reason": "why the current chapter is stable or the cited delta is warranted",
    }
    return f"""You are visually reconsidering the complete candidate pool for one chapter of a
personal memory. This is neutral verification, not a request to make a change. Stable is the
default verdict. The first {len(group_current)} tiles are the CURRENT chapter cut; every later tile
is an available alternative from the already-selected production moments.

EDITORIAL BRIEF
{editorial_brief}

STORY EVIDENCE
{json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))}

STRUCTURAL REVIEW FOCUS
{json.dumps(structural_focus, ensure_ascii=False, separators=(",", ":"))}
The focus chooses what deserves inspection; it does not prove that an alternative earns runtime.
After any proposed change, retain at most {selection_limit} assets across the target moment IDs.
If the target currently has no asset, stable remains valid when none of its candidates adds a
distinct necessary contribution. If it already has capped survivors, a proposed addition must swap
within the limit rather than expanding the moment.

DURATION CAPACITY
At most {capacity} assets in the complete film, currently {len(current)}. Capacity is a ceiling,
never a target. Less is more when no remaining candidate adds a distinct contribution.

REQUIRED ALIASES
{json.dumps(required, separators=(",", ":"))}

CURRENT CHAPTER ALIASES
{json.dumps(group_current, separators=(",", ":"))}

TILE MAPPING
{mapping}

COMPLETE VISIBLE POOL
alias|moment|taken_at|media|favourite|episode|people|context|motion|description
{compact_reservoir_wall(pool_candidates)}

Compare the current target with the complete visible pool. Repeated faces are not automatically a
defect, recurring people may be the story, and a landscape is not a quota. A long trip may need
multiple visuals when they carry genuinely different setup, route, place, action, progression,
payoff, relationship, scale, or atmosphere. {_MEDIA_PRIORITY_GUIDANCE}

Return revise only when an available tile visibly supplies a necessary contribution absent from
the current cut, or clearly replaces current tiles with diminishing returns. Do not add an image
merely because it is attractive or because capacity remains. Propose at most three changes. Added
aliases must be alternatives; removed aliases must be current and must not be required. A grounded
addition may have an empty remove list. Do not infer identity, relationship, causality, or
significance.

When stable, changes must be empty. When revise, changes must be nonempty. Reasons are concise audit
evidence, not hidden chain-of-thought. Return only one complete JSON object with exactly these keys:
{json.dumps(shape, separators=(",", ":"))}
The schema_version must be exactly {FINAL_VISUAL_POOL_RECONSIDERATION_SCHEMA}."""


def read_visual_final_pool_reconsideration(
    raw: str,
    candidates: Sequence[FineCutCandidate],
    *,
    current_aliases: Sequence[str],
    group: dict[str, Any],
    required_aliases: Sequence[str],
    capacity: int,
) -> dict[str, Any]:
    """Validate and apply one chapter-level visual pool proposal."""
    by_alias = {candidate.alias: candidate for candidate in candidates}
    current = tuple(dict.fromkeys(current_aliases))
    required = tuple(dict.fromkeys(required_aliases))
    group_aliases = tuple(group.get("asset_ids", ()))
    group_current = tuple(group.get("current_asset_ids", ()))
    target_moments = tuple(
        group.get("target_moment_ids")
        or dict.fromkeys(by_alias[alias].moment_id for alias in group_aliases)
    )
    selection_limit = group.get("selection_limit", len(group_aliases))
    if (
        len(by_alias) != len(candidates)
        or len(current) != len(tuple(current_aliases))
        or len(required) != len(tuple(required_aliases))
        or not set(required) <= set(current) <= set(by_alias)
        or not set(group_current) <= set(current) & set(group_aliases) <= set(by_alias)
        or not target_moments
        or any(not isinstance(moment_id, str) for moment_id in target_moments)
        or not isinstance(selection_limit, int)
        or isinstance(selection_limit, bool)
        or selection_limit < 1
        or not isinstance(capacity, int)
        or isinstance(capacity, bool)
        or capacity < 1
    ):
        raise ValueError("visual final pool proposal is not grounded")
    payload = final_json_object(raw)
    if (
        payload is None
        or set(payload) != {"schema_version", "verdict", "changes", "overall_reason"}
        or payload.get("schema_version") != FINAL_VISUAL_POOL_RECONSIDERATION_SCHEMA
    ):
        raise ValueError("visual final pool proposal has the wrong envelope")
    verdict = payload.get("verdict")
    raw_changes = payload.get("changes")
    if (
        verdict not in {"stable", "revise"}
        or not isinstance(raw_changes, list)
        or len(raw_changes) > 3
        or (verdict == "stable") != (not raw_changes)
    ):
        raise ValueError("visual final pool proposal verdict is invalid")
    current_set = set(current)
    required_set = set(required)
    alternatives = set(group_aliases) - current_set
    removable = set(group_current) - required_set
    added: set[str] = set()
    removed: set[str] = set()
    changes: list[dict[str, Any]] = []
    discarded_changes = 0
    for row in raw_changes:
        if not isinstance(row, dict) or set(row) != {
            "add_asset_ids",
            "remove_asset_ids",
            "visible_gain",
            "displaced_contribution",
        }:
            discarded_changes += 1
            continue
        add_asset_ids = row.get("add_asset_ids")
        remove_asset_ids = row.get("remove_asset_ids")
        visible_gain = bounded_model_text(row.get("visible_gain"), max_chars=_MAX_REASON_CHARS)
        displaced = bounded_model_text(
            row.get("displaced_contribution"), max_chars=_MAX_REASON_CHARS
        )
        if (
            not isinstance(add_asset_ids, list)
            or not isinstance(remove_asset_ids, list)
            or not add_asset_ids
            and not remove_asset_ids
            or any(not isinstance(alias, str) for alias in (*add_asset_ids, *remove_asset_ids))
            or len(set(add_asset_ids)) != len(add_asset_ids)
            or len(set(remove_asset_ids)) != len(remove_asset_ids)
            or not set(add_asset_ids) <= alternatives
            or not set(remove_asset_ids) <= removable
            or set(add_asset_ids) & set(remove_asset_ids)
            or added & set(add_asset_ids)
            or removed & set(remove_asset_ids)
            or visible_gain is None
            or displaced is None
        ):
            discarded_changes += 1
            continue
        added.update(add_asset_ids)
        removed.update(remove_asset_ids)
        changes.append(
            {
                "add_asset_ids": add_asset_ids,
                "remove_asset_ids": remove_asset_ids,
                "reason": visible_gain,
                "visible_gain": visible_gain,
                "displaced_contribution": displaced,
            }
        )
    if added & removed:
        raise ValueError("visual final pool aliases conflict across changes")
    keep_set = current_set - removed | added
    if not keep_set or len(keep_set) > capacity:
        raise ValueError("visual final pool proposal violates duration capacity")
    if (
        sum(
            candidate.alias in keep_set and candidate.moment_id in target_moments
            for candidate in candidates
        )
        > selection_limit
    ):
        raise ValueError("visual final pool proposal violates the moment cap")
    _require_favourite_representation(by_alias, keep_set)
    overall = bounded_model_text(payload.get("overall_reason"), max_chars=_MAX_REASON_CHARS)
    if overall is None:
        raise ValueError("visual final pool proposal overall reason is unsafe")
    ordered = [candidate.alias for candidate in candidates]
    return {
        "verdict": "revise" if changes else "stable",
        "keep": [alias for alias in ordered if alias in keep_set],
        "added": [alias for alias in ordered if alias in added],
        "removed": [alias for alias in ordered if alias in removed],
        "changes": changes,
        "discarded_changes": discarded_changes,
        "overall_reason": overall,
    }


def visual_final_pool_global_validation_prompt(
    candidates: Sequence[FineCutCandidate],
    *,
    current_aliases: Sequence[str],
    proposals: Sequence[dict[str, Any]],
    tile_mapping: Sequence[tuple[int, str]],
    editorial_brief: str,
    thesis: dict[str, Any],
    complete_current_cut: bool = True,
) -> str:
    """Ask one global visual gate to accept only chapter deltas that improve the whole film."""
    by_alias = {candidate.alias: candidate for candidate in candidates}
    current = tuple(dict.fromkeys(current_aliases))
    proposal_rows = tuple(proposals)
    visible_aliases = tuple(
        dict.fromkeys(
            (*current, *(alias for row in proposal_rows for alias in row["add_asset_ids"]))
        )
    )
    if (
        len(by_alias) != len(candidates)
        or len(current) != len(tuple(current_aliases))
        or not set(current) <= set(by_alias)
        or not proposal_rows
        or tuple(alias for _number, alias in tile_mapping) != visible_aliases
    ):
        raise ValueError("visual final pool global validation is not grounded")
    current_set = set(current)
    proposal_ids_by_added_alias: dict[str, list[str]] = {}
    for row in proposal_rows:
        for alias in row["add_asset_ids"]:
            proposal_ids_by_added_alias.setdefault(alias, []).append(row["change_id"])
    mapping = "\n".join(
        (
            f"tile {number} = {alias} | CURRENT"
            if alias in current_set
            else (
                f"tile {number} = {alias} | PROPOSED ADDITION "
                f"{','.join(proposal_ids_by_added_alias[alias])}"
            )
        )
        for number, alias in tile_mapping
    )
    detailed_aliases = {
        alias
        for row in proposal_rows
        for key in ("add_asset_ids", "remove_asset_ids")
        for alias in row[key]
    }
    wall = compact_visual_global_wall(
        tuple(by_alias[alias] for alias in visible_aliases),
        detailed_aliases=detailed_aliases,
    )
    scope = (
        "the complete assembled cut"
        if complete_current_cut
        else "all selected assets from every affected chapter"
    )
    scope_instruction = (
        "Judge the whole film, not each chapter in isolation."
        if complete_current_cut
        else (
            "Judge the affected chapters together. Do not infer coverage from chapters that are "
            "not attached; a subsequent whole-film audit checks corpus-wide balance."
        )
    )
    shape = {
        "schema_version": FINAL_VISUAL_POOL_GLOBAL_VALIDATION_SCHEMA,
        "decisions": [
            {
                "change_id": "C001",
                "verdict": "accept or reject",
                "reason": "specific net effect on the complete film",
            }
        ],
        "overall_reason": "why the accepted subset improves the whole film without overfilling",
    }
    return f"""You are validating proposed chapter-level edits against {scope} of a personal
memory. This is a confirmation pass, not a request to maximize changes. Reject a
proposal unless the attached pixels and metadata show a clear net gain. Decide every proposal
independently.

EDITORIAL BRIEF
{editorial_brief}

THESIS
{json.dumps(thesis, ensure_ascii=False, separators=(",", ":"))}

PROPOSED CHANGES
{json.dumps(proposal_rows, ensure_ascii=False, separators=(",", ":"))}

TILE MAPPING
Every tile is explicitly labelled as CURRENT or as a PROPOSED ADDITION.
Contact-sheet pages never mix those states: all CURRENT pages come first, then pages containing
only PROPOSED ADDITIONS.
A proposed-addition tile is the candidate being judged, not evidence that the candidate is already
in the current cut. Compare it against the CURRENT tiles when deciding whether it adds a missing
contribution or repeats one already present.
{mapping}

VISIBLE METADATA
Every visible tile retains grounding metadata. Full descriptions are limited to assets directly
involved in a proposed addition or removal; inspect the attached pixels for the complete cut.
alias|moment|taken_at|media|favourite|episode|people|motion|description_when_proposed
{wall}

Coverage is contribution-specific. People or relationship coverage cannot substitute for missing
event, place, route, atmosphere, or action coverage, and the reverse is also true. A chapter is not
visually complete merely because it contains many or important people frames. Call an addition
redundant only when another CURRENT tile visibly carries the same proposed contribution.

{scope_instruction} Recurring and inner-circle people, genuine
special events, consequential places, candid action, route, progression, and atmosphere may earn
runtime. Unrecognized people alone are weak unless their action is necessary to understand an
event. Repeated cats, selfies, posed groups, or same-event views have diminishing returns. Do not
replace candid action with a posed selfie unless it adds an important relationship the film lacks.
A landscape is not a quota. Do not reject an establishing frame merely because it lacks a
recognized person or close action. Accept it only when the current cut otherwise fails to make a
special event, trip phase, consequential place, route, scale, arrival, or atmosphere visually
legible. A portrait taken at an event does not by itself establish the event.
{_MEDIA_PRIORITY_GUIDANCE}

An add-only change is valid when it supplies a genuinely missing beat and the film remains concise.
A replacement is valid only when the added visual is stronger and the removed visual's unique
contribution survives elsewhere. Less is more: available capacity is not a reason to accept.
Return exactly one decision for every supplied change_id, in the same order. Reasons are concise
audit evidence, not hidden chain-of-thought. Return only one complete JSON object with exactly these
keys:
{json.dumps(shape, separators=(",", ":"))}
The schema_version must be exactly {FINAL_VISUAL_POOL_GLOBAL_VALIDATION_SCHEMA}."""


def read_visual_final_pool_global_validation(
    raw: str,
    candidates: Sequence[FineCutCandidate],
    *,
    current_aliases: Sequence[str],
    proposals: Sequence[dict[str, Any]],
    required_aliases: Sequence[str],
    capacity: int,
) -> dict[str, Any]:
    """Apply only globally accepted visual pool deltas under the final-cut invariants."""
    by_alias = {candidate.alias: candidate for candidate in candidates}
    current = tuple(dict.fromkeys(current_aliases))
    required = tuple(dict.fromkeys(required_aliases))
    proposal_rows = tuple(proposals)
    if (
        len(by_alias) != len(candidates)
        or len(current) != len(tuple(current_aliases))
        or len(required) != len(tuple(required_aliases))
        or not set(required) <= set(current) <= set(by_alias)
        or not isinstance(capacity, int)
        or isinstance(capacity, bool)
        or capacity < 1
        or not proposal_rows
    ):
        raise ValueError("visual final pool global validation is not grounded")
    proposal_by_id: dict[str, dict[str, Any]] = {}
    for proposal in proposal_rows:
        if not isinstance(proposal, dict):
            raise ValueError("visual final pool global proposal is invalid")
        change_id = proposal.get("change_id")
        added = proposal.get("add_asset_ids")
        removed = proposal.get("remove_asset_ids")
        if (
            not isinstance(change_id, str)
            or change_id in proposal_by_id
            or not isinstance(added, list)
            or not isinstance(removed, list)
            or any(not isinstance(alias, str) for alias in (*added, *removed))
            or not set(added) <= set(by_alias) - set(current)
            or not set(removed) <= set(current) - set(required)
        ):
            raise ValueError("visual final pool global proposal is not grounded")
        proposal_by_id[change_id] = proposal
    payload = final_json_object(raw)
    repaired_envelope = False
    if payload is None:
        repaired_raw, repairs = _STRAY_STRUCTURAL_QUOTE.subn(r"\1}\2", raw)
        if repairs:
            payload = final_json_object(repaired_raw)
            repaired_envelope = payload is not None
    if (
        payload is None
        or set(payload) != {"schema_version", "decisions", "overall_reason"}
        or payload.get("schema_version") != FINAL_VISUAL_POOL_GLOBAL_VALIDATION_SCHEMA
    ):
        raise ValueError("visual final pool global validation has the wrong envelope")
    raw_decisions = payload.get("decisions")
    expected_ids = tuple(proposal_by_id)
    if not isinstance(raw_decisions, list) or len(raw_decisions) != len(expected_ids):
        raise ValueError("visual final pool global decisions are incomplete")
    decisions: list[dict[str, Any]] = []
    accepted_ids: list[str] = []
    for expected_id, row in zip(expected_ids, raw_decisions, strict=True):
        if not isinstance(row, dict) or set(row) != {"change_id", "verdict", "reason"}:
            raise ValueError("visual final pool global decision has the wrong shape")
        reason = bounded_model_text(row.get("reason"), max_chars=_MAX_REASON_CHARS)
        if (
            row.get("change_id") != expected_id
            or row.get("verdict") not in {"accept", "reject"}
            or reason is None
        ):
            raise ValueError("visual final pool global decision is invalid")
        decision = {"change_id": expected_id, "verdict": row["verdict"], "reason": reason}
        decisions.append(decision)
        if row["verdict"] == "accept":
            accepted_ids.append(expected_id)
    added: set[str] = set()
    removed: set[str] = set()
    for change_id in accepted_ids:
        proposal = proposal_by_id[change_id]
        proposal_added = set(proposal["add_asset_ids"])
        proposal_removed = set(proposal["remove_asset_ids"])
        if added & proposal_added or removed & proposal_removed:
            raise ValueError("accepted visual pool changes conflict")
        added.update(proposal_added)
        removed.update(proposal_removed)
    if added & removed:
        raise ValueError("accepted visual pool aliases conflict")
    keep_set = set(current) - removed | added
    if not keep_set or len(keep_set) > capacity:
        raise ValueError("accepted visual pool changes violate duration capacity")
    _require_favourite_representation(by_alias, keep_set)
    overall = bounded_model_text(payload.get("overall_reason"), max_chars=_MAX_REASON_CHARS)
    if overall is None:
        raise ValueError("visual final pool validation overall reason is unsafe")
    ordered = [candidate.alias for candidate in candidates]
    return {
        "accepted_change_ids": accepted_ids,
        "keep": [alias for alias in ordered if alias in keep_set],
        "added": [alias for alias in ordered if alias in added],
        "removed": [alias for alias in ordered if alias in removed],
        "decisions": decisions,
        "repaired_envelope": repaired_envelope,
        "overall_reason": overall,
    }


def compact_visual_global_wall(
    candidates: Sequence[FineCutCandidate],
    *,
    detailed_aliases: set[str],
) -> str:
    """Keep the whole visual wall grounded without repeating upstream prose."""
    by_alias = {candidate.alias: candidate for candidate in candidates}
    if len(by_alias) != len(candidates) or not detailed_aliases <= set(by_alias):
        raise ValueError("compact visual global wall aliases are not grounded")

    def field(value: str) -> str:
        return " ".join(value.split()).replace("|", "/")

    lines = []
    for candidate in candidates:
        people = ";".join(field(value) for value in candidate.people_context) or "-"
        line = (
            f"{candidate.alias}{candidate.structural_field()}|{candidate.moment_id}|"
            f"{candidate.taken_at_field()}|"
            f"{candidate.media_kind}|fav={int(candidate.favourite)}|"
            f"episode={candidate.episode_id or '-'}|people={people}|"
            f"motion={field(candidate.motion_contribution or '-')}:"
            f"{field(candidate.motion_reason or '-')}"
        )
        if candidate.alias in detailed_aliases:
            line += f"|description={field(candidate.description)}"
        lines.append(line)
    return "\n".join(lines)


def compact_reservoir_wall(candidates: Sequence[FineCutCandidate]) -> str:
    """Render a stable alias-only wall for corpus reconsideration."""
    if len({candidate.alias for candidate in candidates}) != len(candidates):
        raise ValueError("compact reservoir wall needs unique aliases")

    def field(value: str) -> str:
        return " ".join(value.split()).replace("|", "/")

    lines = []
    for candidate in candidates:
        people = ";".join(field(value) for value in candidate.people_context) or "-"
        context = ";".join(field(value) for value in candidate.context) or "-"
        lines.append(
            f"{candidate.alias}{candidate.structural_field()}|{candidate.moment_id}|"
            f"{candidate.taken_at_field()}|"
            f"{candidate.media_kind}|fav={int(candidate.favourite)}|"
            f"episode={candidate.episode_id or '-'}|people={people}|context={context}|"
            f"motion={field(candidate.motion_contribution or '-')}:"
            f"{field(candidate.motion_reason or '-')}|"
            f"{field(candidate.description)}"
        )
    return "\n".join(lines)


def final_asset_reconsideration_prompt(
    candidates: Sequence[FineCutCandidate],
    *,
    current_aliases: Sequence[str],
    required_aliases: Sequence[str],
    capacity: int,
    audit: dict[str, Any],
    editorial_brief: str,
    thesis: dict[str, Any],
) -> str:
    """Open every selected-moment candidate wall against grounded corpus findings."""
    by_alias = {candidate.alias: candidate for candidate in candidates}
    current = tuple(dict.fromkeys(current_aliases))
    required = tuple(dict.fromkeys(required_aliases))
    if (
        len(by_alias) != len(candidates)
        or len(current) != len(tuple(current_aliases))
        or len(required) != len(tuple(required_aliases))
        or not set(required) <= set(current) <= set(by_alias)
    ):
        raise ValueError("final asset reconsideration aliases are not grounded")
    findings = audit.get("findings") if isinstance(audit, dict) else None
    if audit.get("verdict") != "revise" or not isinstance(findings, list) or not findings:
        raise ValueError("final asset reconsideration needs a revision audit")
    shape = {
        "schema_version": FINAL_ASSET_RECONSIDERATION_SCHEMA,
        "changes": [
            {
                "finding_id": "F001",
                "add_asset_ids": ["A010"],
                "remove_asset_ids": ["A002"],
                "reason": "why this exact delta visibly resolves the cited finding",
            }
        ],
        "overall_reason": "why the grounded delta improves the corpus, or why none exists",
    }
    return f"""You are reconsidering a personal-memory draft against all post-Selects candidates from every selected moment.
The candidate wall is complete for this bounded stage. Do not infer that
it contains a fix merely because an audit finding exists.

EDITORIAL BRIEF
{editorial_brief}

THESIS
{json.dumps(thesis, ensure_ascii=False, separators=(",", ":"))}

DURATION CAPACITY
At most {capacity} retained assets. Capacity is a ceiling, never a target. A shorter film is correct
when no remaining candidate adds a distinct contribution.

REQUIRED ALIASES
{json.dumps(required, separators=(",", ":"))}
They must remain selected.

ALL ELIGIBLE RESERVOIR CANDIDATES
alias|moment|taken_at|media|favourite|episode|people|context|motion|description
{compact_reservoir_wall(candidates)}

CURRENT DRAFT ALIASES
{json.dumps(current, separators=(",", ":"))}

GROUNDED AUDIT
{json.dumps(audit, ensure_ascii=False, separators=(",", ":"))}

Return only deltas tied to the supplied finding IDs. Each added alias must be outside the current
draft and each removed alias must be inside it. Never remove a required alias. You may swap, remove
without replacement, or add within capacity. Preserve the exact current set when the wall contains
no visibly stronger answer to the finding. Return an empty changes list when no grounded candidate is better.

An addition must supply the cited missing contribution or clearly strengthen the same contribution
over what it removes. Attractive, clear, face-forward, scenic, or different is not enough. A selfie
may carry relationship or action; an environmental frame may carry place, route, weather, scale,
arrival, or atmosphere. Neither class has an automatic quota. Do not invent identity, relationship,
causality, or significance. Use only the aliases and evidence above.

{_MEDIA_PRIORITY_GUIDANCE}

Reasons are concise audit evidence, not hidden chain-of-thought. Return one complete JSON object with
exactly these keys:
{json.dumps(shape, separators=(",", ":"))}
The schema_version value must be exactly {FINAL_ASSET_RECONSIDERATION_SCHEMA}."""


def read_final_asset_reconsideration(
    raw: str,
    candidates: Sequence[FineCutCandidate],
    *,
    current_aliases: Sequence[str],
    required_aliases: Sequence[str],
    capacity: int,
    audit: dict[str, Any],
) -> dict[str, Any]:
    """Apply a grounded delta proposed from all selected-moment candidate reservoirs."""
    by_alias = {candidate.alias: candidate for candidate in candidates}
    current = tuple(dict.fromkeys(current_aliases))
    required = tuple(dict.fromkeys(required_aliases))
    if (
        len(by_alias) != len(candidates)
        or len(current) != len(tuple(current_aliases))
        or len(required) != len(tuple(required_aliases))
    ):
        raise ValueError("final asset reconsideration needs unique aliases")
    if not set(required) <= set(current) <= set(by_alias):
        raise ValueError("final asset reconsideration aliases are not grounded")
    if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
        raise ValueError("final asset reconsideration capacity is invalid")
    findings = audit.get("findings") if isinstance(audit, dict) else None
    if audit.get("verdict") != "revise" or not isinstance(findings, list) or not findings:
        raise ValueError("final asset reconsideration needs a revision audit")
    finding_ids = tuple(row.get("finding_id") for row in findings if isinstance(row, dict))
    if (
        len(finding_ids) != len(findings)
        or any(not isinstance(finding_id, str) for finding_id in finding_ids)
        or len(set(finding_ids)) != len(finding_ids)
    ):
        raise ValueError("final asset reconsideration audit findings are invalid")

    payload = final_json_object(raw)
    if (
        payload is None
        or set(payload) != {"schema_version", "changes", "overall_reason"}
        or payload.get("schema_version") != FINAL_ASSET_RECONSIDERATION_SCHEMA
    ):
        raise ValueError("final asset reconsideration has the wrong envelope")
    raw_changes = payload.get("changes")
    if not isinstance(raw_changes, list) or len(raw_changes) > 8:
        raise ValueError("final asset reconsideration changes are invalid")
    current_set = set(current)
    required_set = set(required)
    added: set[str] = set()
    removed: set[str] = set()
    changes: list[dict[str, Any]] = []
    for row in raw_changes:
        if not isinstance(row, dict) or set(row) != {
            "finding_id",
            "add_asset_ids",
            "remove_asset_ids",
            "reason",
        }:
            raise ValueError("final asset reconsideration change has the wrong shape")
        finding_id = row.get("finding_id")
        add_asset_ids = row.get("add_asset_ids")
        remove_asset_ids = row.get("remove_asset_ids")
        reason = bounded_model_text(row.get("reason"), max_chars=_MAX_REASON_CHARS)
        if (
            finding_id not in finding_ids
            or not isinstance(add_asset_ids, list)
            or not isinstance(remove_asset_ids, list)
            or not add_asset_ids
            and not remove_asset_ids
            or any(not isinstance(alias, str) for alias in add_asset_ids)
            or any(not isinstance(alias, str) for alias in remove_asset_ids)
            or len(set(add_asset_ids)) != len(add_asset_ids)
            or len(set(remove_asset_ids)) != len(remove_asset_ids)
            or not set(add_asset_ids) <= set(by_alias) - current_set
            or not set(remove_asset_ids) <= current_set - required_set
            or set(add_asset_ids) & set(remove_asset_ids)
            or added & set(add_asset_ids)
            or removed & set(remove_asset_ids)
            or reason is None
        ):
            raise ValueError("final asset reconsideration change is not grounded")
        added.update(add_asset_ids)
        removed.update(remove_asset_ids)
        changes.append(
            {
                "finding_id": finding_id,
                "add_asset_ids": add_asset_ids,
                "remove_asset_ids": remove_asset_ids,
                "reason": reason,
            }
        )
    if added & removed:
        raise ValueError("final asset reconsideration aliases conflict across changes")
    keep_set = current_set - removed | added
    if not keep_set:
        raise ValueError("final asset reconsideration cannot empty a nonempty film")
    if len(keep_set) > capacity:
        raise ValueError("final asset reconsideration exceeds duration capacity")
    _require_favourite_representation(by_alias, keep_set)
    overall = bounded_model_text(payload.get("overall_reason"), max_chars=_MAX_REASON_CHARS)
    if overall is None:
        raise ValueError("final asset reconsideration overall reason is unsafe")
    ordered = [candidate.alias for candidate in candidates]
    return {
        "keep": [alias for alias in ordered if alias in keep_set],
        "added": [alias for alias in ordered if alias in added],
        "removed": [alias for alias in ordered if alias in removed],
        "changes": changes,
        "overall_reason": overall,
    }


def final_asset_delta_validation_prompt(
    candidates: Sequence[FineCutCandidate],
    *,
    before_aliases: Sequence[str],
    proposal: dict[str, Any],
    audit: dict[str, Any],
) -> str:
    """Ask an independent gate whether a proposed corpus delta earns admission."""
    by_alias = {candidate.alias: candidate for candidate in candidates}
    before = tuple(dict.fromkeys(before_aliases))
    after_rows = proposal.get("keep") if isinstance(proposal, dict) else None
    if (
        len(by_alias) != len(candidates)
        or len(before) != len(tuple(before_aliases))
        or not isinstance(after_rows, list)
        or any(not isinstance(alias, str) for alias in after_rows)
        or len(set(after_rows)) != len(after_rows)
        or not set(before) | set(after_rows) <= set(by_alias)
    ):
        raise ValueError("final asset delta validation aliases are not grounded")
    before_wall = "\n".join(
        candidate.wall_line() for candidate in candidates if candidate.alias in before
    )
    after_set = set(after_rows)
    after_wall = "\n".join(
        candidate.wall_line() for candidate in candidates if candidate.alias in after_set
    )
    touched = list(
        dict.fromkeys(
            row.get("finding_id")
            for row in proposal.get("changes", [])
            if isinstance(row, dict) and isinstance(row.get("finding_id"), str)
        )
    )
    shape = {
        "schema_version": FINAL_ASSET_DELTA_VALIDATION_SCHEMA,
        "verdict": "accept or reject",
        "supported_finding_ids": ["F001"],
        "reason": "why every delta is grounded, or why the prior corpus must remain",
    }
    return f"""You are the independent acceptance gate for one proposed edit to a personal-memory
corpus. The previous corpus remains authoritative unless the complete before/after comparison proves
the edit.

GROUNDED AUDIT
{json.dumps(audit, ensure_ascii=False, separators=(",", ":"))}

PROPOSED DELTA
{json.dumps(proposal.get("changes", []), ensure_ascii=False, separators=(",", ":"))}

FINDINGS TO VALIDATE
{json.dumps(touched, separators=(",", ":"))}

BEFORE SEQUENCE
{before_wall}

AFTER SEQUENCE
{after_wall}

Accept only if every changed alias directly resolves its cited grounded finding, the after sequence
visibly improves that contribution in corpus context, and no removal erases a unique relationship,
event, action, place, change, or necessary quiet record. More attractive, scenic, clear, or simply
different is insufficient. An added face or landscape has no automatic value. Reject the entire
delta when any change is speculative, merely aesthetic, redundant, unsupported by its finding, or
damages another unique beat.

{_MEDIA_PRIORITY_GUIDANCE}

For accept, supported_finding_ids must contain every ID in FINDINGS TO VALIDATE exactly once. For
reject, it may contain only the subset that was actually supported. The reason is concise audit
evidence, not hidden chain-of-thought. Return one complete JSON object with exactly these keys:
{json.dumps(shape, separators=(",", ":"))}
The schema_version value must be exactly {FINAL_ASSET_DELTA_VALIDATION_SCHEMA}."""


def read_final_asset_delta_validation(
    raw: str,
    *,
    audit: dict[str, Any],
    proposal: dict[str, Any],
) -> dict[str, Any]:
    """Require independent support for every audit finding touched by a proposed delta."""
    findings = audit.get("findings") if isinstance(audit, dict) else None
    changes = proposal.get("changes") if isinstance(proposal, dict) else None
    if not isinstance(findings, list) or not isinstance(changes, list) or not changes:
        raise ValueError("final asset delta validation needs findings and changes")
    audit_ids = {row.get("finding_id") for row in findings if isinstance(row, dict)}
    touched_ids = {row.get("finding_id") for row in changes if isinstance(row, dict)}
    if (
        len(audit_ids) != len(findings)
        or len(touched_ids) > len(changes)
        or any(not isinstance(finding_id, str) for finding_id in audit_ids | touched_ids)
        or not touched_ids
        or not touched_ids <= audit_ids
    ):
        raise ValueError("final asset delta validation findings are invalid")
    payload = final_json_object(raw)
    if (
        payload is None
        or set(payload)
        != {
            "schema_version",
            "verdict",
            "supported_finding_ids",
            "reason",
        }
        or payload.get("schema_version") != FINAL_ASSET_DELTA_VALIDATION_SCHEMA
    ):
        raise ValueError("final asset delta validation has the wrong envelope")
    verdict = payload.get("verdict")
    supported = payload.get("supported_finding_ids")
    reason = bounded_model_text(payload.get("reason"), max_chars=_MAX_REASON_CHARS)
    if (
        verdict not in {"accept", "reject"}
        or not isinstance(supported, list)
        or any(not isinstance(finding_id, str) for finding_id in supported)
        or len(set(supported)) != len(supported)
        or not set(supported) <= touched_ids
        or verdict == "accept"
        and set(supported) != touched_ids
        or reason is None
    ):
        raise ValueError("final asset delta validation is not grounded")
    return {"verdict": verdict, "supported_finding_ids": supported, "reason": reason}


def final_asset_sequence_review_prompt(
    candidates: Sequence[FineCutCandidate],
    *,
    proposed_aliases: Sequence[str],
    required_aliases: Sequence[str],
    editorial_brief: str,
    thesis: dict[str, Any],
) -> str:
    """Ask one global reduce-only question after chapter cuts are assembled."""
    by_alias = {candidate.alias: candidate for candidate in candidates}
    proposed = tuple(dict.fromkeys(proposed_aliases))
    required = tuple(dict.fromkeys(required_aliases))
    if len(by_alias) != len(candidates) or len(proposed) != len(tuple(proposed_aliases)):
        raise ValueError("final sequence review needs unique aliases")
    if not set(required) <= set(proposed) <= set(by_alias):
        raise ValueError("final sequence review aliases are not grounded")
    wall = "\n".join(
        candidate.wall_line() for candidate in candidates if candidate.alias in proposed
    )
    shape = {
        "schema_version": FINAL_SEQUENCE_REVIEW_CUT_ONLY_SCHEMA,
        "cut": [{"asset_id": "A002", "reason": "why this visual repeats or weakens the set"}],
        "overall_reason": "how the shorter whole sequence preserves the memory",
    }
    return f"""You are performing a reduce-only global review of a proposed personal memory.

The chapter cuts have already selected every asset below. See them now as one chronological film,
not as separate chapter quotas. You may only remove assets; never add or replace one. A shorter film
is correct when repetition would otherwise dilute it. Return only the assets worth removing in cut.
Anything you do not name in cut remains selected and keeps its chronological position.

EDITORIAL BRIEF
{editorial_brief}

THESIS
{json.dumps(thesis, ensure_ascii=False, separators=(",", ":"))}

REQUIRED ASSETS
{json.dumps(required, separators=(",", ":"))}
These are grounded runtime obligations. Never put a required asset in cut.

Judge contribution across the whole draft. Each additional visual should add a new event,
relationship or action, state or change, or necessary sense of place. Repeated subject, place,
activity, pose, or unchanged state has diminishing returns across different chapters and dates.
Ordinary texture is bounded contrast for the main story, not a fresh allowance in every chapter.
Within one continuous occasion or event family, keep the smallest distinct set that lets the viewer
understand it. A new date, outfit, route, or episode label is not by itself a new beat. Keep multiple
visuals only when they establish a distinct setup, action, payoff, relationship turn, or visible
state change; otherwise retain the strongest carrier and cut the echoes.
Start with one strongest carrier per event family. A second should add a genuinely different
contribution; a third should
complete a visible arc such as setup, action, and payoff. Four or more from one family is exceptional
and must show different phases that would be unclear without every retained visual. Before adding an
echo to an already represented family, prefer a worthwhile event or relationship not yet represented.
The proposed count and duration capacity are ceilings, never targets; do not retain weak material to
fill them.

{_MEDIA_PRIORITY_GUIDANCE}

{_UNGROUNDED_INTERACTION_GUIDANCE}

A recurring person may be the story; do not call a face repetitive merely for recurring. The same
pose, action, or occasion can still repeat. An unnamed or one-off face does not become important
merely because it is a portrait. A project that visibly changes can earn progression; an unchanged
thing cannot. Different timestamps do not prove that two visually identical descriptions are
different images. A visible face or selfie is not automatically a relationship contribution, and a
repeated selfie pose can be weaker than a strong view of the place. For travel, outdoor activity, or
another place-led occasion, retain an environmental carrier when location, scale, weather,
atmosphere, route progression, or arrival is part of the experience and the people frames do not
show it. An unpeopled view may carry that beat. Prefer the strongest carrier for each materially
different setting or transition; repeated postcard views still diminish.

Do not turn this into an aesthetic-quality filter. A quiet consequential record may establish a
turning point that no attractive lived scene can show. Preserve it when the thesis and description
ground that change. Favourites are owner evidence, not permission to keep every similar frame.
Never invent identity, relationship, causality, or significance.

PROPOSED WHOLE SEQUENCE
{wall}

Cut reasons are concise audit evidence, not hidden chain-of-thought. Return only one complete JSON
object with exactly these keys:
{json.dumps(shape, separators=(",", ":"))}
The schema_version value must be exactly {FINAL_SEQUENCE_REVIEW_CUT_ONLY_SCHEMA}."""


def apply_final_asset_sequence_review(
    raw: str,
    candidates: Sequence[FineCutCandidate],
    *,
    proposed_aliases: Sequence[str],
    required_aliases: Sequence[str] = (),
) -> dict[str, Any]:
    """Apply a reduce-only whole-sequence verdict, failing open but not approved."""
    by_alias = {candidate.alias: candidate for candidate in candidates}
    if len(by_alias) != len(candidates):
        raise ValueError("final sequence aliases must be unique")
    proposed = tuple(dict.fromkeys(proposed_aliases))
    if len(proposed) != len(tuple(proposed_aliases)) or not set(proposed) <= set(by_alias):
        raise ValueError("final sequence proposal must contain unique grounded aliases")
    required = tuple(dict.fromkeys(required_aliases))
    if not set(required) <= set(proposed):
        raise ValueError("required final sequence assets must be proposed")
    ordered_proposal = [candidate.alias for candidate in candidates if candidate.alias in proposed]
    try:
        payload = final_json_object(raw)
        if payload is None:
            raise ValueError("final sequence review has the wrong envelope")
        schema_version = payload.get("schema_version")
        raw_cut = payload.get("cut")
        if schema_version == FINAL_SEQUENCE_REVIEW_CUT_ONLY_SCHEMA:
            if set(payload) != {"schema_version", "cut", "overall_reason"}:
                raise ValueError("final sequence review has the wrong envelope")
            keep_set = set(proposed)
            overlapping_cuts: set[str] = set()
        elif schema_version == FINAL_SEQUENCE_REVIEW_SCHEMA:
            if set(payload) != {"schema_version", "keep", "cut", "overall_reason"}:
                raise ValueError("final sequence review has the wrong envelope")
            raw_keep = payload.get("keep")
            if not isinstance(raw_keep, list):
                raise ValueError("final sequence review must partition the proposal")
            keep = tuple(raw_keep)
            if any(not isinstance(alias, str) for alias in keep) or len(set(keep)) != len(keep):
                raise ValueError("final sequence keep list is not unique grounded text")
            keep_set = set(keep)
        else:
            raise ValueError("final sequence review has the wrong schema version")
        if not isinstance(raw_cut, list):
            raise ValueError("final sequence cut rows must be a list")
        cut_reasons: dict[str, str] = {}
        for row in raw_cut:
            if not isinstance(row, dict) or set(row) != {"asset_id", "reason"}:
                raise ValueError("final sequence cut row has the wrong shape")
            alias = row.get("asset_id")
            reason = bounded_model_text(row.get("reason"), max_chars=_MAX_REASON_CHARS)
            if not isinstance(alias, str) or alias in cut_reasons or reason is None:
                raise ValueError("final sequence cut row is not grounded")
            cut_reasons[alias] = reason
        cut_set = set(cut_reasons)
        if schema_version == FINAL_SEQUENCE_REVIEW_CUT_ONLY_SCHEMA:
            if not cut_set <= set(proposed):
                raise ValueError("final sequence cut row is not grounded in the proposal")
            keep_set -= cut_set
        else:
            overlapping_cuts = keep_set & cut_set
            for alias in overlapping_cuts:
                del cut_reasons[alias]
            cut_set = set(cut_reasons)
            if keep_set | cut_set != set(proposed):
                raise ValueError("final sequence review did not partition every proposed asset")
        if proposed and not keep_set:
            raise ValueError("final sequence review cannot empty a nonempty film")
        if not set(required) <= keep_set:
            raise ValueError("final sequence review cut a required asset")
        restored_favourite_assets = _restore_sequence_favourite_representation(
            by_alias,
            ordered_proposal,
            keep_set,
            cut_set,
            cut_reasons,
        )
        _require_favourite_representation(by_alias, keep_set)
        overall = bounded_model_text(payload.get("overall_reason"), max_chars=_MAX_REASON_CHARS)
        if overall is None:
            raise ValueError("final sequence review overall reason is unsafe")
    except (TypeError, ValueError) as exc:
        return {
            "status": "unapproved",
            "keep": ordered_proposal,
            "cut": [],
            "overall_reason": "The global sequence review failed open.",
            "warning": str(exc),
        }
    return {
        "status": "approved",
        "keep": [alias for alias in ordered_proposal if alias in keep_set],
        "cut": [
            {"asset_id": alias, "reason": cut_reasons[alias]}
            for alias in ordered_proposal
            if alias in cut_set
        ],
        "discarded_overlapping_cuts": len(overlapping_cuts),
        "restored_favourite_assets": restored_favourite_assets,
        "overall_reason": overall,
    }


def _restore_sequence_favourite_representation(
    by_alias: dict[str, FineCutCandidate],
    ordered_proposal: Sequence[str],
    kept: set[str],
    cut: set[str],
    cut_reasons: dict[str, str],
) -> int:
    """Restore owner evidence only inside a moment the reduce-only review retained."""
    restored = 0
    retained_moments = {by_alias[alias].moment_id for alias in kept}
    for moment_id in retained_moments:
        selected = [by_alias[alias] for alias in kept if by_alias[alias].moment_id == moment_id]
        if any(candidate.favourite for candidate in selected):
            continue
        favourites = [
            alias
            for alias in ordered_proposal
            if by_alias[alias].moment_id == moment_id and by_alias[alias].favourite
        ]
        if not favourites:
            continue
        if _selected_media_outweighs_favourites(
            selected,
            [by_alias[alias] for alias in favourites],
        ):
            continue
        favourite = favourites[0]
        kept.add(favourite)
        cut.discard(favourite)
        cut_reasons.pop(favourite, None)
        restored += 1
    return restored


def _require_favourite_representation(
    by_alias: dict[str, FineCutCandidate],
    kept: set[str],
) -> None:
    favourite_moments = {
        candidate.moment_id for candidate in by_alias.values() if candidate.favourite
    }
    selected_by_moment = {
        moment_id: [
            candidate
            for candidate in by_alias.values()
            if candidate.moment_id == moment_id and candidate.alias in kept
        ]
        for moment_id in {
            candidate.moment_id for candidate in by_alias.values() if candidate.alias in kept
        }
    }
    for moment_id, selected in selected_by_moment.items():
        if moment_id not in favourite_moments or any(candidate.favourite for candidate in selected):
            continue
        favourites = [
            candidate
            for candidate in by_alias.values()
            if candidate.moment_id == moment_id and candidate.favourite
        ]
        if _selected_media_outweighs_favourites(selected, favourites):
            continue
        raise ValueError("a retained favourite-bearing moment has no favourite asset")


def _selected_media_outweighs_favourites(
    selected: Sequence[FineCutCandidate],
    favourites: Sequence[FineCutCandidate],
) -> bool:
    """Let demonstrated motion outrank owner evidence inside one retained moment."""
    return bool(selected and favourites) and max(
        _media_priority(candidate) for candidate in selected
    ) > max(_media_priority(candidate) for candidate in favourites)


def _media_priority(candidate: FineCutCandidate) -> int:
    if candidate.media_kind == "video":
        return 2
    if candidate.media_kind == "live-motion" and candidate.motion_contribution == "meaningful":
        return 1
    return 0


def _project_favourite_representation(
    by_alias: dict[str, FineCutCandidate],
    kept: set[str],
    optional: list[dict[str, str]],
    *,
    required: set[str],
    capacity: int,
) -> int:
    """Project owner evidence inside a moment the model already chose."""
    projected = 0
    selected_moments = tuple(
        dict.fromkeys(by_alias[alias].moment_id for alias in by_alias if alias in kept)
    )
    for moment_id in selected_moments:
        selected = [
            candidate
            for candidate in by_alias.values()
            if candidate.moment_id == moment_id and candidate.alias in kept
        ]
        if any(candidate.favourite for candidate in selected):
            continue
        favourites = [
            candidate
            for candidate in by_alias.values()
            if candidate.moment_id == moment_id and candidate.favourite
        ]
        if not favourites:
            continue
        if _selected_media_outweighs_favourites(selected, favourites):
            continue
        favourite = favourites[0]
        replacement_index = next(
            (
                index
                for index in range(len(optional) - 1, -1, -1)
                if optional[index]["asset_id"] not in required
                and by_alias[optional[index]["asset_id"]].moment_id == moment_id
            ),
            None,
        )
        if len(kept) >= capacity:
            if replacement_index is None:
                raise ValueError(
                    "favourite projection has no replaceable asset inside the selected moment"
                )
            removed = optional[replacement_index]["asset_id"]
            kept.remove(removed)
            optional.pop(replacement_index)
        kept.add(favourite.alias)
        optional.append(
            {
                "asset_id": favourite.alias,
                "reason": "Owner favourite represents the moment the editor already retained.",
            }
        )
        projected += 1
    return projected


def _read_comparisons(
    rows: object,
    by_alias: dict[str, FineCutCandidate],
    kept: set[str],
    required: set[str],
) -> tuple[list[dict[str, str]], int]:
    if not isinstance(rows, list):
        raise ValueError("final asset comparisons must be a list")
    comparisons: list[dict[str, str]] = []
    discarded = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "kept_asset_id",
            "rejected_asset_id",
            "reason",
        }:
            discarded += 1
            continue
        kept_id = row.get("kept_asset_id")
        rejected_id = row.get("rejected_asset_id")
        reason = bounded_model_text(row.get("reason"), max_chars=_MAX_REASON_CHARS)
        if (
            kept_id not in kept
            or kept_id in required
            or rejected_id not in by_alias
            or rejected_id in kept
            or kept_id == rejected_id
            or reason is None
        ):
            discarded += 1
            continue
        comparisons.append(
            {
                "kept_asset_id": kept_id,
                "rejected_asset_id": rejected_id,
                "reason": reason,
            }
        )
    return comparisons, discarded
