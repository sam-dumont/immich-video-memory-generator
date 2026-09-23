"""Known screen and document sources remain evidence, never scene carriers."""

from __future__ import annotations

import re
from collections.abc import Mapping

from immich_memories.analysis.editorial_preparation_picture_facts import picture_facts_on

# The reader is certain or it says nothing: the probe measured a spread of up to 0.174
# between reads of one picture, so a bar near the middle would be a coin toss.
SCREEN_CERTAIN = 0.9

SCREEN_DOCUMENT_LABELS = frozenset(
    {
        "screenshot_from_computer",
        "screenshot_from_manual",
        "geographical_map",
        "table",
        "line_chart",
        "bar_chart",
        "scatter_plot",
        "flow_chart",
        "qr_code",
        "bar_code",
        "calendar",
        "page_thumbnail",
        "full_page_image",
        "logo",
        "signature",
        "engineering_drawing",
    }
)
_DOCUMENT_FIELD = re.compile(r"(?:^|[|,])\s*document=([a-z_]+)(?=\s*(?:[,|]|$))")
SCREEN_HEAD_YES = "yes"
_SCREEN_HEAD_FIELD = re.compile(rf"(?:^|[|,])\s*screen={SCREEN_HEAD_YES}(?=\s*(?:[,|]|$))")


def screen_flagged(heads: Mapping[str, str]) -> bool:
    """The distilled screen head says this frame is one. It only adds to the document head.

    It ships at a band strict enough that over 3,564 photographs it answered yes on 37 and
    every one of them was a screen, so reading it beside `doc_docling` buys nine more
    screens for no extra false refusal. A bank with no `screen` row reads as it always did.
    """
    return heads.get("screen") == SCREEN_HEAD_YES


def screen_flagged_on_line(line: str) -> bool:
    """The same head, read off a rendered line instead of a head mapping."""
    return bool(_SCREEN_HEAD_FIELD.search(line))


_SCREEN_TEXT = re.compile(
    r"\b(screenshot|screen (displaying|showing)|phone screen|computer screen|"
    r"monitor displaying|app interface|tv screen|laptop screen|projector screen)\b",
    re.IGNORECASE,
)


# Phone screen pixel sizes (portrait). A photo never has exactly these dimensions; a screenshot always does.
PHONE_SCREEN_SIZES = frozenset(
    {
        (640, 1136),
        (750, 1334),
        (1080, 1920),
        (1125, 2436),
        (1170, 2532),
        (1179, 2556),
        (1206, 2622),
        (1242, 2208),
        (1242, 2688),
        (1284, 2778),
        (1290, 2796),
        (1320, 2868),
        (828, 1792),
        (1080, 2340),
        (1080, 2400),
        (1440, 3120),
        (1440, 3200),
        (1344, 2992),
    }
)
_RESOLUTION_FIELD = re.compile(r"resolution:(\d{3,4})x(\d{3,4})")


_FACE_CLOSE_UP = re.compile(
    r"(composition|subject_action):[^|]*?\bclose-?up\b[^|]*?\b(face|eye|eyes|nose|forehead|mouth|lips|teeth|skin|chin)\b",
    re.IGNORECASE,
)


_MEDICAL_CARE = re.compile(
    r"\b(eye mask|gel mask|ice pack|cold pack|cold compress|compress on|bandage|band-aid|plaster on|thermometer|"
    r"iv drip|iv line|swollen|rash|infection|nasal cannula|stitches)\b",
    re.IGNORECASE,
)
_IDENTICAL_GRID = re.compile(
    r"(identical|same) (images|photos|pictures|portraits)[^|]{0,60}\b(grid|panel|panels|sheet)\b|"
    r"\b(passport photos?|id photos?|photo booth strip|photo-booth)\b",
    re.IGNORECASE,
)


def medical_care(line: str) -> bool:
    """An ailment or a care item on a person: intimate care, never a carrier."""
    return bool(_MEDICAL_CARE.search(line))


def identical_grid(line: str) -> bool:
    """A sheet of identical portraits is an identification document, whatever the detector called it."""
    return bool(_IDENTICAL_GRID.search(line))


def face_close_up(line: str) -> bool:
    """The detector's composition fact says close-up of a face part: a picture that needs an
    explanation, never a carrier (it stays evidence)."""
    return bool(_FACE_CLOSE_UP.search(line))


def screenshot_by_resolution(line: str) -> bool:
    """A STILL whose pixel size is a phone screen size. A phone records portrait video at exactly
    these sizes, and a Live Photo renders from its still, so neither is ever a screenshot."""
    if "VIDEO" in line or "LIVE PHOTO" in line:
        return False
    match = _RESOLUTION_FIELD.search(line)
    if not match:
        return False
    w, h = int(match.group(1)), int(match.group(2))
    return (min(w, h), max(w, h)) in PHONE_SCREEN_SIZES


def read_as_a_screen(line: str) -> bool:
    """The optional picture reader says this is a screen. Silent without its row.

    This is the arm that catches a TV or a watch face, which no head answers for: the
    document head calls every one of them a photograph.
    """
    facts = picture_facts_on(line)
    screen = facts.get("screen")
    return (isinstance(screen, float) and screen >= SCREEN_CERTAIN) or facts.get(
        "what"
    ) == "screen_or_document"


def excluded_carrier_sources(annotations: Mapping[str, str]) -> dict[str, str]:
    """Use grounded annotation fields, without reclassifying the event's importance.

    A map mentioned in a real scene is not the same as a geographical-map document
    label. No date, filename, person or sporting-event name is part of this rule.
    """
    excluded = {}
    for asset_id, line in annotations.items():
        document = _DOCUMENT_FIELD.search(line)
        if read_as_a_screen(line):
            excluded[asset_id] = "picture-facts:screen"
        elif document and document.group(1) in SCREEN_DOCUMENT_LABELS:
            excluded[asset_id] = f"document-head:{document.group(1)}"
        elif screen_flagged_on_line(line):
            excluded[asset_id] = "screen-head"
        elif _SCREEN_TEXT.search(line):
            excluded[asset_id] = "screen-description"
        elif screenshot_by_resolution(line):
            excluded[asset_id] = "screenshot-resolution"
        elif face_close_up(line):
            excluded[asset_id] = "face-close-up"
        elif medical_care(line):
            excluded[asset_id] = "medical-care"
        elif identical_grid(line):
            excluded[asset_id] = "identical-grid"
    return excluded
