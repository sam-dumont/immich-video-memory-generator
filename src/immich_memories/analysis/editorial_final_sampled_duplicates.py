"""Bounded final-film duplicate discovery over actual displayed sampled material."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from immich_memories.analysis.duplicate_hashing import hamming_distance
from immich_memories.analysis.editorial_picture_evidence import PictureEvidenceOverlay
from immich_memories.analysis.moment_grouping import EPISODE_WINDOW_MINUTES
from immich_memories.analysis.selection_same_picture import SELECTS_MAX_CORROBORATION

# Jaccard over description tokens, at the knee of the measured curve. On
# 1,124,250 real pairs from the cache: 0.60 collapses 33, 0.55 collapses 74,
# 0.50 collapses 135 — the count triples per step below this, which is where
# genuinely different shots start merging. Above it, real duplicates survive:
# the same child in the same hallway scored 0.70 differing only on a t-shirt.
_SAME_THING_THRESHOLD = 0.60

# Short words carry setting, not subject. "in the kitchen" should not make a
# birthday and the washing-up look alike.
_MEANINGFUL_WORD = 4


def _describing_words(clip: object) -> frozenset[str]:
    """What a clip is said to show, as comparable tokens."""
    described = getattr(clip, "llm_description", None)
    subjects = getattr(clip, "llm_subjects", None) or []
    text = " ".join([str(described or ""), *(str(s) for s in subjects)])
    return frozenset(re.findall(rf"[a-z]{{{_MEANINGFUL_WORD},}}", text.lower()))


def describes_the_same_thing(first: object, second: object) -> bool:
    """Whether two clips are photographs of one thing rather than two.

    Asks what the clips are OF, using descriptions already banked -- no model
    call. A clip nothing has described is never merged: treating "unknown" as
    "similar" would quietly collapse the undescribed majority into each other.
    """
    left = _describing_words(getattr(first, "clip", first))
    right = _describing_words(getattr(second, "clip", second))
    if not left or not right:
        return False
    overlap = len(left & right) / len(left | right)
    return overlap >= _SAME_THING_THRESHOLD


def displayed_sample_members(unit: Mapping[str, Any]) -> tuple[str, ...]:
    """Match the source projector: only Live motion expands its declared video material."""
    kind = unit.get("kind")
    if kind in {"still", "photo", "live-still", "video"}:
        return (unit["asset_id"],)
    if kind == "live-motion":
        videos = unit.get("video_ids")
        if not isinstance(videos, (list, tuple)) or not videos:
            return ()
        if any(not isinstance(value, str) or not value for value in videos):
            return ()
        if len(set(videos)) != len(videos):
            return ()
        return tuple(videos)
    # Legacy generic callers can still detect compound evidence conservatively;
    # the final reducer rejects unsupported rendering kinds in _material.
    members = PictureEvidenceOverlay.material_members(unit)
    return tuple(dict.fromkeys((*members, *unit.get("video_ids", ()))))


def _material(unit: Mapping[str, Any]) -> tuple[tuple[str, ...], str | None]:
    members = displayed_sample_members(unit)
    kind = unit.get("kind")
    if kind in {"still", "photo", "live-still", "video"}:
        return members, None
    if kind == "live-motion":
        if not members:
            return members, "live_motion_has_no_valid_declared_video_material"
        return members, None
    return members, "unsupported_rendered_kind"


def _captured_at(unit):
    taken = datetime.fromisoformat(unit["taken"])
    return taken if taken.tzinfo is not None else taken.replace(tzinfo=UTC)


def _priority(unit, protected, quality):
    asset_id = unit["asset_id"]
    value = quality.get(asset_id)
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
    ):
        raise ValueError("existing objective quality must be finite or unavailable")
    return (
        not protected,
        not bool(unit.get("favourite")),
        value is None,
        -float(value) if value is not None else 0.0,
        _captured_at(unit).timestamp(),
        asset_id,
    )


def _nomination_edge(left, right, hashes, records, *, same_episode=False):
    distance = (
        hamming_distance(hashes[left], hashes[right])
        if left in hashes and right in hashes
        else None
    )
    signals = ["same-episode"] if same_episode else []
    if distance is not None and distance <= SELECTS_MAX_CORROBORATION:
        signals.append("hash")
    left_own = records.get(left, {}).get("description")
    right_own = records.get(right, {}).get("description")
    if (
        isinstance(left_own, str)
        and isinstance(right_own, str)
        and describes_the_same_thing(
            SimpleNamespace(llm_description=left_own),
            SimpleNamespace(llm_description=right_own),
        )
    ):
        signals.append("own-description")
    if not signals:
        return None
    return {
        "remove_member": left,
        "keeper_member": right,
        "distance": distance,
        "signals": signals,
    }


@dataclass
class _MaterialIndex:
    """What each carrier actually displays, and what is missing before any comparison."""

    members: dict[str, tuple[str, ...]]
    unavailable: dict[str, list[dict[str, Any]]]
    protected: dict[str, bool]
    hashes: dict[str, str]
    records: Mapping[str, Mapping[str, Any]]


def _bound_samples(
    bound_sample_members: Mapping[str, tuple[str, ...]] | None, by_id: Mapping[str, Any]
) -> Mapping[str, tuple[str, ...]]:
    sampled = bound_sample_members or {}
    if not set(sampled) <= by_id.keys() or any(
        by_id[key].get("kind") != "live-motion"
        or not isinstance(members, tuple)
        or any(not isinstance(member, str) or not member for member in members)
        or len(set(members)) != len(members)
        for key, members in sampled.items()
    ):
        raise ValueError("bound sample mapping requires unique Live material sample identities")
    return sampled


def _member_gaps(
    members: tuple[str, ...],
    reason: str | None,
    picture_records: Mapping[str, Mapping[str, Any]],
    preview_hashes: Mapping[str, str | None],
    hashes: dict[str, str],
) -> list[dict[str, Any]]:
    """Collect this carrier's evidence gaps, keeping every usable member hash."""
    gaps: list[dict[str, Any]] = [{"reason": reason}] if reason else []
    for member in members:
        if picture_records.get(member, {}).get("status") != "available":
            gaps.append({"member": member, "reason": "own_picture_unavailable"})
        value = preview_hashes.get(member)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{16}", value) is None:
            gaps.append({"member": member, "reason": "preview_hash_unavailable"})
        else:
            hashes[member] = value.lower()
    return gaps


def _material_index(
    original: Sequence[dict[str, Any]],
    *,
    sampled: Mapping[str, tuple[str, ...]],
    protected: set[str],
    picture_records: Mapping[str, Mapping[str, Any]],
    preview_hashes: Mapping[str, str | None],
) -> _MaterialIndex:
    material: dict[str, tuple[str, ...]] = {}
    unavailable: dict[str, list[dict[str, Any]]] = {}
    is_protected: dict[str, bool] = {}
    hashes: dict[str, str] = {}
    for unit in original:
        asset_id = unit["asset_id"]
        if asset_id in sampled:
            members = sampled[asset_id]
            reason = None if members else "live_motion_has_no_bound_displayed_samples"
        else:
            members, reason = _material(unit)
        material[asset_id] = members
        original_members = (
            (*unit.get("members", ()), *unit.get("video_ids", ())) if asset_id in sampled else ()
        )
        is_protected[asset_id] = bool(protected & {asset_id, *members, *original_members})
        gaps = _member_gaps(members, reason, picture_records, preview_hashes, hashes)
        if gaps:
            unavailable[asset_id] = gaps
    return _MaterialIndex(material, unavailable, is_protected, hashes, picture_records)


def _episode_of(unit: Mapping[str, Any]) -> str:
    # Event families come from capture time and place, independent of the
    # model splitting similar views into differently named stories or moments.
    return str(unit.get("event") or unit.get("moment") or "")


def _nearby_episode(left, right) -> bool:
    episode = _episode_of(left)
    return (
        bool(episode)
        and episode == _episode_of(right)
        and abs((_captured_at(left) - _captured_at(right)).total_seconds())
        <= EPISODE_WINDOW_MINUTES * 60
    )


def _nominations(
    ordered: Sequence[dict[str, Any]], index: _MaterialIndex
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    nominations: list[dict[str, Any]] = []
    by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for position, keeper in enumerate(ordered):
        for remove in ordered[position + 1 :]:
            kept_id, removed_id = keeper["asset_id"], remove["asset_id"]
            same_episode = _nearby_episode(keeper, remove)
            edges = [
                edge
                for left in index.members[removed_id]
                for right in index.members[kept_id]
                if (
                    edge := _nomination_edge(
                        left, right, index.hashes, index.records, same_episode=same_episode
                    )
                )
                is not None
            ]
            if edges:
                row = {
                    "remove": removed_id,
                    "keeper": kept_id,
                    "status": "pending",
                    "nominated_edges": edges,
                    "checks": [],
                }
                nominations.append(row)
                by_pair[(removed_id, kept_id)] = row
    return nominations, by_pair


class _RelationCache:
    """One conserved outcome per source pair and question, inside a fixed work bound."""

    def __init__(
        self,
        confirm: Callable[[str, str, int | None], Mapping[str, Any]],
        limit: int,
        confirm_episode: Callable[[str, str, int | None], Mapping[str, Any]] | None = None,
    ) -> None:
        self._confirm = {"picture": confirm, "episode": confirm_episode or confirm}
        self._limit = limit
        self._results: dict[tuple[str, ...], dict[str, Any]] = {}
        self.checks_used = 0

    def __call__(
        self, left: str, right: str, distance: int | None = None, *, same_episode: bool = False
    ) -> dict[str, Any] | None:
        mode = "episode" if same_episode else "picture"
        earlier, later = sorted((left, right))
        key = (mode, earlier, later)
        if left == right:
            return {"same": True, "basis": "identical_source_material_member"}
        if key not in self._results:
            if self.checks_used >= self._limit:
                return None
            self.checks_used += 1
            outcome = deepcopy(dict(self._confirm[mode](earlier, later, distance)))
            if "same" not in outcome or type(outcome["same"]) not in (bool, type(None)):
                raise ValueError("sampled relation must report same/different/unavailable")
            self._results[key] = outcome
        return deepcopy(self._results[key])


def _matched_member(
    row: dict[str, Any], member: str, relation: _RelationCache, proof: list[dict[str, Any]]
) -> bool:
    """Bind one removed member to a keeper member, or record why it stays."""
    matches = sorted(
        (edge for edge in row["nominated_edges"] if edge["remove_member"] == member),
        key=lambda edge: (
            edge["remove_member"] != edge["keeper_member"],
            edge["distance"] if edge["distance"] is not None else math.inf,
            edge["keeper_member"],
        ),
    )
    if not matches:
        row["status"] = "unmatched_displayed_member"
        return False
    unknown = False
    for edge in matches:
        # Only a hash nomination's distance corroborates; a description match is not pixels.
        corroborating = edge["distance"] if "hash" in edge["signals"] else None
        # Corroborated pixels keep the measured same-picture question and its second
        # vote; the episode question is for pairs only the capture family nominated.
        outcome = relation(
            member,
            edge["keeper_member"],
            corroborating,
            same_episode="same-episode" in edge["signals"] and corroborating is None,
        )
        if outcome is None:
            row["status"] = "work_limit"
            return False
        evidence = edge | {"relation": outcome}
        row["checks"].append(evidence)
        if outcome["same"] is True:
            proof.append(evidence)
            return True
        unknown |= outcome["same"] is None
    row["status"] = "relation_unavailable" if unknown else "sampled_different"
    return False


def _proof_for(
    row: dict[str, Any], index: _MaterialIndex, relation: _RelationCache
) -> list[dict[str, Any]] | None:
    removed_id, kept_id = row["remove"], row["keeper"]
    if removed_id in index.unavailable or kept_id in index.unavailable:
        row["status"] = "unavailable_material"
        return None
    proof: list[dict[str, Any]] = []
    for member in index.members[removed_id]:
        if not _matched_member(row, member, relation, proof):
            return None
    row["direct_member_proof"] = proof
    return proof


def _retention_conflict(
    unit: Mapping[str, Any], keeper: Mapping[str, Any], index: _MaterialIndex
) -> tuple[str, str] | None:
    if index.protected[unit["asset_id"]]:
        return "protected_duplicate", "exact_protected_material_retained"
    if unit.get("favourite") and not keeper.get("favourite"):
        return "favorite_retained", "favorite_not_replaced_by_nonfavorite"
    return None


def _removed_against_kept(
    unit: dict[str, Any],
    kept: list[dict[str, Any]],
    by_pair: Mapping[tuple[str, str], dict[str, Any]],
    index: _MaterialIndex,
    relation: _RelationCache,
    removals: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
) -> bool:
    asset_id = unit["asset_id"]
    for keeper in kept:
        kept_id = keeper["asset_id"]
        row = by_pair.get((asset_id, kept_id))
        if row is None:
            continue
        proof = _proof_for(row, index, relation)
        if proof is None:
            continue
        conflict = _retention_conflict(unit, keeper, index)
        if conflict is not None:
            row["status"] = conflict[0]
            conflicts.append({"asset_id": asset_id, "keeper": kept_id, "reason": conflict[1]})
            continue
        row["status"] = "removed"
        removals.append({"asset_id": asset_id, "keeper": kept_id, "direct_member_proof": proof})
        return True
    return False


_UNRESOLVED = frozenset(
    {"work_limit", "relation_unavailable", "unavailable_material", "unresolved"}
)


def _survivors(
    ordered: Sequence[dict[str, Any]],
    by_pair: Mapping[tuple[str, str], dict[str, Any]],
    index: _MaterialIndex,
    relation: _RelationCache,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    removals: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for unit in ordered:
        if not _removed_against_kept(unit, kept, by_pair, index, relation, removals, conflicts):
            kept.append(unit)
    return kept, removals, conflicts


def _closed_nominations(
    nominations: list[dict[str, Any]], removed_ids: set[str]
) -> list[dict[str, Any]]:
    """Give every pending nomination its final status and report what stayed open."""
    for row in nominations:
        if row["status"] == "pending":
            row["status"] = (
                "not_needed_removed_unit"
                if {row["remove"], row["keeper"]} & removed_ids
                else "unresolved"
            )
    return [
        row
        for row in nominations
        if row["status"] in _UNRESOLVED and not {row["remove"], row["keeper"]} & removed_ids
    ]


def reduce_final_sampled_duplicates(
    carriers: Sequence[dict[str, Any]],
    *,
    picture_records: Mapping[str, Mapping[str, Any]],
    preview_hashes: Mapping[str, str | None],
    confirm_relation: Callable[[str, str, int | None], Mapping[str, Any]],
    confirm_episode_relation: Callable[[str, str, int | None], Mapping[str, Any]] | None = None,
    protected_asset_ids: Sequence[str] = (),
    objective_quality: Mapping[str, float | None] | None = None,
    max_relation_checks: int | None = None,
    bound_sample_members: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Retain original order and source fields; only direct sampled proof permits removal.

    All selected dates, events and media kinds can nominate one another. Shared capture
    episode and existing own-description similarity are discovery signals only; a hash
    nomination also hands the callback its own distance, which the callback may spend
    instead of a second arrangement. A pair nominated by its episode alone asks the
    episode callback instead, because no pixels corroborate it. The callbacks own
    exact conserved pixels and relation reuse; their returned evidence must be stable, with
    operational metrics kept elsewhere.
    A positive is editorial sampled redundancy, never equality of unseen video motion.
    Each removed material member must directly match a member of a surviving keeper.
    The fixed 2N default is a work bound; unresolved comparisons retain their pictures.
    """
    original = list(carriers)
    by_id = {unit["asset_id"]: unit for unit in original}
    if len(by_id) != len(original) or any(not isinstance(key, str) or not key for key in by_id):
        raise ValueError("final carriers need unique nonblank source IDs")
    limit = 2 * len(original) if max_relation_checks is None else max_relation_checks
    if type(limit) is not int or limit < 0:
        raise ValueError("relation work limit must be a nonnegative integer")
    sampled = _bound_samples(bound_sample_members, by_id)
    index = _material_index(
        original,
        sampled=sampled,
        protected=set(protected_asset_ids),
        picture_records=picture_records,
        preview_hashes=preview_hashes,
    )
    qualities = objective_quality or {}
    ordered = sorted(
        original, key=lambda unit: _priority(unit, index.protected[unit["asset_id"]], qualities)
    )
    nominations, by_pair = _nominations(ordered, index)
    relation = _RelationCache(confirm_relation, limit, confirm_episode_relation)
    kept, removals, conflicts = _survivors(ordered, by_pair, index, relation)
    removed_ids = {row["asset_id"] for row in removals}
    unresolved = _closed_nominations(nominations, removed_ids)
    survivors = [unit for unit in original if unit["asset_id"] not in removed_ids]
    return survivors, {
        "policy": "final-displayed-sampled-duplicates-v3",
        "scope": "direct sampled editorial redundancy only; no equality of unseen video motion",
        "maximum_hash_distance": SELECTS_MAX_CORROBORATION,
        "description_nomination": "existing describes_the_same_thing on own description only",
        "episode_nomination": "shared capture time/place family within the 90-minute window",
        "relation_check_limit": limit,
        "relation_checks": relation.checks_used,
        "input_carriers": len(original),
        "output_carriers": len(survivors),
        "displayed_members": index.members,
        "nominations": nominations,
        "removals": removals,
        "unavailable": index.unavailable,
        "protected_conflicts": conflicts,
        "incomplete": bool(unresolved or index.unavailable or conflicts),
        "unresolved_nominations": len(unresolved),
    }
