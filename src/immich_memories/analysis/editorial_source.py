"""Acquire canonical photo and video context for exact editorial windows."""

from __future__ import annotations

from itertools import chain
from typing import Protocol

from immich_memories.analysis.selection_source import SourceScope
from immich_memories.analysis.special_event_scope import select_source_members
from immich_memories.api.models import Asset, VideoClipInfo
from immich_memories.api.person_expression import PersonExpression
from immich_memories.api.person_scope import (
    PhotoSource,
    VideoSource,
    photos_in_window,
    videos_in_window,
)
from immich_memories.timeperiod import DateRange


class FullEditorialSource(VideoSource, PhotoSource, Protocol):
    """Immich reads needed for unfiltered canonical context."""


def fetch_full_window_source(
    client: FullEditorialSource,
    scope: SourceScope,
) -> tuple[Asset | VideoClipInfo, ...]:
    """Fetch both media kinds once per exact window, without person filtering."""
    by_id: dict[str, Asset | VideoClipInfo] = {}
    for window in _exact_windows(scope, "full"):
        _collect(by_id, videos_in_window(client, [], window), photos_in_window(client, [], window))
    return _scoped_members(by_id, scope)


def _exact_windows(scope: SourceScope, kind: str) -> tuple[DateRange, ...]:
    """The scope's own windows, or the one continuous range it states instead."""
    if scope.date_ranges:
        return scope.date_ranges
    if scope.start_at is None or scope.end_at is None:
        raise ValueError(f"{kind} editorial source needs exact date windows")
    return (DateRange(scope.start_at, scope.end_at),)


def _collect(by_id: dict[str, Asset | VideoClipInfo], *batches) -> None:
    """Keep one source per asset, preferring the clip representation."""
    for source in chain.from_iterable(batches):
        key = _asset(source).id
        if key not in by_id or isinstance(source, VideoClipInfo):
            by_id[key] = source


def _scoped_members(
    by_id: dict[str, Asset | VideoClipInfo], scope: SourceScope
) -> tuple[Asset | VideoClipInfo, ...]:
    return select_source_members(
        sorted(
            by_id.values(), key=lambda source: (_asset(source).file_created_at, _asset(source).id)
        ),
        scope.asset_ids,
    )


def _asset(source: Asset | VideoClipInfo) -> Asset:
    return source.asset if isinstance(source, VideoClipInfo) else source


def resolve_named_expression(expression: PersonExpression, people) -> PersonExpression:
    """One named person can own multiple face IDs; those IDs are alternatives."""
    by_name: dict[str, list[str]] = {}
    for person in people:
        by_name.setdefault(person.name, []).append(person.id)

    def resolve(name):
        ids = list(dict.fromkeys(by_name.get(name, ())))
        if not ids:
            raise ValueError(f"named person has no matching library identity: {name}")
        leaves = tuple(PersonExpression("person", value=key) for key in ids)
        return leaves[0] if len(leaves) == 1 else PersonExpression("any", children=leaves)

    return expression.map_leaves(resolve)


def filter_named_expression(sources, expression: PersonExpression | None):
    """Apply same-asset co-occurrence; surrounding context remains separate."""
    sources = tuple(sources)
    if expression is None:
        return sources
    by_name: dict[str, set[str]] = {}
    for source in sources:
        asset = _asset(source)
        for person in asset.people:
            by_name.setdefault(person.name, set()).add(asset.id)
    selected = expression.evaluate(lambda name: by_name.get(name, ()))
    return tuple(source for source in sources if _asset(source).id in selected)
