"""Reader words for the editor's stage names, shared by the page and the terminal.

The trace and the progress records carry the names the engine uses for its passes
(`audience`, `shareability`, `final_duplicate_review`). A person reading `runs why`
or a progress row should never meet those. One map, one fallback, both surfaces.
"""

from __future__ import annotations

_STAGE_WORDS = {
    "audience": "the family-viewing check",
    "audience-gate": "the family-viewing check",
    "shareability": "the shareability check",
    "final_duplicate_review": "the duplicate review",
    "duplicate_review": "the duplicate review",
    "picture_review": "the picture review",
    "picture-review": "the picture review",
    "owner-required-after-audience": "the owner's tick, after the family-viewing check",
    "owner-required": "the owner's tick",
    "worthy": "the memory-worthy read",
    "standing": "the standing read",
    "period": "the period read",
    "story-weighing": "the story weighing",
    "cull": "the cull",
    "pass-1-cull": "the first cull",
    "source-eligibility": "the source check",
    "editorial final cut": "the final cut",
    "trim": "the timing trim",
    "structure": "the structure pass",
}


def stage_words(stage: str) -> str:
    """The reader's name for an engine stage; unknown names become plain words."""
    known = _STAGE_WORDS.get(stage)
    if known:
        return known
    plain = stage.replace("_", " ").replace("-", " ").strip()
    return f"the {plain} pass" if plain else "an unnamed pass"
