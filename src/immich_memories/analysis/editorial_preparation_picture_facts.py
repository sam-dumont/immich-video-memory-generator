"""Typed facts read off each picture once, at ingest, by a local typed-decision reader.

The reader is asked a frozen set of yes/no and multiple-choice questions about one 800 px tile
and answers with a probability per question. Only the raw numbers are banked: this producer
names what it sees, and the gates downstream own what that means. It is off unless a deployment
configures it, it talks to nothing but its configured endpoint, and it is paid once per source.

The question texts are hashed into the producer string, so rewording one does not quietly mix
two answer sets in a bank; the old rows stop answering and the pictures are read again.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Container, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from contextvars import copy_context
from dataclasses import dataclass, field
from typing import Any, Protocol

from immich_memories.analysis.editorial_bound_sample import source_metadata_digest
from immich_memories.analysis.editorial_picture_facts import picture_tile
from immich_memories.api.models import Asset
from immich_memories.config_models_editorial_preparation import PictureFactsConfig
from immich_memories.store.picture_facts import (
    DESCRIBED,
    REFUSED,
    UNAVAILABLE,
    PictureFacts,
    initialize_picture_facts,
    remember_picture_facts,
    settled_picture_facts,
)

READER_MODEL = "openjev-latest"
STATE = "Look at the photo."
# One picture per request: the probe measured eight tiles in one request at 3.0 s a picture
# against 0.83 s alone, because the reader re-reads every tile for every question.

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
    """The identity of the exact texts asked, so a reworded question cannot reuse old rows."""
    payload = json.dumps(questions, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


PICTURE_FACTS_PRODUCER = f"picture-facts-v1@openjev/{question_set_hash(QUESTIONS)}"


@dataclass(frozen=True, slots=True)
class PictureFactsSource:
    """The picture a row belongs to, and the metadata digest the row answers for."""

    asset_id: str
    digest: str


def picture_facts_sources(assets: Sequence[Asset]) -> tuple[PictureFactsSource, ...]:
    """Every still and Live Photo: a true video has no single tile to read."""
    return tuple(
        PictureFactsSource(asset.id, source_metadata_digest(asset))
        for asset in assets
        if not asset.is_video
    )


def missing_picture_facts(
    connection: sqlite3.Connection, sources: Sequence[PictureFactsSource]
) -> tuple[PictureFactsSource, ...]:
    initialize_picture_facts(connection)
    settled = settled_picture_facts(
        connection, {s.asset_id: s.digest for s in sources}, PICTURE_FACTS_PRODUCER
    )
    return tuple(s for s in sources if s.asset_id not in settled)


def read_answers(reply: Mapping[str, Any]) -> dict[str, Any]:
    """The reader's raw numbers, one entry per question it answered; ValueError otherwise."""
    answers = reply.get("answers")
    if not isinstance(answers, Mapping):
        raise ValueError("picture-facts reply carries no answers")
    read: dict[str, Any] = {}
    for name, question in QUESTIONS.items():
        answer = answers.get(name)
        if not isinstance(answer, Mapping):
            raise ValueError(f"picture-facts reply has no answer for {name}")
        read[name] = _noul(answer) if question["type"] == "noul" else _choice(name, answer)
    return read


def _noul(answer: Mapping[str, Any]) -> float:
    value = answer.get("noul")
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError("picture-facts noul answer is not a probability")
    return round(float(value), 4)


def _choice(name: str, answer: Mapping[str, Any]) -> dict[str, Any]:
    label = answer.get("choice")
    spread = answer.get("probabilities")
    if label not in QUESTIONS[name]["criteria"] or not isinstance(spread, Mapping):
        raise ValueError(f"picture-facts choice answer for {name} is not one of its options")
    return {
        "choice": label,
        "probabilities": {
            option: round(float(value), 4)
            for option, value in spread.items()
            if option in QUESTIONS[name]["criteria"] and isinstance(value, int | float)
        },
    }


def reader_asker(base_url: str, *, timeout: float) -> Callable[[bytes], Mapping[str, Any]]:
    """Ask the configured reader about one tile. Nothing else is ever contacted."""
    url = f"{base_url.rstrip('/')}/systemone"

    def ask(tile: bytes) -> Mapping[str, Any]:
        payload = {
            "model": READER_MODEL,
            "state": STATE,
            "images": ["data:image/jpeg;base64," + base64.b64encode(tile).decode()],
            "questions": QUESTIONS,
        }
        request = urllib.request.Request(  # noqa: S310
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            reply = json.loads(response.read())
        if not isinstance(reply, Mapping):
            raise ValueError("picture-facts reply is not an object")
        return reply

    return ask


@dataclass
class PictureFactsPreparation:
    """What one pass settled, what it left undone, and what it asked the reader."""

    failures: dict[str, str] = field(default_factory=dict)
    described: int = 0
    refused: int = 0
    requests: int = 0


@dataclass(frozen=True, slots=True)
class _Outcome:
    facts: PictureFacts | None
    requests: int = 0
    failure: str = ""


def _describe(
    source: PictureFactsSource,
    preview_for: Callable[[str], bytes],
    ask: Callable[[bytes], Mapping[str, Any]],
    reader: threading.Semaphore,
) -> _Outcome:
    try:
        tile = picture_tile(preview_for(source.asset_id))
    except (OSError, ValueError) as exc:
        return _Outcome(PictureFacts(UNAVAILABLE, {"reason": f"no readable tile: {exc}"}))
    try:
        with reader:
            reply = ask(tile)
    except urllib.error.HTTPError as exc:
        return _Outcome(PictureFacts(REFUSED, {"reason": f"reader answered HTTP {exc.code}"}), 1)
    except (OSError, ValueError) as exc:
        return _Outcome(None, 1, f"{type(exc).__name__}: {exc}")
    try:
        return _Outcome(PictureFacts(DESCRIBED, read_answers(reply)), 1)
    except ValueError as exc:
        return _Outcome(PictureFacts(REFUSED, {"reason": str(exc)}), 1)


def prepare_picture_facts(
    *,
    connection: sqlite3.Connection,
    sources: Sequence[PictureFactsSource],
    preview_for: Callable[[str], bytes],
    ask: Callable[[bytes], Mapping[str, Any]],
    concurrency: int,
    check_cancelled: Callable[[], None],
    progress: Callable[[str, int, int], None],
) -> PictureFactsPreparation:
    """Bank a row, or a settled refusal, for each source; transport failures stay undone."""
    initialize_picture_facts(connection)
    outcome = PictureFactsPreparation()
    reader = threading.Semaphore(concurrency)
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for start in range(0, len(sources), max(1, concurrency)):
            check_cancelled()
            batch = sources[start : start + max(1, concurrency)]
            futures = [
                pool.submit(copy_context().run, _describe, source, preview_for, ask, reader)
                for source in batch
            ]
            for source, future in zip(batch, futures, strict=True):
                _settle(connection, source, future.result(), outcome)
            progress("picture_facts", start + len(batch), len(sources))
    return outcome


def _settle(
    connection: sqlite3.Connection,
    source: PictureFactsSource,
    result: _Outcome,
    outcome: PictureFactsPreparation,
) -> None:
    outcome.requests += result.requests
    if result.facts is None:
        outcome.failures[source.asset_id] = result.failure
        return
    remember_picture_facts(
        connection,
        asset_id=source.asset_id,
        producer=PICTURE_FACTS_PRODUCER,
        source_digest=source.digest,
        facts=result.facts,
    )
    if result.facts.status == DESCRIBED:
        outcome.described += 1
    else:
        outcome.refused += 1


class PreparationStage(Protocol):
    """What a producer needs from the acquisition pass it runs inside."""

    @property
    def failures(self) -> dict[str, str]: ...

    @property
    def transfer(self) -> dict[str, dict[str, int]]: ...

    @property
    def check(self) -> Callable[[], None]: ...

    @property
    def report(self) -> Callable[[str, int, int], None]: ...

    @property
    def preview_for(self) -> Callable[[str], bytes]: ...

    def timed(self, stage: str, pictures: int) -> AbstractContextManager[None]: ...


def acquire_picture_facts(
    stage: PreparationStage,
    connection: sqlite3.Connection,
    assets: Sequence[Asset],
    served: Container[str],
    *,
    config: PictureFactsConfig,
    provider: Callable[..., PictureFactsPreparation],
) -> None:
    """Read every picture this pass has not read yet, or report why the reader could not.

    An endpoint that is not there is one producer's problem, named once against the
    endpoint. Nothing here is a demanded fact, so no absence of it can block a cut.
    """
    owed = _owed(connection, assets, served) if config.enabled else ()
    if not owed:
        return
    stage.check()
    try:
        with stage.timed("picture_facts", len(owed)):
            outcome = provider(
                connection=connection,
                sources=owed,
                preview_for=stage.preview_for,
                ask=reader_asker(config.base_url, timeout=config.timeout_seconds),
                concurrency=config.concurrency,
                check_cancelled=stage.check,
                progress=stage.report,
            )
    except Exception as exc:
        stage.failures["picture_facts"] = (
            f"picture-facts reader at {config.base_url}: {type(exc).__name__}: {exc}; "
            "no picture facts were banked this run"
        )
        return
    stage.failures.update({f"picture_facts:{k}": v for k, v in outcome.failures.items()})
    stage.transfer["picture_facts"] = {"requests": outcome.requests}


def _owed(
    connection: sqlite3.Connection, assets: Sequence[Asset], served: Container[str]
) -> tuple[PictureFactsSource, ...]:
    sources = [s for s in picture_facts_sources(assets) if s.asset_id in served]
    return missing_picture_facts(connection, sources)


_SHORT = {
    "screen": "screen",
    "overlay_graphics": "overlay",
    "face_extreme_closeup": "face-closeup",
    "bathing_now": "bathing",
    "breastfeeding_now": "breastfeeding",
    "medical_procedure": "medical",
    "private_record": "record",
}
# Below this a noul says nothing any gate reads, and the line is read by people too.
_QUIET_BELOW = 0.10
_QUIET_CHOICES = {"adult_coverage": "no_adult", "child_coverage": "no_child"}


def plain_picture_facts(facts: PictureFacts) -> str:
    """What the reader saw, as neutral numbers. No policy, no verdict, no prose."""
    if facts.status != DESCRIBED:
        return ""
    parts = [
        f"{_SHORT[name]} {probability:.2f}"
        for name in NOULS
        if name != "worth"
        and (probability := facts.noul(name)) is not None
        and probability >= _QUIET_BELOW
    ]
    if (worth := facts.noul("worth")) is not None:
        parts.append(f"worth {worth:.2f}")
    parts.extend(
        f"{name.removesuffix('_coverage')}={label}"
        for name in CHOICES
        if (label := facts.choice(name)) and label != _QUIET_CHOICES.get(name)
    )
    return "picture: " + "; ".join(parts) if parts else ""
