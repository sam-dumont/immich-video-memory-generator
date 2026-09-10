"""What one picture's annotation line says, and the anchor row the memory-worthy gate reads.

Junk culled by facts: an anchor with nothing showable gets no primary. These readings are
text-only — the flag and people tail of a line is stripped before the story reader sees it, so a
picture is judged on what it shows.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

LIVING = re.compile(
    r"\b(man|woman|person|people|child|children|kid|girl|boy|baby|couple|family|friend|friends|group|crowd|cyclist|cyclists|rider|runner|hiker|hikers|walker|player|someone|he|she|they|cat|dog|kitten|kittens|puppy|horse|bird|animal|selfie|portrait|face)\b",
    re.IGNORECASE,
)
DOCUMENT_TEXT = re.compile(
    r"\b(licen[cs]e|registration number|passport|id card|identity card|ticket|receipt|invoice|boarding pass|map of|route from|label that reads|screenshot|qr code|barcode)\b",
    re.IGNORECASE,
)
_SOFT_OR_DARK = re.compile(r"\bSOFT\b|\bDARK\b")
_FACT_PREFIX = re.compile(
    r"^(20\d\d|LIVE|VIDEO|with |at |activity=|setting|resolution|exposure|duration|location=|children=|STARRED|SOFT|DARK)"
)


class UnitLines:
    """The annotation line of a playable unit, and the facts read out of it."""

    def __init__(self, lines: Mapping[str, str]) -> None:
        self._lines = lines

    def label(self, u: dict) -> str:
        media = {
            "live-motion": f"Live Photo burst, {'measured' if u.get('motion_assessed') else 'available'} motion {u['raw_seconds']} s, {len(u['members'])} shots",
            "live-still": "Live Photo shown as a still",
            "video": f"video {u['raw_seconds']} s",
            "still": "photo",
        }[u["kind"]]
        star = "FAVOURITE, " if u.get("favourite") else ""
        return f"[{star}{media}] {self._lines.get(u['asset_id'], '(no annotation line)')}"

    def line(self, u) -> str:
        return self._lines.get(u["asset_id"], "")

    def description(self, u) -> str:
        """The scene prose alone, without the date, media, setting and flag fields."""
        parts = [q.strip() for q in self.line(u).split("|")]
        described = [q for q in parts if not _FACT_PREFIX.match(q)]
        return described[0] if described else ""

    def shows_life(self, u) -> bool:
        return (
            bool(LIVING.search(self.description(u)))
            or u["kind"] in ("live-motion", "video")
            or bool(u.get("favourite"))
        )

    def lone_object(self, u) -> bool:
        return not self.shows_life(u)

    def is_soft_or_dark(self, u) -> bool:
        return bool(_SOFT_OR_DARK.search(self.line(u)))

    def is_document(self, u) -> bool:
        return bool(DOCUMENT_TEXT.search(self.line(u)))


class AnchorRows:
    """The one row per happening that the memory-worthy gate and the story reader are shown."""

    def __init__(
        self,
        *,
        anchor_label: Mapping[str, str],
        families: Mapping[str, list[str]],
        moments: Mapping[str, dict],
        place_names: Mapping[str, str],
        moment_places: Mapping[str, list],
        event_assets: Mapping[str, list[str]],
        assets: Mapping[str, Any],
        observed: Mapping[str, str],
        person_context: Mapping[str, str],
        meaning_of: Mapping[str, str],
    ) -> None:
        self._anchor_label = anchor_label
        self._families = families
        self._moments = moments
        self._place_names = place_names
        self._moment_places = moment_places
        self._event_assets = event_assets
        self._assets = assets
        self._observed = observed
        self._person_context = person_context
        self._meaning_of = meaning_of

    def with_person_context(self, f: str, prose: str) -> str:
        # Scope facts independently, then append after any scene-prose truncation.
        return f"{prose} | {self._person_context[f]}" if self._person_context[f] else prose

    def _distinct_field(self, ms: Sequence[str], field: str, limit: int) -> list[str]:
        return list(
            dict.fromkeys(
                self._moments[m][field] for m in ms if self._moments[m][field] not in ("", "null")
            )
        )[:limit]

    def line(self, f: str, units: Sequence[dict], pictures: int) -> str:
        ms = self._families[f]
        days = sorted({self._moments[m]["taken"][:10] for m in ms})
        span = sum(float(self._moments[m]["span_s"] or 0) for m in ms)
        favs = sum(
            1
            for a in self._event_assets.get(f, [])
            if a in self._assets and self._assets[a].is_favorite
        )
        motion = sum(1 for u in units if u["kind"] in ("live-motion", "video"))
        pls = list(
            dict.fromkeys(
                self._place_names.get(p, p) for m in ms for p in self._moment_places.get(m, [])
            )
        )[:3]
        acts = self._distinct_field(ms, "activity", 3)
        people = self._distinct_field(ms, "people_head", 2)
        return (
            f"{self._anchor_label[f]}: {days[0]}{'..' + days[-1] if days[-1] != days[0] else ''} | pictures={pictures} "
            f"motion={motion} favourites={favs} span={span:.0f}s | places={'; '.join(pls) or '-'} | "
            f"activity={','.join(acts) or '-'} people={','.join(people) or '-'} | {self.with_person_context(f, self._observed[f])} | inferred episode summary: {self._meaning_of[f][:160]}"
        )
