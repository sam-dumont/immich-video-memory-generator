"""Reduce final-cut copies that escaped time-bounded Selects moments."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import combinations
from math import isfinite, sqrt
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from immich_memories.analysis.clip_scaler import describes_the_same_thing
from immich_memories.analysis.duplicate_hashing import compute_thumbnail_hash, hamming_distance
from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.selection_selects import (
    SELECTS_MAX_CORROBORATION,
    AbsorbedFrame,
    SamePicturePairDecision,
    confirm_same_picture_pairs,
)
from immich_memories.analysis.visual_request_planner import VisionRequestLimits

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_gateway import EditorialGateway
    from immich_memories.analysis.visual_atlas import AtlasTile, VisualAtlas

# A newspaper front page and the printed photograph on it are one picture, and
# hashes cannot see it: that measured pair sits at aHash 34/64 -- 3.4x past the
# perceptual gate -- while SSCD (facebookresearch/sscd-copy-detection
# disc_mixup, MIT) puts it at 0.654 cosine. The floor admits that pair and
# nothing further; every SSCD nomination still has to survive the model.
SSCD_MIN_COSINE = 0.60

# Once two frames are confirmed to BE one picture, the copy that is a document
# ABOUT the picture is the worse one to show. Substring match on the card text.
DOCUMENT_ARTIFACT_WORDS = (
    "newspaper",
    "magazine",
    "document",
    "screenshot",
    "receipt",
    "printout",
)


class CopyEmbedder(Protocol):
    """Maps preview JPEG bytes to a copy-detection embedding."""

    def __call__(self, jpeg_bytes: bytes) -> Sequence[float]: ...


@dataclass(frozen=True)
class FinalDuplicateNomination:
    """Cheap pair evidence; only an exact source checksum is itself a cut verdict."""

    earlier_asset_id: str
    later_asset_id: str
    signals: tuple[str, ...]
    perceptual_distance: int | None = None
    nomination_source: str = "hash"
    copy_similarity: float | None = None


@dataclass(frozen=True)
class FinalDuplicateReview:
    """Fail-open final membership after confirmed cross-moment copies are folded."""

    survivors: tuple[EditorialCandidate, ...]
    absorbed: tuple[AbsorbedFrame, ...]
    nominations: tuple[FinalDuplicateNomination, ...]
    decisions: tuple[SamePicturePairDecision, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _DescriptionEvidence:
    llm_description: str
    llm_subjects: tuple[str, ...] = ()


def review_final_duplicates(
    winners: Sequence[EditorialCandidate],
    *,
    descriptions: dict[str, str],
    atlas: VisualAtlas,
    requester: EditorialGateway,
    sheet_output_dir: Path,
    required_asset_ids: Sequence[str] = (),
    limits: VisionRequestLimits | None = None,
    concurrency: int = 1,
    media_priorities: dict[str, int] | None = None,
    copy_embedder: CopyEmbedder | None = None,
) -> FinalDuplicateReview:
    """Find and confirm copies across the assembled final wall.

    Pixel and text similarity only nominate compatible still pairs. A supplied
    copy embedder adds the copies hashes cannot reach -- a printed photograph
    is a very different image of the same picture. Removal then requires the
    same two-order visual agreement as Selects. A full-source checksum is
    already byte-level proof and needs no model opinion; every ambiguous or
    unreadable routed pair keeps both frames.
    """
    ordered = tuple(sorted(winners, key=lambda item: (item.taken_at, item.asset_id)))
    by_id = {candidate.asset_id: candidate for candidate in ordered}
    if len(by_id) != len(ordered):
        raise ValueError("final duplicate review needs unique asset IDs")
    required = frozenset(required_asset_ids)
    if not required <= set(by_id):
        raise ValueError("required final duplicate assets must be winners")
    priorities = media_priorities or {}
    if not set(priorities) <= set(by_id) or any(
        not isinstance(priority, int) or isinstance(priority, bool) or priority < 0
        for priority in priorities.values()
    ):
        raise ValueError("final duplicate media priorities are invalid")

    embeddings = _CopyEmbeddings(copy_embedder)
    nominations = _nominate_pairs(
        ordered,
        descriptions=descriptions,
        atlas=atlas,
        embeddings=embeddings,
    )
    if not nominations:
        return FinalDuplicateReview(ordered, (), (), (), tuple(embeddings.warnings))
    decisions = _confirm_nominations(
        nominations,
        by_id=by_id,
        atlas=atlas,
        requester=requester,
        sheet_output_dir=sheet_output_dir,
        limits=limits,
        concurrency=concurrency,
    )
    if not _decisions_match_nominations(decisions, nominations):
        return FinalDuplicateReview(
            ordered,
            (),
            nominations,
            (),
            (
                *embeddings.warnings,
                "!! Final duplicate confirmation returned mismatched pairs; every asset was kept",
            ),
        )
    return _apply_confirmed_pairs(
        ordered,
        nominations=nominations,
        decisions=decisions,
        required=required,
        media_priorities=priorities,
        descriptions=descriptions,
        nomination_warnings=tuple(embeddings.warnings),
    )


def _nominate_pairs(
    winners: tuple[EditorialCandidate, ...],
    *,
    descriptions: dict[str, str],
    atlas: VisualAtlas,
    embeddings: _CopyEmbeddings,
) -> tuple[FinalDuplicateNomination, ...]:
    tiles_by_id = {tile.entity_id: tile for tile in atlas.tiles}
    hashes = {
        candidate.asset_id: (
            _thumbnail_hash(atlas, candidate.asset_id) if candidate.media_kind == "photo" else None
        )
        for candidate in winners
    }
    rows = (
        _nominate_pair(
            earlier,
            later,
            descriptions=descriptions,
            tiles_by_id=tiles_by_id,
            hashes=hashes,
            embeddings=embeddings,
        )
        for earlier, later in combinations(winners, 2)
    )
    return tuple(row for row in rows if row is not None)


def _nominate_pair(
    earlier: EditorialCandidate,
    later: EditorialCandidate,
    *,
    descriptions: dict[str, str],
    tiles_by_id: dict[str, AtlasTile],
    hashes: dict[str, str | None],
    embeddings: _CopyEmbeddings,
) -> FinalDuplicateNomination | None:
    left_id, right_id = earlier.asset_id, later.asset_id
    left_checksum = earlier.source.checksum
    if left_checksum and left_checksum == later.source.checksum:
        return FinalDuplicateNomination(left_id, right_id, ("exact-checksum",))
    earlier_tile = tiles_by_id.get(left_id)
    later_tile = tiles_by_id.get(right_id)
    if earlier_tile is None or later_tile is None:
        return None
    if earlier.media_kind != "photo" or later.media_kind != "photo":
        return None

    distance = _hash_distance(hashes[left_id], hashes[right_id])
    if earlier_tile.sha256 and earlier_tile.sha256 == later_tile.sha256:
        return FinalDuplicateNomination(left_id, right_id, ("exact-atlas-tile",), distance)
    if distance is not None and distance <= SELECTS_MAX_CORROBORATION:
        left = _DescriptionEvidence(descriptions.get(left_id, ""))
        right = _DescriptionEvidence(descriptions.get(right_id, ""))
        if describes_the_same_thing(left, right):
            return FinalDuplicateNomination(
                left_id, right_id, ("perceptual-description",), distance
            )

    similarity = embeddings.similarity(earlier_tile, later_tile)
    if similarity is None or similarity < SSCD_MIN_COSINE:
        return None
    return FinalDuplicateNomination(
        earlier_asset_id=left_id,
        later_asset_id=right_id,
        signals=("sscd-cosine",),
        perceptual_distance=distance,
        nomination_source="sscd",
        copy_similarity=similarity,
    )


class _CopyEmbeddings:
    """Per-wall SSCD vectors, cached by asset and switched off on first failure.

    Nomination is an addition, never an obligation: an asset the wall has no
    preview for, or a model that will not load, costs this pass its extra
    candidates and nothing else.
    """

    def __init__(self, embedder: CopyEmbedder | None) -> None:
        self._embedder = embedder
        self._vectors: dict[str, tuple[float, ...] | None] = {}
        self.warnings: list[str] = []

    def similarity(self, earlier: AtlasTile, later: AtlasTile) -> float | None:
        left = self._vector(earlier)
        right = self._vector(later)
        if left is None or right is None or len(left) != len(right):
            return None
        return sum(one * other for one, other in zip(left, right, strict=True))

    def _vector(self, tile: AtlasTile) -> tuple[float, ...] | None:
        if self._embedder is None:
            return None
        if tile.entity_id not in self._vectors:
            self._vectors[tile.entity_id] = self._embed(tile)
        return self._vectors[tile.entity_id]

    def _embed(self, tile: AtlasTile) -> tuple[float, ...] | None:
        embedder = self._embedder
        if embedder is None or not tile.jpeg_bytes:
            return None
        try:
            return _unit_vector(embedder(tile.jpeg_bytes))
        # A checkpoint that will not load and a checkpoint that returns nonsense
        # are the same event here: this pass loses candidates, not pictures.
        except Exception as error:
            self._embedder = None
            self.warnings.append(
                f"!! Copy-detection embeddings unavailable; hash nomination only: {error}"
            )
            return None


def _unit_vector(values: Sequence[float]) -> tuple[float, ...] | None:
    """Cosine similarity is a dot product only once both sides are unit length."""
    norm = sqrt(sum(float(value) * float(value) for value in values))
    if not isfinite(norm) or norm <= 0:
        return None
    return tuple(float(value) / norm for value in values)


def _confirm_nominations(
    nominations: tuple[FinalDuplicateNomination, ...],
    *,
    by_id: dict[str, EditorialCandidate],
    atlas: VisualAtlas,
    requester: EditorialGateway,
    sheet_output_dir: Path,
    limits: VisionRequestLimits | None,
    concurrency: int,
) -> tuple[SamePicturePairDecision, ...]:
    routed = tuple(row for row in nominations if "exact-checksum" not in row.signals)
    routed_decisions: tuple[SamePicturePairDecision, ...] = ()
    if routed:
        pairs = tuple((by_id[row.earlier_asset_id], by_id[row.later_asset_id]) for row in routed)
        routed_decisions = confirm_same_picture_pairs(
            pairs,
            atlas=atlas,
            requester=requester,
            sheet_output_dir=sheet_output_dir,
            corroborating_distances=tuple(row.perceptual_distance for row in routed),
            limits=limits,
            concurrency=concurrency,
        )
    routed_by_pair = {
        (decision.earlier_asset_id, decision.later_asset_id): decision
        for decision in routed_decisions
    }
    decisions: list[SamePicturePairDecision] = []
    for nomination in nominations:
        pair = (nomination.earlier_asset_id, nomination.later_asset_id)
        if "exact-checksum" in nomination.signals:
            decisions.append(SamePicturePairDecision(*pair, True))
        elif decision := routed_by_pair.get(pair):
            decisions.append(decision)
        else:
            decisions.append(
                SamePicturePairDecision(
                    *pair,
                    False,
                    "!! Final duplicate confirmation omitted a routed pair; both kept",
                )
            )
    return tuple(decisions)


def _thumbnail_hash(atlas: VisualAtlas, asset_id: str) -> str | None:
    try:
        tile = atlas.tile_for(asset_id)
        if tile.jpeg_bytes is None:
            return None
        return compute_thumbnail_hash(tile.jpeg_bytes) or None
    except (KeyError, OSError, ValueError):
        return None


def _hash_distance(left: str | None, right: str | None) -> int | None:
    if left is None or right is None:
        return None
    try:
        return hamming_distance(left, right)
    except (TypeError, ValueError):
        return None


def _decisions_match_nominations(
    decisions: tuple[SamePicturePairDecision, ...],
    nominations: tuple[FinalDuplicateNomination, ...],
) -> bool:
    return len(decisions) == len(nominations) and all(
        (decision.earlier_asset_id, decision.later_asset_id)
        == (nomination.earlier_asset_id, nomination.later_asset_id)
        for decision, nomination in zip(decisions, nominations, strict=True)
    )


def _apply_confirmed_pairs(
    winners: tuple[EditorialCandidate, ...],
    *,
    nominations: tuple[FinalDuplicateNomination, ...],
    decisions: tuple[SamePicturePairDecision, ...],
    required: frozenset[str],
    media_priorities: dict[str, int],
    descriptions: dict[str, str],
    nomination_warnings: tuple[str, ...] = (),
) -> FinalDuplicateReview:
    by_id = {candidate.asset_id: candidate for candidate in winners}
    documents = frozenset(
        asset_id for asset_id in by_id if _reads_as_document(descriptions.get(asset_id, ""))
    )
    survivor_by_id = dict(by_id)
    absorbed: list[AbsorbedFrame] = []
    warnings = [*nomination_warnings]
    warnings.extend(decision.warning for decision in decisions if decision.warning)
    for members in _confirmed_components(winners, decisions):
        if len(members) < 2:
            continue
        folded = _fold_component(
            members,
            required=required,
            media_priorities=media_priorities,
            documents=documents,
        )
        survivor_by_id[folded.keeper.asset_id] = folded.keeper
        for frame in folded.absorbed:
            survivor_by_id.pop(frame.asset_id, None)
        absorbed.extend(folded.absorbed)
        if folded.warning:
            warnings.append(folded.warning)

    survivors = tuple(
        survivor_by_id[candidate.asset_id]
        for candidate in winners
        if candidate.asset_id in survivor_by_id
    )
    return FinalDuplicateReview(
        survivors=survivors,
        absorbed=tuple(absorbed),
        nominations=nominations,
        decisions=decisions,
        warnings=tuple(warnings),
    )


@dataclass(frozen=True)
class _FoldedComponent:
    keeper: EditorialCandidate
    absorbed: tuple[AbsorbedFrame, ...]
    warning: str | None = None


def _confirmed_components(
    winners: tuple[EditorialCandidate, ...],
    decisions: tuple[SamePicturePairDecision, ...],
) -> list[list[EditorialCandidate]]:
    """Group only frames every member of the group was directly confirmed against."""
    positive = {
        frozenset((decision.earlier_asset_id, decision.later_asset_id))
        for decision in decisions
        if decision.same
    }
    components: list[list[EditorialCandidate]] = []
    for candidate in winners:
        for members in components:
            if all(
                frozenset((candidate.asset_id, member.asset_id)) in positive for member in members
            ):
                members.append(candidate)
                break
        else:
            components.append([candidate])
    return components


def _fold_component(
    members: Sequence[EditorialCandidate],
    *,
    required: frozenset[str],
    media_priorities: dict[str, int],
    documents: frozenset[str],
) -> _FoldedComponent:
    required_members = [candidate for candidate in members if candidate.asset_id in required]
    if len(required_members) > 1:
        keeper = _canonical_keeper(
            required_members,
            favourite_sources=members,
            media_priorities=media_priorities,
            documents=documents,
        )
        return _FoldedComponent(
            keeper,
            tuple(
                _absorbed_duplicate(candidate, keeper, documents=documents)
                for candidate in members
                if candidate.asset_id not in required
            ),
            "!! Confirmed required duplicates remain because runtime obligations outrank "
            "the final duplicate audit",
        )
    keeper = (
        _required_keeper(required_members[0], members)
        if required_members
        else _canonical_keeper(
            members,
            favourite_sources=members,
            media_priorities=media_priorities,
            documents=documents,
        )
    )
    return _FoldedComponent(
        keeper,
        tuple(
            _absorbed_duplicate(candidate, keeper, documents=documents)
            for candidate in members
            if candidate.asset_id != keeper.asset_id
        ),
    )


def _required_keeper(
    required: EditorialCandidate,
    members: Sequence[EditorialCandidate],
) -> EditorialCandidate:
    if required.favourite or not any(candidate.favourite for candidate in members):
        return required
    return replace(required, favourite=True)


def _reads_as_document(description: str) -> bool:
    """Whether a card describes a document ABOUT a picture rather than the picture."""
    lowered = description.lower()
    return any(word in lowered for word in DOCUMENT_ARTIFACT_WORDS)


def _canonical_keeper(
    candidates: Sequence[EditorialCandidate],
    *,
    favourite_sources: Sequence[EditorialCandidate],
    media_priorities: dict[str, int],
    documents: frozenset[str],
) -> EditorialCandidate:
    """Prefer the picture over a document of it, then rendering quality, then first."""
    keeper = max(
        enumerate(candidates),
        key=lambda item: (
            item[1].asset_id not in documents,
            media_priorities.get(item[1].asset_id, 0),
            -item[0],
        ),
    )[1]
    if keeper.favourite or not any(candidate.favourite for candidate in favourite_sources):
        return keeper
    return replace(keeper, favourite=True)


def _absorbed_duplicate(
    candidate: EditorialCandidate,
    keeper: EditorialCandidate,
    *,
    documents: frozenset[str],
) -> AbsorbedFrame:
    decided_by_document = candidate.asset_id in documents and keeper.asset_id not in documents
    rule = "non-document" if decided_by_document else "media-priority-then-first-occurrence"
    return AbsorbedFrame(
        asset_id=candidate.asset_id,
        kept_asset_id=keeper.asset_id,
        reason=(
            "two arrangements confirmed this cross-moment asset is another copy of the kept "
            f"picture; survivor rule: {rule}"
        ),
    )
