"""Render one complete, stable text line from live source and stored annotations."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from immich_memories.analysis.editorial_preparation_picture_facts import plain_picture_facts
from immich_memories.analysis.subject_framing import framing_annotation, subject_framing
from immich_memories.store.asset_annotations import (
    AssetAnnotationFactBatch,
    AssetAnnotationFactRepository,
    StoredAssetAnnotationFacts,
)

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_contracts import EditorialCandidate

ANNOTATION_LINE_RENDERER_VERSION = "annotation-line-v1"

_DARK_BRIGHTNESS = 40.0
_DARK_FRACTION = 0.5
_BLOWN_BRIGHTNESS = 215.0
_BLOWN_FRACTION = 0.4
_LONG_HEX = re.compile(r"[0-9a-f]{32,}", re.IGNORECASE)
_HEAD_SILENCE = {
    "people": frozenset({"undetermined"}),
    "children": frozenset({"undetermined"}),
    "activity": frozenset({"other"}),
    "location": frozenset({"undetermined"}),
    "doc_docling": frozenset({"photograph"}),
    "nsfw_marqo": frozenset({"no"}),
    "venue": frozenset({"other"}),
    "swim": frozenset({"no"}),
}


@dataclass(frozen=True)
class AnnotationContract:
    """Every version that determines the bytes of an annotation line."""

    renderer_version: str
    producer_versions: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.renderer_version.strip():
            raise ValueError("annotation renderer version cannot be blank")
        if (
            not self.producer_versions
            or any(not version.strip() for version in self.producer_versions)
            or len(self.producer_versions) != len(set(self.producer_versions))
        ):
            raise ValueError("annotation producer versions must be unique and nonblank")


@dataclass(frozen=True)
class AssetAnnotationLine:
    """One provider-safe line, keyed internally by its stable asset ID."""

    asset_id: str
    text: str
    description: str | None = None
    heads: tuple[tuple[str, str], ...] = ()
    stitching_burst_id: str | None = None

    def __post_init__(self) -> None:
        if not self.asset_id.strip() or not self.text.strip():
            raise ValueError("annotation line needs an internal ID and provider-visible text")
        head_names = tuple(head for head, _label in self.heads)
        if (
            self.description is not None
            and not self.description.strip()
            or any(not head.strip() or not label.strip() for head, label in self.heads)
            or len(head_names) != len(set(head_names))
            or self.stitching_burst_id is not None
            and not self.stitching_burst_id.strip()
        ):
            raise ValueError("annotation card facts must be unique and nonblank")


@dataclass(frozen=True)
class AnnotationLineBatch:
    """One immutable evidence snapshot shared by readers and card derivation."""

    requested_asset_ids: tuple[str, ...]
    lines: tuple[AssetAnnotationLine, ...]
    missing_asset_ids: tuple[str, ...]
    contract: AnnotationContract
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        requested = self.requested_asset_ids
        line_ids = tuple(line.asset_id for line in self.lines)
        if not requested or len(requested) != len(set(requested)):
            raise ValueError("annotation batch needs unique requested IDs")
        if len(line_ids) != len(set(line_ids)) or set(line_ids).intersection(
            self.missing_asset_ids
        ):
            raise ValueError("annotation batch outcomes must be unique")
        if tuple(asset_id for asset_id in requested if asset_id in set(line_ids)) != line_ids:
            raise ValueError("annotation lines must preserve requested order")
        expected_missing = tuple(
            asset_id for asset_id in requested if asset_id not in set(line_ids)
        )
        if self.missing_asset_ids != expected_missing:
            raise ValueError("annotation batch missing IDs must be the exact complement")

    def as_mapping(self) -> Mapping[str, str]:
        """Return the ordered internal routing map without mutating the snapshot."""
        return {line.asset_id: line.text for line in self.lines}

    def records_by_id(self) -> Mapping[str, AssetAnnotationLine]:
        """Return structured card facts from the same immutable evidence snapshot."""
        return {line.asset_id: line for line in self.lines}


class AnnotationFactReader(Protocol):
    """Read one complete immutable fact snapshot for requested assets."""

    def facts_for(self, asset_ids: tuple[str, ...]) -> AssetAnnotationFactBatch: ...


class _PersonContext(Protocol):
    # Read-only members: the renderer only reads them, and a mutable attribute
    # would force every caller's narrower field type to match exactly.
    @property
    def relationship(self) -> str | None: ...

    @property
    def tier(self) -> str | None: ...

    @property
    def birth_date(self) -> date | str | None: ...


@dataclass(frozen=True)
class _ObservedPerson:
    person_id: str
    name: str
    birth_date: date | None


class StoredAnnotationLineReader:
    """Read all stored fact families in batches and render one line per live asset."""

    def __init__(
        self,
        *,
        store_path: Path,
        candidates: Sequence[EditorialCandidate],
        description_model: str,
        head_versions: Mapping[str, str],
        pixel_producer_key: str,
        picture_facts_producer: str = "",
        people_context: Mapping[str, _PersonContext] | None = None,
        fact_repository: AnnotationFactReader | None = None,
    ) -> None:
        candidate_by_id = {candidate.asset_id: candidate for candidate in candidates}
        if len(candidate_by_id) != len(candidates):
            raise ValueError("annotation reader needs unique candidate IDs")
        self._candidate_by_id = candidate_by_id
        self._head_versions = dict(head_versions)
        self._people_context = dict(people_context or {})
        self._fact_repository = fact_repository or AssetAnnotationFactRepository(
            Path(store_path),
            description_model=description_model,
            head_versions=head_versions,
            pixel_producer_key=pixel_producer_key,
            picture_facts_producer=picture_facts_producer,
        )
        self._contract = AnnotationContract(
            renderer_version=ANNOTATION_LINE_RENDERER_VERSION,
            producer_versions=(
                f"description:{description_model}",
                "flags:all-except-exposure-v2",
                *(f"head:{head}:{version}" for head, version in sorted(head_versions.items())),
                "motion-bursts:legacy-v1",
                "people:immich-live+owner-context-v1",
                f"pixel:{pixel_producer_key}",
                "source:editorial-candidate-v1",
                *((f"picture-facts:{picture_facts_producer}",) if picture_facts_producer else ()),
            ),
        )

    @property
    def contract(self) -> AnnotationContract:
        """Expose the exact immutable annotation bytes contract for downstream keys."""
        return self._contract

    def lines_for(self, asset_ids: tuple[str, ...]) -> AnnotationLineBatch:
        """Return deterministic complete lines, or none when the store cannot be read."""
        ordered_ids = tuple(dict.fromkeys(asset_ids))
        if not ordered_ids:
            raise ValueError("annotation reader needs at least one requested asset")
        if any(asset_id not in self._candidate_by_id for asset_id in ordered_ids):
            return AnnotationLineBatch(
                requested_asset_ids=ordered_ids,
                lines=(),
                missing_asset_ids=ordered_ids,
                contract=self._contract,
                warnings=("!! requested annotation source unavailable",),
            )
        fact_batch = self._fact_repository.facts_for(ordered_ids)
        facts = fact_batch.as_mapping()
        if fact_batch.unavailable_asset_ids or set(facts) != set(ordered_ids):
            return AnnotationLineBatch(
                requested_asset_ids=ordered_ids,
                lines=(),
                missing_asset_ids=ordered_ids,
                contract=self._contract,
                warnings=fact_batch.warnings
                or ("!! annotation fact snapshot incomplete; no partial evidence rendered",),
            )
        rendered_lines = tuple(
            AssetAnnotationLine(
                asset_id=asset_id,
                text=_render_line(
                    self._candidate_by_id[asset_id],
                    facts[asset_id],
                    people_context=self._people_context,
                    head_versions=self._head_versions,
                ),
                description=facts[asset_id].description,
                heads=facts[asset_id].heads,
                stitching_burst_id=_stitching_burst_id(facts[asset_id]),
            )
            for asset_id in ordered_ids
        )
        private_tokens = _private_tokens(
            tuple(self._candidate_by_id[asset_id] for asset_id in ordered_ids),
            tuple(facts[asset_id] for asset_id in ordered_ids),
        )
        contains_private_token = _private_token_matcher(private_tokens)
        lines = tuple(line for line in rendered_lines if not contains_private_token(line.text))
        line_ids = {line.asset_id for line in lines}
        missing_ids = tuple(asset_id for asset_id in ordered_ids if asset_id not in line_ids)
        privacy_warning = (
            (
                f"!! {len(missing_ids)} annotation line(s) withheld because private "
                "identifiers were rendered",
            )
            if missing_ids
            else ()
        )
        return AnnotationLineBatch(
            requested_asset_ids=ordered_ids,
            lines=lines,
            missing_asset_ids=missing_ids,
            contract=self._contract,
            warnings=(*fact_batch.warnings, *privacy_warning),
        )


def _render_line(
    candidate: EditorialCandidate,
    facts: StoredAssetAnnotationFacts,
    *,
    people_context: Mapping[str, _PersonContext],
    head_versions: Mapping[str, str],
) -> str:
    parts = [candidate.taken_at.isoformat(timespec="minutes").replace("T", " ")]
    _append_media(parts, candidate, facts)
    if facts.description:
        parts.append(facts.description)
    if facts.setting and "insufficient" not in facts.setting.casefold():
        parts.append(f"setting: {facts.setting}")
    if facts.exposure and facts.exposure.casefold() != "none":
        parts.append(f"exposure: {facts.exposure}")
    place = _place(candidate)
    if place:
        parts.append(f"at {place}")
    people = _people(candidate, facts)
    if people:
        parts.append(_render_people(people, candidate, people_context))
    framing = subject_framing(facts.faces)
    if framing is not None:
        parts.append(framing_annotation(framing))
    head_bits = _head_bits(facts, head_versions)
    if head_bits:
        parts.append(", ".join(head_bits))
    if candidate.favourite:
        parts.append("STARRED by the photographer")
    if facts.picture_facts is not None and (segment := plain_picture_facts(facts.picture_facts)):
        parts.append(segment)
    parts.extend(_flag_notes(facts))
    parts.extend(_pixel_warnings(facts))
    parts.extend(
        annotation
        for annotation in candidate.grounded_annotations
        if not annotation.startswith(("live-photo-rendering-family:", "live-photo-stitch-members:"))
    )
    return " | ".join(parts)


def _render_people(
    people,
    candidate: EditorialCandidate,
    people_context: Mapping[str, _PersonContext],
) -> str:
    return "with " + "; ".join(
        _render_person(person, candidate.taken_at, people_context.get(person.person_id))
        for person in people
    )


def _head_bits(facts: StoredAssetAnnotationFacts, head_versions: Mapping[str, str]) -> list[str]:
    heads = dict(facts.heads)
    bits: list[str] = []
    for head in sorted(head_versions):
        label = heads.get(head)
        if label and label not in _HEAD_SILENCE.get(head, frozenset()):
            rendered_head = head.replace("doc_docling", "document").replace("nsfw_marqo", "nsfw")
            bits.append(f"{rendered_head}={label}")
    return bits


def _flag_notes(facts: StoredAssetAnnotationFacts) -> list[str]:
    return [
        f"FLAGGED {flag.flag}{f' ({flag.reason})' if flag.reason else ''}"
        for flag in facts.flags
        if flag.flag
    ]


def _append_media(
    parts: list[str],
    candidate: EditorialCandidate,
    facts: StoredAssetAnnotationFacts,
) -> None:
    if candidate.media_kind == "video":
        duration = candidate.shippable_duration
        parts.append(f"VIDEO {duration:.0f}s raw" if duration else "VIDEO")
    if candidate.media_kind != "live_photo":
        return
    motion = facts.motion
    if motion is not None and motion.beats_a_still and len(motion.still_ids) > 1:
        duration = motion.duration_seconds or 0.0
        parts.append(
            f"LIVE PHOTO BURST of {len(motion.still_ids)} stills, "
            f"stitches to a {duration:.0f}s clip"
        )
    elif motion is not None and motion.beats_a_still:
        duration = motion.duration_seconds or 0.0
        parts.append(f"LIVE PHOTO, renders as a {duration:.0f}s clip")
    else:
        parts.append("LIVE PHOTO (renders as a still)")


def _stitching_burst_id(facts: StoredAssetAnnotationFacts) -> str | None:
    motion = facts.motion
    if motion is None or not motion.beats_a_still or not motion.burst_id:
        return None
    return motion.burst_id


def _private_tokens(
    candidates: Sequence[EditorialCandidate],
    facts: Sequence[StoredAssetAnnotationFacts],
) -> frozenset[str]:
    identifiers: list[str | None] = []
    for candidate in candidates:
        identifiers.extend(
            (
                candidate.asset_id,
                candidate.source.live_photo_video_id,
                candidate.rendering_family_id,
                *candidate.live_photo_stitch_member_ids,
                *(person.id for person in (candidate.source.people or ())),
            )
        )
    for stored in facts:
        identifiers.extend(person.person_id for person in stored.people)
        if stored.motion is not None:
            identifiers.extend(
                (
                    stored.motion.burst_id,
                    *stored.motion.still_ids,
                )
            )
    tokens: set[str] = set()
    for value in identifiers:
        identifier = str(value or "").strip().casefold()
        if len(identifier) < 8:
            continue
        tokens.add(identifier)
        try:
            UUID(identifier)
        except ValueError:
            match = _LONG_HEX.search(identifier)
            if match is not None:
                tokens.add(match.group(0)[:8].casefold())
        else:
            tokens.add(identifier[:8])
    return frozenset(tokens)


def _private_token_matcher(tokens: frozenset[str]) -> Callable[[str], bool]:
    """Compile literal substring checks once per annotation cohort.

    Identifier prefixes are already part of the privacy policy. Indexing them
    avoids searching every line once for every identifier in a large period.
    Longer identifiers still require their full literal match; an index prefix
    alone never withholds a line unless it is itself a policy token.
    """
    if not tokens:
        return lambda _text: False
    width = min(8, min(map(len, tokens)))
    terminal = frozenset(token for token in tokens if len(token) == width)
    longer: dict[str, list[str]] = {}
    for token in tokens:
        prefix = token[:width]
        if prefix not in terminal:
            longer.setdefault(prefix, []).append(token)

    def contains(text: str) -> bool:
        rendered = text.casefold()
        for offset in range(len(rendered) - width + 1):
            prefix = rendered[offset : offset + width]
            if prefix in terminal:
                return True
            matches = longer.get(prefix)
            if matches and any(rendered.startswith(token, offset) for token in matches):
                return True
        return False

    return contains


def _people(
    candidate: EditorialCandidate,
    facts: StoredAssetAnnotationFacts,
) -> tuple[_ObservedPerson, ...]:
    observed: list[_ObservedPerson] = []
    seen: set[str] = set()
    for person in candidate.source.people or ():
        name = _clean(person.name)
        if not name:
            continue
        person_id = person.id
        key = person_id or name.casefold()
        seen.add(key)
        observed.append(_ObservedPerson(person_id, name, _as_date(person.birth_date)))
    for fact in facts.people:
        key = fact.person_id or fact.name.casefold()
        if fact.name and key not in seen:
            seen.add(key)
            observed.append(_ObservedPerson(fact.person_id, fact.name, fact.birth_date))
    return tuple(sorted(observed, key=lambda item: item.name.casefold()))


def _render_person(
    person: _ObservedPerson,
    taken_at: datetime,
    context: _PersonContext | None,
) -> str:
    relationship = context.relationship if context is not None else None
    tier = context.tier if context is not None else None
    born = person.birth_date or (_as_date(context.birth_date) if context is not None else None)
    bits = [
        value
        for value in (
            relationship,
            _age_label(born, taken_at),
            f"{tier} circle" if tier else None,
        )
        if value
    ]
    return f"{person.name} ({'; '.join(bits)})" if bits else person.name


def _age_label(born: date | None, when: datetime) -> str | None:
    if born is None:
        return None
    days = (when.date() - born).days
    if days < 0:
        return "capture predates recorded birth date"
    if days <= 1:
        return "newborn"
    if days < 60:
        return f"{days} days old"
    if days < 730:
        return f"{days // 30} months old"
    years = when.year - born.year - ((when.month, when.day) < (born.month, born.day))
    return f"aged {years}"


def _pixel_warnings(facts: StoredAssetAnnotationFacts) -> tuple[str, ...]:
    pixel = facts.pixel
    if pixel is None:
        return ()
    warnings = []
    if (
        pixel.sharpness is not None
        and pixel.soft_below is not None
        and pixel.sharpness < pixel.soft_below
    ):
        warnings.append("SOFT (blurry)")
    if (
        pixel.brightness is not None
        and pixel.brightness < _DARK_BRIGHTNESS
        or pixel.dark_fraction is not None
        and pixel.dark_fraction > _DARK_FRACTION
    ):
        warnings.append("DARK")
    if (
        pixel.brightness is not None
        and pixel.brightness > _BLOWN_BRIGHTNESS
        or pixel.bright_fraction is not None
        and pixel.bright_fraction > _BLOWN_FRACTION
    ):
        warnings.append("BLOWN OUT")
    if pixel.needs_rotation:
        warnings.append("rotated")
    return tuple(warnings)


def _place(candidate: EditorialCandidate) -> str:
    exif = candidate.source.exif_info
    if exif is None:
        return ""
    named = tuple(_clean(value) for value in (exif.city, exif.state, exif.country) if value)
    if named:
        return ", ".join(named)
    if exif.latitude is not None and exif.longitude is not None:
        return f"{exif.latitude:.3f},{exif.longitude:.3f}"
    return ""


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())
