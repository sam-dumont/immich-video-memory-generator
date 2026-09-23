"""The audience question, asked of twelve carriers in one request.

The single question classifies one carrier's captions against a closed vocabulary, and asked
once per carrier it was 172 of the measured year's 634 requests. This asks the same vocabulary
of up to twelve carriers at once, each under its own label with its own answer, and hands each
carrier's answer to the single check exactly as if it had been asked alone: the check maps it,
applies every floor, and asks the exposure question itself. A batch the reader does not answer
for a carrier leaves that carrier to be asked alone, as before: a skipped carrier is never a
cleared one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import partial
from typing import Any

from immich_memories.analysis.editorial_page_recovery import read_page_answer
from immich_memories.analysis.editorial_reader_concurrency import reader_map
from immich_memories.analysis.editorial_shareability_audience import (
    ACTIVITY_CONTENT_FINDINGS,
    ACTIVITY_CONTENT_PROMPT,
    _first_json_object,
)

AUDIENCE_BATCH_SIZE = 12
_ANSWER_LINE = "Return one JSON object only:"
_ONE_GROUP = "Any matching picture makes its finding apply to this group."
_NUDITY_LINE = "- nudity_shirtless_or_underwear:"
_TAIL = (
    "Several groups of pictures follow, one group per line, each under its own label. "
    "Classify every group on its own: any matching picture makes its finding apply to its own "
    "group and to no other, and a finding in one group says nothing about the next.\n\n"
    "Return one JSON object only, with every label exactly once, for example: {example}. "
    'Each value is {{"finding":"one category above","why":"brief described content '
    'supporting it, at most 12 words"}}. Do not make an audience/export verdict.\n\n'
    "Groups:\n{listing}"
)


def batched_activity_prompt(
    groups: Sequence[tuple[str, Sequence[Mapping[str, Any]]]], *, allow_nudity: bool
) -> str:
    """The single question's vocabulary and rules, over several labelled groups of captions."""
    vocabulary = ACTIVITY_CONTENT_PROMPT[: ACTIVITY_CONTENT_PROMPT.index(_ANSWER_LINE)]
    vocabulary = vocabulary.replace(_ONE_GROUP, "Any matching picture makes its finding apply.")
    if not allow_nudity:
        vocabulary = "\n".join(
            line for line in vocabulary.splitlines() if not line.startswith(_NUDITY_LINE)
        )
    listing = "\n".join(f"{label}: {_captions(members)}" for label, members in groups)
    example = json.dumps(
        {label: {"finding": "none", "why": "..."} for label, _members in groups[:2]},
        separators=(",", ":"),
    )
    return vocabulary + _TAIL.format(example=example, listing=listing)


def read_batched_activity(raw: str, labels: Sequence[str], *, allow_nudity: bool) -> dict[str, str]:
    """Each answered label's answer, as the single check would have received it on its own.

    A label the reader skipped, or answered outside the vocabulary, is left out: that carrier is
    not cleared, it is asked alone. Raises ValueError only when nothing in the reply is readable,
    so the batch is asked again.
    """
    obj = _first_json_object(raw.strip())
    answered = {
        label: json.dumps(obj[label], ensure_ascii=False)
        for label in labels
        if obj is not None and _readable(obj.get(label), allow_nudity)
    }
    if not answered:
        raise ValueError(
            "Return one JSON object keyed by every group label, each with a finding from the "
            "vocabulary and a why"
        )
    return answered


def ask_activity_batches(
    judge, pending: Mapping[str, tuple[Mapping[str, Any], bool]], *, size: int
) -> dict[str, str]:
    """The activity answer for each evidence key, asked `size` carriers at a time.

    `pending` maps an evidence key to its evidence and whether the nudity finding may be
    answered for it; a batch only ever mixes carriers asked the same way. A batch that fails
    every bounded attempt answers none of its carriers, which the caller then asks alone.
    """
    chunks: list[tuple[list[str], bool]] = []
    for allow in (True, False):
        keys = [key for key, (_evidence, allowed) in pending.items() if allowed is allow]
        chunks.extend((keys[i : i + size], allow) for i in range(0, len(keys), size))
    answers: dict[str, str] = {}
    staged = [(number, keys, allow) for number, (keys, allow) in enumerate(chunks, 1)]
    for part in reader_map(judge, partial(_ask_batch, pending=pending), staged):
        answers.update(part)
    return answers


def _ask_batch(judge, chunk, *, pending) -> dict[str, str]:
    number, keys, allow = chunk
    labels = {key: f"G{index + 1:02d}" for index, key in enumerate(keys)}
    prompt = batched_activity_prompt(
        [(labels[key], pending[key][0].get("members", ())) for key in keys], allow_nudity=allow
    )
    try:
        by_label = read_page_answer(
            judge,
            stage=f"shareability-batch-{number:02d}-activity",
            prompt=prompt,
            max_tokens=60 * len(keys) + 60,
            read=partial(read_batched_activity, labels=list(labels.values()), allow_nudity=allow),
        )
    except ValueError:
        return {}
    return {key: by_label[labels[key]] for key in keys if labels[key] in by_label}


def _captions(members: Sequence[Mapping[str, Any]]) -> str:
    captions = [{"picture": member["member"], "caption": member["caption"]} for member in members]
    return json.dumps(captions, ensure_ascii=False, separators=(",", ":"))


def _readable(value: object, allow_nudity: bool) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("finding"), str)
        and value["finding"] in ACTIVITY_CONTENT_FINDINGS
        and (allow_nudity or value["finding"] != "nudity_shirtless_or_underwear")
        and isinstance(value.get("why"), str)
    )
