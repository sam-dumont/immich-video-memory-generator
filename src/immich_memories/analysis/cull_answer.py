"""Strict parsing for the two Cull buckets, asked inside each episode's scope.

Cull removes the junk and the failed pictures, and protects the favourites. It
never chooses between similar frames: a real month held runs of eight to
thirty-five near-duplicates, and deciding between those is a later pass's work.

The per-episode shape is not presentation. Probed on one real 57-tile pack,
three repeats each: a flat answer over the whole pack parsed once in three and
gave fifty-five of fifty-seven tiles the same label, while the same question
asked inside each episode's own scope parsed three times in three and returned
identical tiles every run. A small model cannot search a large flat set; it
answers reliably inside a named small scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Two buckets, because a vocabulary of named defects was measured collapsing:
# shown one populated example the model copied its defect onto seven unrelated
# visuals, and asked to choose among four families it produced a pair that was
# not even legal. The bucket carries the reason; the model only sorts.
CULL_BUCKET_REASONS = {
    "notes": "taken as a note rather than as a moment",
    "failed": "the picture did not come out",
    # Files saved INTO the library, never taken by or of this life: stock
    # imagery (real camera exif, high resolution — provenance gates pass it),
    # archival/instructional footage (rides the pre-smartphone low-res
    # bypass). Both archetypes shipped on graded 2007 walls three times before
    # this bucket existed (owner, 2026-09-01: "this should be culled at the
    # beginning").
    "foreign": "saved from elsewhere, not a picture of this life",
}
CULL_BUCKETS = tuple(CULL_BUCKET_REASONS)
# Read, validated, and thrown away. Without somewhere to put "merely
# unremarkable" the model puts it in notes: measured at temperature 0 on one
# real pack, notes held 19 tiles of which nine were fields and cycling paths.
# Given this third list it held four -- a photographed document and three shots
# of a television -- and nothing else. Choosing between similar frames is a
# later pass's work, so what lands here is not Cull's to act on.
_RESPONSE_PLANNING_CHARS_PER_TOKEN = 3


@dataclass(frozen=True)
class CullDecision:
    """One visual Cull removed, and which of its two questions removed it."""

    asset_id: str
    bucket: str
    reason: str = field(init=False)

    def __post_init__(self) -> None:
        reason = CULL_BUCKET_REASONS.get(self.bucket)
        if not self.asset_id.strip() or reason is None:
            raise ValueError("Cull decision needs a stable asset and a known bucket")
        object.__setattr__(self, "reason", reason)
