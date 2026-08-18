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
) -> float | None:
    """Enhance photo score with LLM visual analysis.

    Sends the photo to the configured VLM (same as video content analysis)
    and gets an interest + quality rating. Blends with metadata score.
    """
    if app_config is None or not app_config.content_analysis.enabled:
        return None

    llm_score = _query_photo_llm(photo_path, app_config, provider_circuit=provider_circuit)
    if llm_score is None:
        return None

    # Blend: replace the LLM placeholder weight with actual LLM score
    # metadata_score was computed with _W_LLM * 0.5 as placeholder
    penalty = 1.0 - config.score_penalty
    # Remove placeholder, add actual LLM score
    adjusted = (metadata_score / penalty) - _W_LLM * 0.5 + _W_LLM * llm_score
    return min(1.0, max(0.0, adjusted)) * penalty


_PHOTO_ANALYSIS_PROMPT = """Analyze this photo for a memory video compilation. Rate on two scales (0.0-1.0):

1. **interest**: How interesting/memorable is this photo? (action, emotion, rare moment > static/mundane)
2. **quality**: Technical quality (composition, focus, lighting, not blurry/dark)

Respond as JSON: {"interest": 0.X, "quality": 0.X, "emotion": "word"}"""


def _photo_score_value(value: object) -> float | None:
    """Normalize one strict 0–1 JSON score without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        return None
    return score


def _parse_photo_score(text: str) -> float | None:
    """Extract complete interest and quality scores from a VLM response."""
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
    return (interest + quality) / 2


def _query_photo_llm(photo_path: Path, config: object, provider_circuit=None) -> float | None:
    """Send photo to VLM and get a 0-1 score."""
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
        return _parse_photo_score(text)

    except httpx.HTTPError as e:
        if provider_circuit is not None:
            provider_circuit.disable("content-analysis provider is unreachable")
        logger.debug(f"LLM photo analysis failed: {e}")
        return None
    except (RuntimeError, ValueError, OSError, KeyError, IndexError, TypeError) as e:
        logger.debug(f"LLM photo analysis returned an invalid response: {e}")
        return None
