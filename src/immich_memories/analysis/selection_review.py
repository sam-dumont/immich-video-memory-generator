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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from immich_memories.analysis.smart_pipeline import ClipWithSegment
    from immich_memories.config_models_llm import LLMConfig

from immich_memories.analysis.llm_failures import stop_if_this_is_our_bug

logger = logging.getLogger(__name__)

# An overeager model must not gut the video: at most this share of the
# selection can be dropped in one review, never below one allowed drop.
_MAX_DROP_RATIO = 0.2

# Enough room that the model answers rather than narrating; see _ask.
_REVIEW_MAX_TOKENS = 8000

_PROMPT = """You are reviewing the final cut of a personal memory video.
Below is every clip in timeline order, with everything we can see and hear.

Each clip carries `when=` (its timestamp) and `moment=` (which moment it
belongs to). Clips sharing a moment name were shot in the same place within
minutes of each other — they ARE the same moment, whatever their descriptions
say. Two clips can also show one scene under different dates: cameras keep
their own clocks and one of them is often wrong by hours or a day, so trust
what the descriptions show over what the dates imply.

{clips}

Judge the SET as a whole: feel, coherence, variety. Drop a clip when it is

- REDUNDANT: the same moment, scene or kind of shot is already in the set.
  Two clips sharing a `moment=` name are the same moment: keep the better one
  unless each shows something the other does not.
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
  Nobody held the camera for some of these: a doorbell, a security camera or
  a dashcam records whatever passes it, and a person arriving at a front door
  is an event the house noticed rather than a moment anyone chose to keep.
  Drop these however good the picture is, and whoever is in them.

A memory of a trip or an occasion is not only the people in it. If every
clip is a person facing the camera, say so by dropping the weakest of them:
where they were is part of what happened.

A clip with no description has not been analysed yet. That says nothing about
whether it is any good: never drop a clip for missing information, and never
treat it as a duplicate on those grounds.

Most good selections need no changes. Answer with STRICT JSON only, no prose:
{{"drop": [{{"index": <clip number>, "reason": "<short reason>"}}]}}
Use an empty list when the set is good."""


def _ask(
    prompt: str, llm_config: LLMConfig, timeout_seconds: int, cache_path: Path | None = None
) -> str:
    from immich_memories.analysis.llm_query import query_llm

    return asyncio.run(
        query_llm(
            prompt,
            llm_config,
            temperature=0.2,
            # Measured on real review prompts against a local Qwen3.6-35B. The
            # 500-token default never produced a verdict at all: the model
            # narrates its reasoning into the content channel on a prompt this
            # long, and 500 tokens ran out mid-thought. 2000 was still not
            # enough. At 4000 a verdict appeared after ~15k characters of
            # narration; at 8000 the whole answer was 496 characters, because
            # a model with headroom answers instead of thinking aloud. A
            # bigger ceiling here is not a bigger bill.
            max_tokens=_REVIEW_MAX_TOKENS,
            timeout_seconds=timeout_seconds,
            thinking=True,
            cache_path=cache_path,
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


def _clip_line(index: int, member: ClipWithSegment, moment: str | None = None) -> str:

    clip = member.clip
    parts = [f"Clip {index}:"]
    # WHY read through asset: date and place live on the Immich asset, not on
    # VideoClipInfo — reading them off the clip silently sent nothing (#475).
    taken = getattr(clip.asset, "file_created_at", None)
    if taken:
        # The time of day, not just the date. Asked to drop "the same moment",
        # the judge was shown date=2011-08-04 twice and could not tell ten
        # minutes from ten hours: a ship-deck performance shipped three times.
        parts.append(f"when={taken.isoformat(timespec='minutes')}")
    if moment:
        parts.append(f"moment={moment}")
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


def _clips_block(selected: list[ClipWithSegment]) -> str:
    """Every clip as the judge sees it, each one told which moment it is in.

    Moments come from the shared time-and-place grouping rather than a window
    invented here, so "the same moment" means the same thing to the judge as
    it does to the stages that built the cut.
    """
    return "\n".join(
        _clip_line(index + 1, member, moment)
        for index, (member, moment) in enumerate(
            zip(selected, _moment_labels(selected), strict=True)
        )
    )


def _moment_labels(selected: list[ClipWithSegment]) -> list[str]:
    """A moment name per clip, in the order given."""
    from immich_memories.analysis.moment_grouping import _group_by_time_and_place

    assets = [m.clip.asset for m in selected]
    by_asset: dict[str, str] = {}
    for number, moment in enumerate(_group_by_time_and_place(assets), start=1):
        for asset in moment:
            by_asset[asset.id] = f"M{number}"
    return [by_asset.get(m.clip.asset.id, "M?") for m in selected]


def _verdict_in(raw: str | None) -> list | None:
    r"""The drop list the model actually produced, or None if it produced none.

    Not re.search(r"\{.*\}") over the whole answer. The prompt shows the model
    the shape it wants, and a model reasoning aloud quotes that shape back —
    so a first-brace-to-last-brace grab returns
    {"drop": [{"index": <number>, "reason": "<short reason>"}]}, json.loads
    chokes on the placeholder, and the review reports nothing to drop.

    Every brace-balanced candidate is tried instead, last first: the verdict
    comes after the reasoning, and a template echo never parses. Validating
    against the shape is what separates the answer from the question.
    """
    if not raw:
        return None
    for candidate in reversed(_balanced_objects(raw)):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("drop"), list):
            return payload["drop"]
    return None


def _balanced_objects(text: str) -> list[str]:
    """Every {...} in the text whose braces balance, outermost only."""
    spans, depth, start = [], 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                spans.append(text[start : i + 1])
    return spans


# Grep-able marker for the one thing this pass cannot say for itself. "0 drops"
# has meant both "the model approved this cut" and "the model never answered"
# for as long as the review has existed, and a reader cannot tell them apart.
# A counter belongs in the metrics layer once #654 lands; until then this at
# least makes the difference findable in a log.
UNREADABLE_VERDICT_MARKER = "selection_review_unreadable"


def review_selection(
    selected: list[ClipWithSegment],
    llm_config: LLMConfig,
    *,
    timeout_seconds: int = 45,
    cache_path: Path | None = None,
) -> list[str]:
    """Asset ids the LLM says to drop from the selection; [] on any doubt.

    Every outcome here says which one it was. The pass is fail-open by design
    — a model that cannot answer must not be able to gut a memory — and that
    made "the model read the set and approved it" identical to "the model
    returned an empty string": both an empty list and both silent. A rendered
    year recap came back with 38 clips and no drop lines, which reads as a
    clean cut and was a broken call.
    """
    if len(selected) < 3:
        logger.debug("Selection review: %d clips is too few to judge as a set", len(selected))
        return []
    clips_block = _clips_block(selected)
    prompt = _PROMPT.format(clips=clips_block)
    try:
        raw = _ask(prompt, llm_config, timeout_seconds, cache_path)
    except Exception as e:  # WHY broad: the review is optional; never break selection
        stop_if_this_is_our_bug(e, "selection review")
        logger.warning("Selection review unavailable (%s): nothing dropped", type(e).__name__)
        return []

    entries = _verdict_in(raw)
    if entries is None:
        logger.warning(
            "Selection review: could not read a verdict from %d chars — nothing dropped. "
            "This is not an approved cut; the answer was unreadable. [%s]",
            # a null content is documented mlx-vlm behaviour, not an empty answer
            len(raw) if raw else 0,
            UNREADABLE_VERDICT_MARKER,
        )
        return []
    try:
        indices = [int(e["index"]) for e in entries if "index" in e]
    except (TypeError, ValueError) as e:
        logger.warning(
            "Selection review answer could not be read (%s) [%s]: nothing dropped",
            type(e).__name__,
            UNREADABLE_VERDICT_MARKER,
        )
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
    if not drops:
        logger.info("Selection review: read %d clips as a set, nothing to drop", len(selected))
    return drops
