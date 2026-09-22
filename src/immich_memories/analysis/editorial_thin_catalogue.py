"""What the thin model layer is allowed to read about a period before it looks at a film.

Cataloguing produces an account of the period and a neutral list of its stories; a film reads
them and decides nothing on its own behalf here. The protocol states exactly that much, so the
layer can be written against it before the cataloguing modules land: today the account comes
from the library overview table when the library has been catalogued, the stories and their
hints from the rules story reading of the same run. Nothing in here is a weight, a priority or an
answer about a picture.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

TIERS = ("remarkable", "maybe", "background")


@dataclass(frozen=True)
class ThinStory:
    """One story of the period as cataloguing left it: no weight, no place in any film."""

    key: str
    title: str
    purpose: str
    episodes: tuple[str, ...]
    tier: str
    first_day: str
    asset_ids: tuple[str, ...]


@runtime_checkable
class ThinCatalogue(Protocol):
    """The catalogued period the layer reads: an account, its stories, and their hints."""

    @property
    def thesis(self) -> str: ...

    @property
    def stories(self) -> Sequence[ThinStory]: ...

    @property
    def hints(self) -> Mapping[str, Mapping[str, Any]]: ...

    def notable_record_of(self, asset_id: str) -> str: ...

    def records_in(self, story: ThinStory) -> int: ...


@dataclass(frozen=True)
class BankedCatalogue:
    """A catalogue assembled from what a bank holds about the period."""

    thesis: str
    stories: tuple[ThinStory, ...]
    hints: Mapping[str, Mapping[str, Any]]
    records: Mapping[str, str] = field(default_factory=dict)

    def notable_record_of(self, asset_id: str) -> str:
        """What the catalogue records this picture is a record of, if anything."""
        return self.records.get(asset_id, "")

    def records_in(self, story: ThinStory) -> int:
        """How many of a story's pictures the catalogue records something about."""
        return sum(1 for asset in story.asset_ids if self.records.get(asset))


def banked_catalogue(
    *,
    account: str,
    story_rows: Sequence[Mapping[str, Any]],
    hints: Mapping[str, Mapping[str, Any]],
    asset_ids_of: Mapping[str, Sequence[str]],
    records: Mapping[str, str] | None = None,
) -> BankedCatalogue | None:
    """The catalogue of one period, or None when the library holds no account of it.

    None is the fallback signal: a film with no catalogued period plans the way it always has.
    A story the period holds no pictures of is left out, because a story with nothing to show
    can neither be weighed nor spoken for.
    """
    if not account.strip():
        return None
    stories = tuple(
        ThinStory(
            key=str(row["key"]),
            title=str(row.get("title") or ""),
            purpose=str(row.get("purpose") or ""),
            episodes=tuple(str(key) for key in row.get("episodes") or ()),
            tier=_tier(row.get("gate")),
            first_day=str(row.get("first_day") or ""),
            asset_ids=tuple(asset_ids_of.get(str(row["key"])) or ()),
        )
        for row in story_rows
        if asset_ids_of.get(str(row["key"]))
    )
    if not stories:
        return None
    return BankedCatalogue(
        thesis=account.strip(),
        stories=stories,
        hints=dict(hints),
        records=dict(records or {}),
    )


def _tier(value: object) -> str:
    word = str(value or "")
    return word if word in TIERS else "background"
