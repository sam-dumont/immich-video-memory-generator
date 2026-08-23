"""Photo scoring — ranks photos for inclusion in memory videos.

Two scoring modes:
1. Fast (metadata only): favorites, faces, camera — no I/O, instant
2. LLM (visual analysis): sends the photo to VLM for interest/quality rating

Photos score lower than videos by default (via score_penalty) to ensure
videos always win in a tie.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

from immich_memories.api.models import Asset
from immich_memories.config import Config
from immich_memories.config_models import PhotoConfig

logger = logging.getLogger(__name__)

# Weight distribution for metadata scoring
_W_FAVORITE = 0.25
_W_FACES = 0.15
_W_FACE_COUNT = 0.10  # More faces = more interesting
_W_CAMERA = 0.05
_W_LLM = 0.30  # LLM interest/quality score (when available)
_W_BASE = 0.15


def score_photo(asset: Asset, config: PhotoConfig) -> float:
    """Score a photo for selection priority. Returns 0.0-1.0."""
    raw = _W_BASE

    if asset.is_favorite:
        raw += _W_FAVORITE

    if asset.people:
        raw += _W_FACES
        # More faces = more interesting (family photos > solo)
        face_count = len(asset.people)
        raw += min(_W_FACE_COUNT, _W_FACE_COUNT * face_count / 3)

    if asset.exif_info and asset.exif_info.make:
        raw += _W_CAMERA

    # Without LLM, redistribute that weight to base
    raw += _W_LLM * 0.5  # Assume average LLM score when not available

    raw = min(1.0, max(0.0, raw))
    return raw * (1.0 - config.score_penalty)


def score_photo_with_llm(
    photo_path: Path,
    metadata_score: float,
    config: PhotoConfig,
    app_config: Config,
    provider_circuit=None,
) -> PhotoLook | None:
    """Enhance a photo's score with what the VLM sees, and keep what it says.

    Sends the photo to the configured VLM (the same one video content analysis
    uses) and blends its interest and quality into the metadata score. The
    words come back too: a photograph that cannot describe itself is a
    photograph the holistic review cannot judge.
    """
    if app_config is None or not app_config.content_analysis.enabled:
        return None

    look = _query_photo_llm(photo_path, app_config, provider_circuit=provider_circuit)
    if look is None:
        return None

    # Blend: replace the LLM placeholder weight with actual LLM score
    # metadata_score was computed with _W_LLM * 0.5 as placeholder
    penalty = 1.0 - config.score_penalty
    # Remove placeholder, add actual LLM score
    adjusted = (metadata_score / penalty) - _W_LLM * 0.5 + _W_LLM * look.score
    blended = min(1.0, max(0.0, adjusted)) * penalty
    return PhotoLook(score=blended, payload=look.payload)


# The same fields the video path asks the same model for. A photo used to be
# asked only for two numbers, which were averaged into one — so the holistic
# review, which reads a clip's description, was handed a bare line for every
# photograph and told never to drop a clip for missing information.
_PHOTO_ANALYSIS_PROMPT = """Look at this photo for a memory video, and say what is in it.

- description: What is happening here?
- category: What is it mainly of? Exactly one of: people, animal, landscape, object, screen.
  Use "screen" for a phone, watch, computer or TV display, a screenshot, or a document
  or a form -- even when a person is holding the device.
- subjects: What is in frame? (short lowercase nouns, e.g. ["child", "dog", "beach"])
- emotion: The mood, in one word.
- interest: How memorable is this moment, 0.0-1.0? Action, emotion and a rare moment
  outrank the static and the everyday.
- quality: Technical quality, 0.0-1.0 -- composition, focus, lighting, not blurry or dark.

Respond as JSON only:
{"description": "...", "category": "people", "subjects": ["..."], "emotion": "...",
 "interest": 0.X, "quality": 0.X}"""


def _photo_score_value(value: object) -> float | None:
    """Normalize one strict 0–1 JSON score without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        return None
    return score


@dataclass(frozen=True)
class PhotoLook:
    """One look at a photograph: the score, and what the model saw in it.

    payload carries the fields cache_projection.apply_semantic_payload puts on
    a clip, so a photo reaches the holistic review saying the same kinds of
    things a video does.
    """

    score: float
    payload: dict[str, object]


def _text_field(value: object) -> str | None:
    """One short string from the model, or nothing if it did not answer."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:400] or None


def _parse_photo_look(text: str) -> PhotoLook | None:
    """Extract the scores a photo must have, and whatever else it came with.

    interest and quality stay required — they are what selection ranks on, and
    a response without them is not an answer. Everything else is optional: a
    small model that returns the two numbers alone still scores exactly as it
    used to, it just tells the review nothing.
    """
    match = re.search(r"\{[^}]+\}", text)
    if not match:
        logger.debug("LLM photo analysis: no JSON in response: %s", text[:100])
        return None

    data = json.loads(match.group())
    if not isinstance(data, dict) or not {"interest", "quality"} <= data.keys():
        logger.debug("LLM photo analysis: response omitted required score fields")
        return None

    interest = _photo_score_value(data["interest"])
    quality = _photo_score_value(data["quality"])
    if interest is None or quality is None:
        logger.debug("LLM photo analysis: score fields were invalid")
        return None

    subjects = data.get("subjects")
    return PhotoLook(
        score=(interest + quality) / 2,
        payload={
            "description": _text_field(data.get("description")),
            "category": _text_field(data.get("category")),
            "emotion": _text_field(data.get("emotion")),
            "subjects": [s for s in subjects if isinstance(s, str)]
            if isinstance(subjects, list)
            else None,
            "setting": None,
            "interestingness": interest,
            "quality": quality,
        },
    )


def _query_photo_llm(photo_path: Path, config: object, provider_circuit=None) -> PhotoLook | None:
    """Send the photo to the VLM and keep everything it says about it."""
    if provider_circuit is not None and not provider_circuit.available:
        return None

    try:
        import httpx

        from immich_memories.analysis.llm_query import build_llm_timeout
        from immich_memories.analysis.provider_health import classify_openai_response

        llm_config = config.llm  # type: ignore[attr-defined]
        ca_config = config.content_analysis  # type: ignore[attr-defined]

        with photo_path.open("rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        content: list[dict] = [
            {"type": "text", "text": _PHOTO_ANALYSIS_PROMPT},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}",
                    "detail": ca_config.openai_image_detail,
                },
            },
        ]

        payload = {
            "model": llm_config.model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 256,
        }

        headers = {}
        if llm_config.api_key:
            headers["Authorization"] = f"Bearer {llm_config.api_key}"

        resp = httpx.post(
            f"{llm_config.base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=build_llm_timeout(float(llm_config.timeout_seconds)),
        )
        health = classify_openai_response(resp.status_code, resp.json(), llm_config.model)
        if not health.available:
            if provider_circuit is not None and provider_circuit.set_health(health):
                logger.warning("Photo content analysis disabled for this run: %s", health.message)
            return None
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        return _parse_photo_look(text)

    except httpx.HTTPError as e:
        if provider_circuit is not None:
            provider_circuit.disable("content-analysis provider is unreachable")
        logger.debug(f"LLM photo analysis failed: {e}")
        return None
    except (RuntimeError, ValueError, OSError, KeyError, IndexError, TypeError) as e:
        logger.debug(f"LLM photo analysis returned an invalid response: {e}")
        return None
