"""The LLM holistic pass over a finished selection (#468).

The mechanical judge sees scores; it cannot see that two clips are the same
birthday candles twice, or that one clip breaks the set's feel. This pass
shows the LLM the FULL cut — every clip's raw description, emotion, setting,
subjects and audio, in timeline order — and asks which clips are
redundant or clash with the whole. Raw data in, not pre-digested stats: the
model finds the patterns.

Strictly optional: any failure, timeout or unparseable answer drops nothing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.analysis.smart_pipeline import ClipWithSegment
    from immich_memories.config_models import LLMConfig

logger = logging.getLogger(__name__)

# An overeager model must not gut the video: at most this share of the
# selection can be dropped in one review, never below one allowed drop.
_MAX_DROP_RATIO = 0.2

_PROMPT = """You are reviewing the final cut of a personal memory video.
Below is every clip in timeline order, with everything we can see and hear.

{clips}

Judge the SET as a whole: feel, coherence, variety. Identify clips that are
REDUNDANT (the same moment or scene shown twice) or that CLASH with the rest
of the set. Most good selections need no changes.

Answer with STRICT JSON only, no prose:
{{"drop": [{{"index": <clip number>, "reason": "<short reason>"}}]}}
Use an empty list when the set is good."""


def _ask(prompt: str, llm_config: LLMConfig, timeout_seconds: int) -> str:
    from immich_memories.analysis.llm_query import query_llm

    return asyncio.run(
        query_llm(prompt, llm_config, temperature=0.2, timeout_seconds=timeout_seconds)
    )


def _clip_line(index: int, member: ClipWithSegment) -> str:
    from immich_memories.generate_privacy import clip_location_name

    clip = member.clip
    parts = [f"Clip {index}:"]
    # WHY read through asset: date and place live on the Immich asset, not on
    # VideoClipInfo — reading them off the clip silently sent nothing (#475).
    taken = getattr(clip.asset, "file_created_at", None)
    if taken:
        parts.append(f"date={taken.date().isoformat()}")
    where = clip_location_name(getattr(clip.asset, "exif_info", None))
    if where:
        parts.append(f"place={where}")
    parts.append(f"score={member.score:.2f}")
    for label, attr in (
        ("description", "llm_description"),
        ("emotion", "llm_emotion"),
        ("setting", "llm_setting"),
        ("subjects", "llm_subjects"),
        ("heard", "audio_categories"),
    ):
        value = getattr(clip, attr, None)
        if value:
            parts.append(f"{label}={value!s}")
    return " ".join(parts)


def review_selection(
    selected: list[ClipWithSegment],
    llm_config: LLMConfig,
    *,
    timeout_seconds: int = 45,
) -> list[str]:
    """Asset ids the LLM says to drop from the selection; [] on any doubt."""
    if len(selected) < 3:
        return []
    clips_block = "\n".join(_clip_line(i + 1, m) for i, m in enumerate(selected))
    prompt = _PROMPT.format(clips=clips_block)
    try:
        raw = _ask(prompt, llm_config, timeout_seconds)
    except Exception as e:  # WHY broad: the review is optional; never break selection
        logger.debug("Selection review skipped: %s", e)
        return []

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return []
    try:
        payload = json.loads(match.group(0))
        entries = payload.get("drop", [])
        indices = [int(e["index"]) for e in entries if "index" in e]
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        return []

    max_drops = max(1, int(len(selected) * _MAX_DROP_RATIO))
    drops = []
    for idx in indices[:max_drops]:
        if 1 <= idx <= len(selected):
            member = selected[idx - 1]
            if not getattr(member.clip.asset, "is_favorite", False):
                drops.append(member.clip.asset.id)
    for entry in entries[: len(drops)]:
        logger.info(
            "Selection review: dropping clip %s (%s)",
            entry.get("index"),
            entry.get("reason", "no reason given"),
        )
    return drops
