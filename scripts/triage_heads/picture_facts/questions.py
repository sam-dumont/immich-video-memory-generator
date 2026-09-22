"""The exact eleven questions the product's picture-facts producer asks.

Copied verbatim from `src/immich_memories/analysis/editorial_preparation_picture_facts.py`
at 1c896ae5 (PR #1140) so the teacher labels this experiment banks are the same answers the
product banks. The hash below is what the producer string carries.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

STATE = "Look at the photo."

_WHAT_CRITERIA = {
    "people_moment": "people in a real moment",
    "place_or_scenery": "a place or scenery worth seeing",
    "meaningful_record": "a result, sign or object whose meaning is clearly visible",
    "screen_or_document": "a screen or a document",
    "lone_everyday_object": "a lone everyday object",
    "empty_room_ceiling_or_floor": "an empty room, a ceiling or a floor",
    "accidental_or_blurred_frame": "an accidental or blurred frame",
    "body_part_closeup": "a close-up of a body part",
}

QUESTIONS: Mapping[str, Mapping[str, Any]] = {
    "screen": {
        "type": "noul",
        "instructions": (
            "This photo is of a screen, a monitor, a TV or a phone display, or is a recording "
            "of one."
        ),
    },
    "overlay_graphics": {
        "type": "noul",
        "instructions": (
            "Graphics, telemetry, maps or text are laid over real footage in this photo."
        ),
    },
    "face_extreme_closeup": {
        "type": "noul",
        "instructions": "This photo is an extreme close-up of a face, filling most of the frame.",
    },
    "bathing_now": {
        "type": "noul",
        "instructions": (
            "Someone in this photo is being bathed, bathing or showering right now, even when "
            "water or clothing covers the body."
        ),
    },
    "breastfeeding_now": {
        "type": "noul",
        "instructions": (
            "Someone in this photo is breastfeeding or expressing milk right now, including "
            "under a cover."
        ),
    },
    "medical_procedure": {
        "type": "noul",
        "instructions": (
            "This photo shows an invasive medical procedure, an operation or a delivery in "
            "progress, or an open wound."
        ),
    },
    "private_record": {
        "type": "noul",
        "instructions": (
            "This photo shows a readable personal record: an identity card, a wristband, a "
            "badge, or a medical or financial document. A brand, a sign, a race bib or a "
            "jersey is not a personal record."
        ),
    },
    "worth": {
        "type": "noul",
        "instructions": (
            "This photo shows something worth showing on its own in a family film: people in a "
            "real moment, a place worth seeing, or a clearly visible meaningful record."
        ),
    },
    "what": {
        "type": "choice",
        "instructions": "What does this photo mainly show?",
        "criteria": _WHAT_CRITERIA,
    },
    "adult_coverage": {
        "type": "choice",
        "instructions": "How covered is the least covered adult in this photo?",
        "criteria": {
            "no_adult": "no adult is visible",
            "clothed": "wearing ordinary clothes",
            "swimwear": "wearing swimwear",
            "bare_torso": "bare-chested or bare-torsoed",
            "underwear_only": "wearing only underwear",
            "nude": "nude",
        },
    },
    "child_coverage": {
        "type": "choice",
        "instructions": "How covered is the least covered child in this photo?",
        "criteria": {
            "no_child": "no child is visible",
            "clothed": "wearing ordinary clothes",
            "swimwear": "wearing swimwear",
            "nappy_or_underwear_only": "wearing only a nappy or underwear",
            "nude": "nude",
        },
    },
}

NOULS = tuple(name for name, question in QUESTIONS.items() if question["type"] == "noul")
CHOICES = tuple(name for name, question in QUESTIONS.items() if question["type"] == "choice")


def question_set_hash(questions: Mapping[str, Mapping[str, Any]]) -> str:
    payload = json.dumps(questions, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
