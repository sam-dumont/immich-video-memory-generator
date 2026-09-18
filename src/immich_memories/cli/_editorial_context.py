"""What one `generate` run is about, in the exact terms the editorial route reads.

The CLI's flags, presets and fetched sources land here and leave as a single
`EditorialRunContext`: the identity the artifacts are filed under, the windows
that may be acquired, and the timing policy the render must honour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from immich_memories.analysis.editorial_runtime import EditorialRunContext
from immich_memories.analysis.special_event_scope import read_special_event_admission
from immich_memories.api.person_expression import PersonExpression
from immich_memories.planning.auto_duration import DURATION_FROM_DURATION_FLAG
from immich_memories.timeperiod import DateRange

if TYPE_CHECKING:
    from immich_memories.cli._run_inputs import ResolvedRunInputs
    from immich_memories.config_loader import Config


def narrow_to_special_event(
    *,
    memory_type: str | None,
    assets: list,
    photo_assets: list | None,
    memory_preset_params: dict | None,
) -> tuple[list, list | None]:
    """Keep only the catalogued members of a special event, components included."""
    if memory_type != "special_day":
        return assets, photo_assets
    from immich_memories.analysis.live_photo_pipeline import drop_live_photo_components
    from immich_memories.analysis.special_event_scope import (
        select_source_members,
        validate_special_event_scope,
    )

    params = memory_preset_params or {}
    members = validate_special_event_scope(params.get("event_id"), params.get("asset_ids", ()))
    if not members:
        return assets, photo_assets
    narrowed = drop_live_photo_components(
        list(select_source_members(assets, members)), photo_assets or []
    )
    if photo_assets is None:
        return narrowed, None
    return narrowed, list(select_source_members(photo_assets, members))


def build_editorial_context(
    *,
    resolved: ResolvedRunInputs,
    config: Config,
    memory_type: str | None,
    memory_key: str | None,
    output_stem: str,
    assets: list,
    date_range: DateRange,
    date_ranges: tuple[DateRange, ...] | list[DateRange] | None,
    duration: float,
    duration_source: str = DURATION_FROM_DURATION_FLAG,
    transition: str,
    title_override: str | None,
    person_names: list[str],
    accept_any_provenance: bool,
    owner_required_asset_ids: tuple[str, ...] = (),
    owner_excluded_asset_ids: tuple[str, ...] = (),
) -> EditorialRunContext:
    """Bind the run's identity, scope and timing before any source is touched."""
    from immich_memories.processing.editorial_timing import build_editorial_timing_policy

    product = str(memory_type or "custom")
    person_expression = _person_expression(resolved)
    label, album_ref, album_sources = _naming(
        product,
        resolved=resolved,
        assets=assets,
        date_range=date_range,
        title_override=title_override,
    )
    special_event = product == "special_day"
    key = _context_key(memory_key or output_stem, person_expression)
    return EditorialRunContext(
        key=key,
        label=label,
        product=product,
        date_ranges=tuple(date_ranges) if date_ranges is not None else (date_range,),
        target_seconds=duration,
        duration_source=duration_source,
        hemisphere=resolved.preset_params.get("hemisphere", "north"),
        render_timing=build_editorial_timing_policy(
            config=config,
            target_seconds=duration,
            memory_type=memory_type,
            date_start=date_range.start,
            date_end=date_range.end,
            person_name=person_names[0] if person_names else None,
            memory_preset_params=resolved.preset_params,
            transition=transition,
            transition_duration=config.defaults.transition_duration,
        ),
        artifact_dir=config.cache.cache_path / "editorial-runs" / key,
        people=tuple(person_names),
        person_match=_person_match(resolved),
        person_expression=person_expression,
        accept_any_provenance=accept_any_provenance,
        owner_required_asset_ids=owner_required_asset_ids,
        owner_excluded_asset_ids=owner_excluded_asset_ids,
        trip=product == "trip",
        album_ref=album_ref,
        album_sources=album_sources,
        special_event_id=resolved.preset_params.get("event_id") if special_event else None,
        event_admission=read_special_event_admission(resolved.preset_params.get("event_admission")),
        event_asset_ids=(
            tuple(resolved.preset_params.get("asset_ids") or ()) if special_event else ()
        ),
    )


def _person_match(resolved: ResolvedRunInputs) -> Literal["and", "or"]:
    return "or" if resolved.preset_params.get("person_match") == "or" else "and"


def _person_expression(resolved: ResolvedRunInputs) -> PersonExpression | None:
    record = resolved.preset_params.get("person_expression")
    return PersonExpression.from_dict(record) if record is not None else None


def _context_key(base: str, person_expression: PersonExpression | None) -> str:
    if person_expression is None:
        return base
    import hashlib
    import json

    digest = hashlib.sha256(
        json.dumps(person_expression.to_dict(), sort_keys=True).encode()
    ).hexdigest()
    return f"{base}-people-{digest[:12]}"


def _naming(
    product: str,
    *,
    resolved: ResolvedRunInputs,
    assets: list,
    date_range: DateRange,
    title_override: str | None,
) -> tuple[str, str | None, tuple]:
    """Album runs carry their captured sources; every other product fetches windows."""
    if product == "album":
        return (
            str(resolved.preset_params.get("album_name") or date_range.description),
            str(resolved.preset_params.get("album_id") or ""),
            (*assets, *(resolved.photo_assets or [])),
        )
    if product == "trip":
        location = resolved.preset_params.get("location_name")
        return str(title_override or location or date_range.description), None, ()
    return title_override or date_range.description, None, ()
