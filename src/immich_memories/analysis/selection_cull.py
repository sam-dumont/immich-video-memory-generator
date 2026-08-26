"""Reject-only Pass 1 over banked episode scans and retained atlas pixels."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from immich_memories.analysis.contact_sheets import (
    ContactSheetPage,
    build_contact_sheets,
    sheet_layout,
)
from immich_memories.analysis.cull_answer import CullDecision, read_cull_namespaces
from immich_memories.analysis.editorial_contracts import (
    DecisionProvenance,
    EditorialCandidate,
    PassTrace,
    RequestTrace,
    TraceDecision,
)
from immich_memories.analysis.period_insight import (
    EpisodeScanAttempt,
    EpisodeScanPack,
    PassZeroResult,
)
from immich_memories.analysis.selection_flow import PreparedEditorialSource

PASS_ONE_VERSION = "pass-1-v1"  # noqa: S105 - public editorial pass identity
_REVIEW_STATE_COLOURS = {
    "KEEP": (45, 65, 75),
    "RECORD": (0, 115, 150),
    "CULL": (170, 20, 20),
}
_REVIEW_FAVOURITE_COLOUR = (180, 130, 0)


@dataclass(frozen=True)
class CullReviewEntry:
    """One globally numbered owner-review visual and its Pass 1 state."""

    number: int
    page_id: str
    asset_id: str
    taken_at: str
    favourite: bool
    status: Literal["KEEP", "RECORD", "CULL"]
    reason: str | None
    source_tile_sha256: str | None


@dataclass(frozen=True)
class CullReviewArtifacts:
    """Zero-call owner pages and their complete machine-readable legend."""

    pages: tuple[ContactSheetPage, ...]
    entries: tuple[CullReviewEntry, ...]
    warnings: tuple[str, ...]
    manifest_path: Path


@dataclass(frozen=True)
class CullPassResult:
    """Chronological Pass 1 membership after junk and failures are removed."""

    survivors: tuple[EditorialCandidate, ...]
    rejected: tuple[CullDecision, ...]
    warnings: tuple[str, ...]
    trace: PassTrace
    review: CullReviewArtifacts
    actual_calls: int = 0


@dataclass(frozen=True)
class _PackCullReading:
    rejects: tuple[CullDecision, ...] = ()
    warnings: tuple[str, ...] = ()
    requests: tuple[RequestTrace, ...] = ()


def run_cull(
    prepared: PreparedEditorialSource,
    pass_zero: PassZeroResult,
    *,
    review_output_dir: Path,
) -> CullPassResult:
    """Reparse banked v3 Pass 1 namespaces without making another model request."""
    attempts = {(attempt.pack_id, attempt.page_id): attempt for attempt in pass_zero.scan_attempts}
    readings = tuple(
        _read_pack_cull(pack, attempts.get((pack.pack_id, pack.page.sheet_id)))
        for pack in pass_zero.episode_packs
    )
    rejects, pass_one_warnings, logical_requests = _combine_pack_readings(readings)
    rejects, favourite_warnings = _protect_favourites(prepared, rejects)
    pass_one_warnings = _ordered_unique((*pass_one_warnings, *favourite_warnings))
    ordered_rejects, survivors = _apply_cull(prepared, rejects)
    pass_one_warnings = _with_over_cull_warning(
        pass_one_warnings,
        len(ordered_rejects),
        len(prepared.candidates),
    )
    _record_new_warnings(prepared, pass_one_warnings)
    recorded_trace = _record_cull_trace(
        prepared,
        pass_zero,
        survivors,
        ordered_rejects,
        logical_requests,
    )
    warnings = _authoritative_warnings(prepared)
    review = _render_review(
        prepared,
        pass_zero,
        ordered_rejects,
        warnings,
        review_output_dir,
    )
    return CullPassResult(
        survivors,
        ordered_rejects,
        warnings,
        recorded_trace,
        review,
    )


def _read_pack_cull(
    pack: EpisodeScanPack,
    attempt: EpisodeScanAttempt | None,
) -> _PackCullReading:
    if attempt is None:
        return _PackCullReading(warnings=(f"!! Pass 1 missing episode scan: {pack.page.sheet_id}",))
    requests = (
        ()
        if attempt.request_trace is None
        else (replace(attempt.request_trace, actual_calls=0, attempts=()),)
    )
    if attempt.answer is None:
        return _PackCullReading(
            warnings=(f"!! Pass 1 failed episode scan: {pack.page.sheet_id}",),
            requests=requests,
        )
    if not _bank_matches_pack(pack, attempt.answer.request_trace):
        return _PackCullReading(
            warnings=(f"!! Pass 1 mismatched episode scan provenance: {pack.page.sheet_id}",),
            requests=requests,
        )
    parsed = read_cull_namespaces(
        attempt.answer.raw_text,
        pack_alias=1,
        tile_map=_tile_map(pack),
        episode_tiles=_episode_tiles(pack),
        unavailable_asset_ids=frozenset(
            asset_id for scope in pack.scopes for asset_id in scope.unavailable_asset_ids
        ),
    )
    if parsed is None:
        return _PackCullReading(
            warnings=(f"!! Pass 1 unreadable episode scan: {pack.page.sheet_id}",),
            requests=requests,
        )
    return _PackCullReading(
        rejects=parsed.cull_rejects,
        warnings=_namespace_warnings(pack.page.sheet_id, parsed.cull_valid) + parsed.warnings,
        requests=requests,
    )


def _namespace_warnings(page_id: str, cull_valid: bool) -> tuple[str, ...]:
    return () if cull_valid else (f"!! Pass 1 invalid Cull namespace: {page_id}",)


def _combine_pack_readings(
    readings: tuple[_PackCullReading, ...],
) -> tuple[
    tuple[CullDecision, ...],
    tuple[str, ...],
    tuple[RequestTrace, ...],
]:
    rejects = tuple(reject for reading in readings for reject in reading.rejects)
    warnings = _ordered_unique(
        tuple(warning for reading in readings for warning in reading.warnings)
    )
    requests = tuple(request for reading in readings for request in reading.requests)
    return rejects, warnings, requests


def _apply_cull(
    prepared: PreparedEditorialSource,
    rejects: tuple[CullDecision, ...],
) -> tuple[tuple[CullDecision, ...], tuple[EditorialCandidate, ...]]:
    order = {candidate.asset_id: index for index, candidate in enumerate(prepared.candidates)}
    ordered_rejects = tuple(sorted(rejects, key=lambda item: order[item.asset_id]))
    rejected_ids = {decision.asset_id for decision in ordered_rejects}
    survivors = tuple(
        candidate for candidate in prepared.candidates if candidate.asset_id not in rejected_ids
    )
    return ordered_rejects, survivors


def _protect_favourites(
    prepared: PreparedEditorialSource,
    rejects: tuple[CullDecision, ...],
) -> tuple[tuple[CullDecision, ...], tuple[str, ...]]:
    favourite_ids = {candidate.asset_id for candidate in prepared.candidates if candidate.favourite}
    protected = tuple(decision for decision in rejects if decision.asset_id in favourite_ids)
    accepted = tuple(decision for decision in rejects if decision.asset_id not in favourite_ids)
    warnings = tuple(
        f"!! cull reject conflicted with protected favourite: {decision.asset_id}"
        for decision in protected
    )
    return accepted, warnings


def _with_over_cull_warning(
    warnings: tuple[str, ...],
    reject_count: int,
    candidate_count: int,
) -> tuple[str, ...]:
    if reject_count * 4 > candidate_count * 3:
        return (*warnings, "!! possible over-cull")
    return warnings


def _record_new_warnings(
    prepared: PreparedEditorialSource,
    warnings: tuple[str, ...],
) -> None:
    prepared.trace.warnings.extend(
        warning for warning in warnings if warning not in prepared.trace.warnings
    )


def _authoritative_warnings(prepared: PreparedEditorialSource) -> tuple[str, ...]:
    return _ordered_unique(
        tuple(
            warning if warning.startswith("!!") else f"!! {warning}"
            for warning in prepared.trace.warnings
        )
    )


def _record_cull_trace(
    prepared: PreparedEditorialSource,
    pass_zero: PassZeroResult,
    survivors: tuple[EditorialCandidate, ...],
    rejects: tuple[CullDecision, ...],
    requests: tuple[RequestTrace, ...],
) -> PassTrace:
    provenance = _pass_provenance(prepared, pass_zero, requests)
    prepared.trace.record_editorial_pass(
        PassTrace(
            name="pass-1-cull",
            input_ids=prepared.candidate_ids,
            kept_ids=tuple(candidate.asset_id for candidate in survivors),
            rejected=tuple(TraceDecision(item.asset_id, item.reason) for item in rejects),
            unresolved=(),
            duration_before=sum(item.shippable_duration for item in prepared.candidates),
            duration_after=sum(item.shippable_duration for item in survivors),
            provenance=provenance,
            request_traces=requests,
        )
    )
    return prepared.trace.editorial_passes[-1]


def _episode_tiles(pack: EpisodeScanPack) -> dict[int, tuple[int, ...]]:
    return {
        scope.episode_alias: tuple(ref.number for ref in scope.tile_refs) for scope in pack.scopes
    }


def _tile_map(pack: EpisodeScanPack) -> dict[int, str]:
    return {ref.number: ref.entity_id for scope in pack.scopes for ref in scope.tile_refs}


def _bank_matches_pack(pack: EpisodeScanPack, request_trace: RequestTrace) -> bool:
    expected_ids = tuple(ref.entity_id for ref in pack.page.tile_refs)
    return (
        request_trace.provenance.input_ids == expected_ids
        and request_trace.provenance.sheet_hashes
        == request_trace.attached_sheet_hashes
        == (pack.page.sha256,)
    )


def _pass_provenance(
    prepared: PreparedEditorialSource,
    pass_zero: PassZeroResult,
    requests: tuple[RequestTrace, ...],
) -> DecisionProvenance:
    keys = tuple(request.provenance.request_key for request in requests)
    return DecisionProvenance(
        pass_name="pass-1-cull",  # noqa: S106 - public editorial pass identity
        pass_version=PASS_ONE_VERSION,
        schema_version="episode-scan-v3",
        model_identity=requests[0].provenance.model_identity if requests else "",
        input_ids=prepared.candidate_ids,
        sheet_hashes=tuple(pack.page.sha256 for pack in pass_zero.episode_packs),
        request_key=sha256("\x00".join(keys).encode()).hexdigest(),
        cache_hit=bool(requests) and all(request.cache_hit for request in requests),
    )


def _render_review(
    prepared: PreparedEditorialSource,
    pass_zero: PassZeroResult,
    rejects: tuple[CullDecision, ...],
    warnings: tuple[str, ...],
    output_dir: Path,
) -> CullReviewArtifacts:
    reject_by_id = {decision.asset_id: decision for decision in rejects}
    atlas_tiles = tuple(
        pass_zero.atlas.tile_for(candidate.asset_id) for candidate in prepared.candidates
    )
    pages = build_contact_sheets(atlas_tiles, "pass-0-1-review", output_dir)
    page_by_asset = {ref.entity_id: page.sheet_id for page in pages for ref in page.tile_refs}
    number_by_asset = {ref.entity_id: ref.number for page in pages for ref in page.tile_refs}
    entries = tuple(
        CullReviewEntry(
            number=number_by_asset[candidate.asset_id],
            page_id=page_by_asset[candidate.asset_id],
            asset_id=candidate.asset_id,
            taken_at=candidate.taken_at.isoformat(),
            favourite=candidate.favourite,
            status="CULL" if candidate.asset_id in reject_by_id else "KEEP",
            reason=(
                f"{reject_by_id[candidate.asset_id].bucket}: "
                f"{reject_by_id[candidate.asset_id].reason}"
                if candidate.asset_id in reject_by_id
                else None
            ),
            source_tile_sha256=pass_zero.atlas.tile_for(candidate.asset_id).sha256,
        )
        for candidate in prepared.candidates
    )
    pages = tuple(
        _decorate_review_page(
            page,
            tuple(entry for entry in entries if entry.page_id == page.sheet_id),
        )
        for page in pages
    )
    if warnings:
        pages = tuple(_add_warning_banner(page, warnings) for page in pages)
    manifest_path = output_dir / "pass-0-1-review.json"
    manifest_path.write_text(
        json.dumps(
            {
                "warnings": list(warnings),
                "entries": [_review_entry_dict(entry) for entry in entries],
            },
            indent=2,
        )
        + "\n"
    )
    return CullReviewArtifacts(pages, entries, warnings, manifest_path)


def _review_entry_dict(entry: CullReviewEntry) -> dict[str, object]:
    return {
        "number": entry.number,
        "page_id": entry.page_id,
        "asset_id": entry.asset_id,
        "taken_at": entry.taken_at,
        "favourite": entry.favourite,
        "status": entry.status,
        "reason": entry.reason,
        "source_tile_sha256": entry.source_tile_sha256,
    }


def _decorate_review_page(
    page: ContactSheetPage,
    entries: tuple[CullReviewEntry, ...],
) -> ContactSheetPage:
    from PIL import Image, ImageDraw

    with Image.open(io.BytesIO(page.jpeg_bytes)) as decoded:
        sheet = decoded.convert("RGB")
    draw = ImageDraw.Draw(sheet)
    columns, tile = sheet_layout(len(page.tile_refs))
    entry_by_number = {entry.number: entry for entry in entries}
    for position, ref in enumerate(page.tile_refs):
        _draw_review_markers(
            draw,
            left=(position % columns) * tile,
            top=(position // columns) * tile,
            tile=tile,
            entry=entry_by_number[ref.number],
        )
    with_legend = _append_review_legend(sheet, entries)
    return _replace_page_image(page, with_legend)


def _draw_review_markers(
    draw: Any,
    *,
    left: int,
    top: int,
    tile: int,
    entry: CullReviewEntry,
) -> None:
    strip_height = max(18, min(26, tile // 6))
    strip_top = top + tile - strip_height - 3
    draw.rectangle(
        (left + 3, strip_top, left + tile - 3, top + tile - 3),
        fill=_REVIEW_STATE_COLOURS[entry.status],
    )
    draw.text((left + 7, strip_top + 4), entry.status, fill=(255, 255, 255))
    if entry.favourite:
        fav_width = 38
        draw.rectangle(
            (left + tile - fav_width - 3, top + 3, left + tile - 3, top + 21),
            fill=_REVIEW_FAVOURITE_COLOUR,
        )
        draw.text((left + tile - fav_width + 2, top + 6), "FAV", fill=(255, 255, 255))


def _append_review_legend(image: Any, entries: tuple[CullReviewEntry, ...]):
    from PIL import Image, ImageDraw

    decisions = tuple(entry for entry in entries if entry.status != "KEEP")
    lines = tuple(
        f"#{entry.number} {entry.status} {entry.reason}"
        for entry in decisions
        if entry.reason is not None
    ) or ("No RECORD/CULL decisions on this page.",)
    columns = 2
    rows = -(-len(lines) // columns)
    line_height = 14
    footer_height = 34 + rows * line_height
    composed = Image.new(
        "RGB",
        (image.width, image.height + footer_height),
        (25, 25, 25),
    )
    composed.paste(image, (0, 0))
    draw = ImageDraw.Draw(composed)
    draw.text(
        (8, image.height + 7),
        "VISIBLE DECISION LEGEND - number -> function/defect/reason",
        fill=(255, 255, 255),
    )
    column_width = image.width // columns
    for index, line in enumerate(lines):
        column = index % columns
        row = index // columns
        draw.text(
            (8 + column * column_width, image.height + 25 + row * line_height),
            line,
            fill=(225, 225, 225),
        )
    return composed


def _replace_page_image(page: ContactSheetPage, image: Any) -> ContactSheetPage:
    output = io.BytesIO()
    image.save(output, "JPEG", quality=88)
    data = output.getvalue()
    page.path.write_bytes(data)
    return replace(page, jpeg_bytes=data, sha256=sha256(data).hexdigest())


def _add_warning_banner(
    page: ContactSheetPage,
    warnings: tuple[str, ...],
) -> ContactSheetPage:
    from PIL import Image, ImageDraw

    with Image.open(io.BytesIO(page.jpeg_bytes)) as decoded:
        sheet = decoded.convert("RGB")
    banner_height = 28 + 18 * len(warnings)
    warned = Image.new("RGB", (sheet.width, sheet.height + banner_height), (185, 18, 18))
    warned.paste(sheet, (0, banner_height))
    draw = ImageDraw.Draw(warned)
    draw.text((10, 7), "!! INVALID FOR OWNER VERDICT", fill=(255, 255, 255))
    for index, warning in enumerate(warnings):
        draw.text((10, 25 + index * 18), warning, fill=(255, 255, 255))
    output = io.BytesIO()
    warned.save(output, "JPEG", quality=88)
    data = output.getvalue()
    page.path.write_bytes(data)
    return replace(page, jpeg_bytes=data, sha256=sha256(data).hexdigest())


def _warn_once(warnings: list[str], warning: str) -> None:
    if warning not in warnings:
        warnings.append(warning)


def _ordered_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
