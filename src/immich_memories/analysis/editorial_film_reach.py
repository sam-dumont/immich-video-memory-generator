"""The pictures a film can reach, which are the only ones it prepares (#1181).

A film selects from the pictures it was asked about: a person film from the pictures where
the person is recognised. The window around them is still read, as Immich metadata, because
episodes and moments are cut from the whole library; but preparing it (previews, heads, the
exposure detector over video frames and Live Photo clips, face boxes) bought the film
nothing. Over a lifetime window it was 90k pictures for a 7.5k-picture pool.

Two things around a demanded picture still decide what it may do, so they are prepared too:
the other stills of its Live Photo rendering family, which play in the same shot, and the
capture run it sits in, whose flags decide whether the exposure rule holds it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.editorial_exposure_chains import runs_holding


def film_reach(
    candidates: Sequence[EditorialCandidate], demanded_ids: Iterable[str]
) -> frozenset[str]:
    """The admitted pictures a film over ``demanded_ids`` can select or must read facts of."""
    by_id = {candidate.asset_id: candidate for candidate in candidates}
    demanded = {asset_id for asset_id in demanded_ids if asset_id in by_id}
    families = {
        member
        for asset_id in demanded
        for member in by_id[asset_id].live_photo_stitch_member_ids
        if member in by_id
    }
    return frozenset(
        runs_holding(
            ((candidate.asset_id, candidate.taken_at) for candidate in candidates),
            demanded | families,
        )
    )
