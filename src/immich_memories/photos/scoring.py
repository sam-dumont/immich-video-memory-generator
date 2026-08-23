"""Photo scoring — ranks photos for inclusion in memory videos.

Two scoring modes:
1. Fast (metadata only): favorites, faces, camera — no I/O, instant
2. LLM (visual analysis): sends the photo to VLM for interest/quality rating

Photos score lower than videos by default (via score_penalty) to ensure
videos always win in a tie.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from immich_memories.api.immich import ImmichAPIError
from immich_memories.api.models import Asset
from immich_memories.config import Config
from immich_memories.config_models_render import PhotoConfig

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
# Room for a description and five short fields. The old cap of 256 was set
# when the answer was two numbers, and a model that opens with a sentence
# before its JSON runs out of tokens mid-answer.
_PHOTO_ANSWER_TOKENS = 500

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
    """Ask the configured model about a photo, through the one client.

    This used to be a second HTTP client with its own rules: it POSTed
    OpenAI-style whatever provider was configured, so every Ollama server
    answered 404; it skipped the rstrip the shared client does; and it gave up
    the first time a model replied with nothing, where every other caller
    retries. query_llm owns provider routing, the URL and that retry, and this
    keeps only what is genuinely its own — the image detail the content
    analysis config asks for, and the circuit that turns photo analysis off
    for a run when the provider is unwell.
    """
    if provider_circuit is not None and not provider_circuit.available:
        return None

    try:
        import httpx

        from immich_memories.analysis.llm_query import query_llm

        llm_config = config.llm  # type: ignore[attr-defined]
        ca_config = config.content_analysis  # type: ignore[attr-defined]

        text = asyncio.run(
            query_llm(
                _PHOTO_ANALYSIS_PROMPT,
                llm_config,
                temperature=0.1,
                max_tokens=_PHOTO_ANSWER_TOKENS,
                timeout_seconds=llm_config.timeout_seconds,
                images=[photo_path.read_bytes()],
                image_detail=ca_config.openai_image_detail,
            )
        )
        return _parse_photo_look(text)

    except httpx.HTTPStatusError as e:
        _note_provider_health(e.response, config, provider_circuit)
        return None
    except httpx.HTTPError as e:
        if provider_circuit is not None:
            provider_circuit.disable("content-analysis provider is unreachable")
        logger.debug(f"LLM photo analysis failed: {e}")
        return None
    except (RuntimeError, ValueError, OSError, KeyError, IndexError, TypeError) as e:
        logger.debug(f"LLM photo analysis returned an invalid response: {e}")
        return None


def _note_provider_health(response: object, config: object, provider_circuit) -> None:
    """Read a rejected response for what it says about the provider."""
    from immich_memories.analysis.provider_health import classify_openai_response

    llm_config = config.llm  # type: ignore[attr-defined]
    try:
        body = response.json()  # type: ignore[attr-defined]
    except ValueError:
        body = ""
    health = classify_openai_response(
        response.status_code,  # type: ignore[attr-defined]
        body,
        llm_config.model,
    )
    if (
        not health.available
        and provider_circuit is not None
        and provider_circuit.set_health(health)
    ):
        logger.warning("Photo content analysis disabled for this run: %s", health.message)


# A cached row is only as good as everything that produced it: the model, the
# prompt it answered, and the formula that blended the answer into a score.
# The key carries all three, because a row that predates any of them is not a
# cheaper version of today's answer, it is a different one. Warm-cache photos
# ranked on the pre-#489 formula while cache-miss photos beside them ranked on
# the new one — two scoring regimes inside a single selection.
#
# Bump this whenever the prompt or the scoring weights change. It invalidates
# the affected rows exactly once.
# What a row can answer depends on the prompt as much as the model, so the key
# carries both: rows written when a photo could only report two numbers cannot
# describe themselves, and bumping this invalidates them once.
_PHOTO_LOOK_VERSION = "look2"


def _photo_look_version(model: str) -> str:
    """The cache key for a photo look: which model, answering which prompt."""
    return f"{model}#{_PHOTO_LOOK_VERSION}"


def semantic_payloads_for(
    db_path: Path | None,
    asset_ids: list[str],
    model_version: str | None,
) -> dict[str, dict]:
    """What the VLM said about these photos, keyed by asset id.

    Read from the score cache rather than threaded back through scoring: the
    row is written as each photo is scored, so this answers for the ones just
    looked at and for the ones a previous run paid for. Callers hand the
    result to cache_projection.apply_semantic_payload.
    """
    if not db_path or not asset_ids:
        return {}
    cache = _get_score_cache(db_path)
    if cache is None:
        return {}
    rows = _cached_scores(cache, asset_ids, _photo_look_version(model_version or ""))
    return {asset_id: _payload_from_cache(row) for asset_id, row in rows.items()}


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _as_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _payload_from_cache(row: dict) -> dict:
    """What a cached row can tell the review, in the shape a clip expects."""
    return {
        "description": row.get("llm_description"),
        "category": None,
        "emotion": row.get("llm_emotion"),
        "subjects": None,
        "setting": None,
        "interestingness": row.get("llm_interest"),
        "quality": row.get("llm_quality"),
    }


def _enhance_with_llm(
    scored: list[tuple[Asset, float]],
    config: PhotoConfig,
    work_dir: Path,
    download_fn: Any,
    db_path: Path | None = None,
    app_config: Any = None,
    thumbnail_fn: Any = None,
    provider_circuit: Any = None,
) -> tuple[list[tuple[Asset, float]], dict[str, dict]]:
    """Check cache first, then LLM-score uncached photos.

    Returns the scores and, beside them, what the model said about each photo
    — keyed by asset id, in the shape apply_semantic_payload expects. A photo
    that cannot describe itself is one the holistic review cannot judge.
    """

    if app_config is None or not app_config.content_analysis.enabled or not app_config.llm.model:
        return scored, {}

    cache = _get_score_cache(db_path) if db_path else None
    asset_ids = [a.id for a, _ in scored]
    model_version = _photo_look_version(app_config.llm.model)
    cached = _cached_scores(cache, asset_ids, model_version)

    cache_hits = 0
    enhanced: list[tuple[Asset, float]] = []
    payloads: dict[str, dict] = {}
    for asset, meta_score in scored:
        # Cache hit — use stored score, and the words stored with it
        if asset.id in cached:
            row = cached[asset.id]
            enhanced.append((asset, row["combined_score"]))
            payloads[asset.id] = _payload_from_cache(row)
            cache_hits += 1
            continue

        # Cache miss — download + LLM
        look = _llm_score_photo(
            asset,
            meta_score,
            config,
            work_dir,
            download_fn,
            app_config,
            thumbnail_fn=thumbnail_fn,
            provider_circuit=provider_circuit,
        )
        effective_score = look.score if look is not None else meta_score
        enhanced.append((asset, effective_score))
        if look is not None:
            payloads[asset.id] = look.payload

        # Only successful semantic results belong to the configured model.
        if cache and look is not None and model_version:
            cache.save_asset_score(
                asset_id=asset.id,
                asset_type="photo",
                metadata_score=meta_score,
                combined_score=effective_score,
                llm_interest=_as_float(look.payload.get("interestingness")),
                llm_quality=_as_float(look.payload.get("quality")),
                llm_emotion=_as_text(look.payload.get("emotion")),
                llm_description=_as_text(look.payload.get("description")),
                model_version=model_version,
            )

    if cache_hits:
        logger.info(f"Photo score cache: {cache_hits} hits, {len(scored) - cache_hits} misses")

    return enhanced, payloads


def _llm_score_photo(
    asset: Asset,
    meta_score: float,
    config: PhotoConfig,
    work_dir: Path,
    download_fn: Any,
    app_config: Any,
    thumbnail_fn: Any = None,
    provider_circuit: Any = None,
) -> PhotoLook | None:
    """Look at a photo with the VLM, using a lightweight thumbnail.

    Uses Immich thumbnail API (~100 KB) instead of downloading the full
    HEIC (5-15 MB). Falls back to full download if no thumbnail_fn.
    """
    from immich_memories.photos.scoring import score_photo_with_llm

    if provider_circuit is not None and not provider_circuit.available:
        return None

    thumb_path = work_dir / f"{asset.id}_thumb.jpg"

    # WHY: Thumbnails are ~100 KB vs 5-15 MB for full HEICs. The VLM
    # doesn't need HDR gain maps or 4K resolution to score a photo.
    if thumbnail_fn and not thumb_path.exists():
        try:
            thumb_bytes = thumbnail_fn(asset.id, size="preview")
            thumb_path.write_bytes(thumb_bytes)
        except (ImmichAPIError, OSError, RuntimeError, ValueError):
            thumbnail_fn = None  # Fall back to full download

    if thumb_path.exists():
        try:
            return score_photo_with_llm(
                thumb_path,
                meta_score,
                config,
                app_config,
                provider_circuit=provider_circuit,
            )
        except (OSError, RuntimeError, ValueError):
            return None

    # Fallback: download full file (old behavior)
    ext = Path(asset.original_file_name).suffix if asset.original_file_name else ".jpg"
    raw_path = work_dir / f"{asset.id}{ext}"
    if not raw_path.exists():
        try:
            download_fn(asset.id, raw_path)
        except (ImmichAPIError, OSError, RuntimeError, ValueError):
            return None

    try:
        from immich_memories.photos.animator import prepare_photo_source

        prepared = prepare_photo_source(raw_path, work_dir)
        return score_photo_with_llm(
            prepared.path,
            meta_score,
            config,
            app_config,
            provider_circuit=provider_circuit,
        )
    except (OSError, RuntimeError, ValueError):
        return None


def _cached_scores(cache, asset_ids: list[str], model_version: str | None) -> dict:
    """Previously computed scores, or nothing if the cache cannot answer.

    The cache opens lazily, so an unwritable database survives construction
    and raises on the first read instead — which took photo scoring down with
    it. Losing the cache costs LLM calls, not the run.
    """
    import sqlite3

    if not cache or not model_version:
        return {}
    try:
        return cache.get_asset_scores_batch(asset_ids, model_version=model_version)
    except (OSError, sqlite3.Error) as exc:
        logger.debug("Photo score cache unreadable (%s): rescoring", exc)
        return {}


def _get_score_cache(db_path: Path):
    """Get the asset score cache, or None when it cannot be opened.

    sqlite raises OperationalError rather than OSError for an unwritable or
    missing directory, so that case escaped the guard and took photo scoring
    down with it. A cache that cannot be opened costs repeated LLM calls, not
    a failed run.
    """
    import sqlite3

    try:
        from immich_memories.cache.asset_score_cache import AssetScoreCache

        return AssetScoreCache(db_path=db_path)
    except (ImportError, OSError, sqlite3.Error) as exc:
        logger.debug("Photo score cache unavailable (%s): scores will not persist", exc)
        return None
