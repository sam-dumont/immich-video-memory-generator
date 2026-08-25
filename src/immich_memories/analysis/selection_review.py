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
from dataclasses import dataclass, field
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

Ask ONE question of every clip: **would you show this to someone else?**

That is the whole test. Not whether the photograph is good, not whether the
day was important — whether this particular clip is one you would put in front
of another person as part of the story. A picture kept for a reason that is
nobody else's business fails it: an injury, a symptom, a bathroom, a screen
you photographed to remember something, a room you were documenting. So does
the fourth photograph of a thing already shown three times, however good it
is on its own.

The categories below are ways a clip fails that question, not a checklist to
match against. When one of them fits, the answer was already no.

Each clip carries `when=` (its timestamp), `episode=` (which occasion it
belongs to), `starred=yes` when the owner marked it a favourite, and
`camera=front` when the selfie camera took it. A front-camera picture is
usually of the person holding the phone; some are worth showing and many are
not, so let it inform the question rather than answer it.

Clips sharing an episode name happened in one place across one stretch of
time — one site visit, one party, one hike. They are ONE OCCASION however
different their descriptions read: three brick pavilions from one afternoon
are one visit, not architectural variety.

Two clips can also show one scene under different dates: cameras keep their
own clocks and one of them is often wrong by hours or a day, so trust what the
descriptions show over what the dates imply.

`starred=yes` is the owner's own judgment and outranks yours. Never drop a
starred clip in favour of an unstarred one from the same episode. When one
episode holds SEVERAL starred clips, choose between THOSE — an occasion the
owner starred repeatedly still earns one place, not one each, and that is the
only case where dropping a starred clip is right.

{clips}

Judge the SET as a whole: feel, coherence, variety. A clip fails the question
when it is

- REDUNDANT: the same moment, scene or kind of shot is already in the set.
  An episode earns ONE place unless each of its clips shows something the
  others genuinely do not. The same occasion photographed over and over is one
  idea shown many times, however much its descriptions differ.
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

`unreadable=yes` is different, and the rule above does not cover it. That clip
WAS looked at and could not be described. Nothing will ever describe it, so it
cannot be judged on what it shows and cannot answer the question. Shipping
something nobody can describe is worse than a shorter memory: drop it unless
the rest of the line gives you a reason to keep it.

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


def _front_camera(exif: object) -> bool:
    """Whether the selfie camera took this.

    Immich carries the lens through from EXIF, and a front lens names itself.
    An asset with no lens recorded says nothing either way.
    """
    lens = getattr(exif, "lens_model", None)
    return isinstance(lens, str) and "front" in lens.lower()


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


def _clip_line(
    index: int,
    member: ClipWithSegment,
    moment: str | None = None,
    *,
    unreadable: bool = False,
) -> str:

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
        parts.append(f"episode={moment}")
    if _front_camera(getattr(clip.asset, "exif_info", None)):
        # How a phone says "this is a picture of me". The owner's junk list is
        # full of them and his keepers are not, but the description cannot
        # tell them apart: "a person indoors" is both.
        parts.append("camera=front")
    if getattr(clip.asset, "is_favorite", False):
        # Several starred clips in one occasion are a battle to judge between.
        # Not saying so let the review drop a favourite and keep the unstarred
        # clip beside it — and the review shrinks the pool, so nothing
        # downstream could put it back.
        parts.append("starred=yes")
    where = _place_for_llm(getattr(clip.asset, "exif_info", None))
    if where:
        parts.append(f"place={where}")
    parts.append(f"score={member.score:.2f}")
    if unreadable and not getattr(clip, "llm_description", None):
        # Looked at, and could not be described. Distinct from silence: the
        # rule that protects a clip nobody has queued would otherwise protect
        # this one forever, and verify never re-queues an attempt.
        parts.append("unreadable=yes")
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


@dataclass(frozen=True)
class ReviewVerdict:
    """The drops that were carried out, and one line per entry saying why.

    Returned rather than logged alone so the trace can carry the ledger into
    the file a rejection is diagnosed from.
    """

    drops: list[str] = field(default_factory=list)
    fates: list[str] = field(default_factory=list)


def _clips_block(
    selected: list[ClipWithSegment],
    unreadable_ids: set[str] | frozenset[str] = frozenset(),
) -> str:
    """Every clip as the judge sees it, each one told which moment it is in.

    Occasions come from the shared time-and-place grouping rather than a
    window invented here, so what the judge is asked to judge is the same unit
    the rest of the pipeline reasons about.
    """
    return "\n".join(
        _clip_line(
            index + 1,
            member,
            episode,
            unreadable=member.clip.asset.id in unreadable_ids,
        )
        for index, (member, episode) in enumerate(
            zip(selected, _episode_labels(selected), strict=True)
        )
    )


def _episode_labels(selected: list[ClipWithSegment]) -> list[str]:
    """An occasion name per clip, in the order given.

    The EPISODE window, not the moment one. A site visit ran 15:07, 15:22 and
    16:04 in one place: fifteen and forty-two minutes apart, so every
    same-moment window called them three different things and the judge, shown
    three labels on one day, had no basis to object. "Three brick pavilions"
    reads as architectural variety in text. An episode is the block a moment
    sits in — an afternoon somewhere, a party, a hike — which is the unit a
    memory should show once.
    """
    from immich_memories.analysis.moment_grouping import (
        EPISODE_WINDOW_MINUTES,
        _group_by_time_and_place,
    )

    assets = [m.clip.asset for m in selected]
    by_asset: dict[str, str] = {}
    grouped = _group_by_time_and_place(assets, window_minutes=EPISODE_WINDOW_MINUTES)
    for number, episode in enumerate(grouped, start=1):
        for asset in episode:
            by_asset[asset.id] = f"E{number}"
    return [by_asset.get(m.clip.asset.id, "E?") for m in selected]


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
    unreadable_ids: set[str] | frozenset[str] = frozenset(),
) -> ReviewVerdict:
    """What the review decided, and what was done about each part of it.

    Nothing is dropped on any doubt — the pass is fail-open by design.

    Every outcome here says which one it was. The pass is fail-open by design
    — a model that cannot answer must not be able to gut a memory — and that
    made "the model read the set and approved it" identical to "the model
    returned an empty string": both an empty list and both silent. A rendered
    year recap came back with 38 clips and no drop lines, which reads as a
    clean cut and was a broken call.
    """
    if len(selected) < 3:
        logger.debug("Selection review: %d clips is too few to judge as a set", len(selected))
        return ReviewVerdict()
    clips_block = _clips_block(selected, unreadable_ids)
    prompt = _PROMPT.format(clips=clips_block)
    try:
        raw = _ask(prompt, llm_config, timeout_seconds, cache_path)
    except Exception as e:  # WHY broad: the review is optional; never break selection
        stop_if_this_is_our_bug(e, "selection review")
        logger.warning("Selection review unavailable (%s): nothing dropped", type(e).__name__)
        return ReviewVerdict()

    entries = _verdict_in(raw)
    if entries is None:
        logger.warning(
            "Selection review: could not read a verdict from %d chars — nothing dropped. "
            "This is not an approved cut; the answer was unreadable. [%s]",
            # a null content is documented mlx-vlm behaviour, not an empty answer
            len(raw) if raw else 0,
            UNREADABLE_VERDICT_MARKER,
        )
        return ReviewVerdict()
    verdict = _apply(entries, selected)
    for fate in verdict.fates:
        logger.info("Selection review: %s", fate)
    if not verdict.drops:
        logger.info("Selection review: read %d clips as a set, nothing to drop", len(selected))
    return verdict


def _apply(entries: list, selected: list[ClipWithSegment]) -> ReviewVerdict:
    """Carry out the verdict, and say what happened to every part of it.

    Each entry gets one line naming its fate. The old code logged
    `entries[:len(drops)]` — the FIRST n entries rather than the applied ones —
    so a vetoed entry was reported as dropped and four renders of drop lines
    were partly fiction.

    The starred rule is the owner's, and it is a battle, not an immunity: an
    occasion the owner starred repeatedly earns one place, so a starred clip
    may lose to a starred sibling from the same occasion but never to anything
    else. The code this replaces skipped every starred clip silently, which
    made starred junk immortal and the prompt's instruction a dead letter.
    """
    from collections import Counter

    max_drops = max(1, int(len(selected) * _MAX_DROP_RATIO))
    episodes = _episode_labels(selected)
    stars_left = Counter(
        episode
        for episode, member in zip(episodes, selected, strict=True)
        if getattr(member.clip.asset, "is_favorite", False)
    )

    drops: list[str] = []
    fates: list[str] = []
    for entry in entries:
        dropped, fate = _fate_of(entry, selected, episodes, stars_left, drops, max_drops)
        fates.append(fate)
        if dropped is not None:
            drops.append(dropped)
    return ReviewVerdict(drops=drops, fates=fates)


def _fate_of(
    entry: object,
    selected: list[ClipWithSegment],
    episodes: list[str],
    stars_left: dict[str, int],
    drops: list[str],
    max_drops: int,
) -> tuple[str | None, str]:
    """What becomes of one verdict entry, and the line that says so."""
    index = entry.get("index") if isinstance(entry, dict) else None
    reason = entry.get("reason", "no reason given") if isinstance(entry, dict) else "unreadable"
    if not isinstance(index, int) or isinstance(index, bool):
        return None, f"clip {index!r}: not a clip number, ignored ({reason})"
    if not 1 <= index <= len(selected):
        return None, f"clip {index}: no such clip in the cut, ignored ({reason})"

    member = selected[index - 1]
    asset_id = member.clip.asset.id
    if asset_id in drops:
        return None, f"{asset_id}: named twice, already dropped ({reason})"
    if len(drops) >= max_drops:
        return None, f"{asset_id}: kept — cap of {max_drops} drop(s) this round reached ({reason})"

    episode = episodes[index - 1]
    if getattr(member.clip.asset, "is_favorite", False):
        if stars_left[episode] < 2:
            return (
                None,
                f"{asset_id}: kept — starred, and the only starred clip of its occasion ({reason})",
            )
        stars_left[episode] -= 1
    return asset_id, f"{asset_id}: applied ({reason})"
