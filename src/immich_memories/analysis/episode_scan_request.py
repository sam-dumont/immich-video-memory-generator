"""Provider-visible request for fused episode reading, record, and Cull namespaces."""

from __future__ import annotations

from typing import TYPE_CHECKING

from immich_memories.analysis.editorial_contracts import (
    EditorialCandidate,
)

if TYPE_CHECKING:
    pass

# v5: the cull question gained the foreign list and named watch faces
# (2026-09-01) — without this bump, cached v4 answers replayed against the
# stricter parser and the cull failed open to zero (caught live on the first
# validation run).
_RENDER_VERSION = "visual-atlas-v1/contact-sheet-v1"


def candidate_who_and_where(candidate: EditorialCandidate) -> tuple[str, ...]:
    """Place and recognised people — free facts this request was not sending.

    `subject-evidence` collapses everyone present into one enum value, so a ride
    with a partner and a ride alone read identically, and the place was absent
    altogether: `selection_review._place_for_llm` had no caller on this path.
    Both are grounded observation, not judgement, and both are omitted rather
    than defaulted when Immich has nothing — an absent place must not read as a
    place, and an unnamed face is evidence someone is there, never of who.
    """
    faces = tuple(candidate.source.people or ())
    named = tuple(dict.fromkeys(person.name for person in faces if person.name))
    place = _place(candidate)
    return tuple(
        annotation
        for annotation in (
            f"place:{place}" if place else "",
            f"faces:{len(faces)}" if faces else "",
            f"people:{', '.join(named)}" if named else "",
        )
        if annotation
    )


def _place(candidate: EditorialCandidate) -> str:
    """City, state and country, because the caption form is too thin to reason from.

    Paradise and Winchester are Las Vegas Strip townships; without the state
    they read as two unrelated villages rather than one trip.
    """
    exif = candidate.source.exif_info
    if exif is None:
        return ""
    named = [part for part in (exif.city, exif.state, exif.country) if part]
    return ", ".join(named)
