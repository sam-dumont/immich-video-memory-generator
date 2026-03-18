"""Photo scoring — ranks photos for inclusion in memory videos.

Two scoring modes:
1. Fast (metadata only): favorites, faces, camera — no I/O, instant
2. LLM (visual analysis): sends the photo to VLM for interest/quality rating

Photos score lower than videos by default (via score_penalty) to ensure
videos always win in a tie.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from immich_memories.api.models import Asset
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
) -> float:
    """Enhance photo score with LLM visual analysis.

    Sends the photo to the configured VLM (same as video content analysis)
    and gets an interest + quality rating. Blends with metadata score.
    """
    from immich_memories.config_loader import get_config

    app_config = get_config()
    if not app_config.content_analysis.enabled:
        return metadata_score

    llm_score = _query_photo_llm(photo_path, app_config)
    if llm_score is None:
        return metadata_score

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


def _query_photo_llm(photo_path: Path, config: object) -> float | None:
    """Send photo to VLM and get a 0-1 score."""
    try:
        import httpx

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
            timeout=llm_config.timeout_seconds,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]

        # Parse JSON response
        import json
        import re

        match = re.search(r"\{[^}]+\}", text)
        if match:
            data = json.loads(match.group())
            interest = float(data.get("interest", 0.5))
            quality = float(data.get("quality", 0.5))
            return (interest + quality) / 2

        logger.debug(f"LLM photo analysis: no JSON in response: {text[:100]}")
        return None

    except Exception as e:
        logger.debug(f"LLM photo analysis failed: {e}")
        return None
