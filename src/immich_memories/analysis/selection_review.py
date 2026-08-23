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

Judge the SET as a whole: feel, coherence, variety. Drop a clip when it is

- REDUNDANT: the same moment, scene or kind of shot is already in the set.
  Self-portraits and mirror shots repeat hard — three of them from three days
  is one idea shown three times, however different the days were. A selfie of
  one person alone is the weakest way to record a day: prefer the clip that
  shows what they were doing, or who they were with, and drop the solo shot
  when the set already has one.
- CROWDING OUT: the same THING recurring until it takes over — a pet, a room,
  an object photographed on unrelated days while nothing else happens. Keep
  the best two or three and drop the rest, even when each is a fine
  photograph. Two exceptions, and they matter more than the rule:
    * A subject that is NEW is the story of the period it arrives in, not
      repetition — a kitten, a puppy, a first home, a first instrument. A
      quiet month whose one development is a new animal is allowed to be
      mostly that animal.
    * A person is never crowding out. When someone appears through most of a
      period they are usually its story: a new baby, a companion on a trip.
      Repetition of a person is REDUNDANT only when it is the same SHOT
      repeated, never merely the same face living their life.
    * A thing that CHANGES across the set is a project, not repetition — a
      house being renovated, a car being restored, furniture being built.
      Progress is the story. Keep the sequence and let it read in order.
- CLASHING: it breaks the feel the rest of the set has,
- NOT A MEMORY: it records a thing rather than a moment — an object or a
  product photographed on its own, a screen or a video game, a document or
  a receipt, an empty room, a photo taken to remember information rather
  than an occasion. A person can be in one of these: a head-and-shoulders
  portrait against a blank wall, facing the camera, no expression and no
  surroundings, is an identity photo taken for a form. Nothing happened when
  it was taken, and several of them in one set is a giveaway.

A memory of a trip or an occasion is not only the people in it. If every
clip is a person facing the camera, say so by dropping the weakest of them:
where they were is part of what happened.

A clip with no description has not been analysed yet. That says nothing about
whether it is any good: never drop a clip for missing information, and never
treat it as a duplicate on those grounds.

Most good selections need no changes. Answer with STRICT JSON only, no prose:
{{"drop": [{{"index": <clip number>, "reason": "<short reason>"}}]}}
Use an empty list when the set is good."""


def _ask(prompt: str, llm_config: LLMConfig, timeout_seconds: int) -> str:
    from immich_memories.analysis.llm_query import query_llm

    return asyncio.run(
        query_llm(
            prompt, llm_config, temperature=0.2, timeout_seconds=timeout_seconds, thinking=True
        )
    )


def _place_for_llm(exif: object) -> str | None:
    """City, state and country — the caption form is too thin to reason from.

    Captions say "Paradise" because that is what reads well on screen. A model
    asked whether a set of clips hangs together cannot do anything with that:
    Paradise and Winchester are the Las Vegas Strip townships, and without the
    state they look like two unrelated villages rather than one trip.
    """
    if not exif:
        return None
    parts = [getattr(exif, attr, None) for attr in ("city", "state", "country")]
    named = [p for p in parts if p]
    return ", ".join(named) if named else None


def _clip_line(index: int, member: ClipWithSegment) -> str:

    clip = member.clip
    parts = [f"Clip {index}:"]
    # WHY read through asset: date and place live on the Immich asset, not on
    # VideoClipInfo — reading them off the clip silently sent nothing (#475).
    taken = getattr(clip.asset, "file_created_at", None)
    if taken:
        parts.append(f"date={taken.date().isoformat()}")
    where = _place_for_llm(getattr(clip.asset, "exif_info", None))
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
