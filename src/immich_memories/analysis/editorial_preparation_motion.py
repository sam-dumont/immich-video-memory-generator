"""One line of motion per video, asked once of the caption seat and banked like a caption.

The pick compares a video with a still of the same moment. It used to learn what happens in
the video by sending sampled frames to the reader during the cut, every cut. Now the small
caption model reads three keyframes once, at preparation, and the reader only ever sees the
banked sentence. A miss, or a tier without a caption seat, gives the pick the plain facts.
"""

from __future__ import annotations

import base64
import io
import json
import sqlite3
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from contextvars import copy_context
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from immich_memories.analysis.editorial_bound_sample import source_metadata_digest
from immich_memories.analysis.editorial_description_contract import API_MODEL, REPETITION_PENALTY
from immich_memories.analysis.editorial_motion_facts import RESIDUAL_PRODUCER
from immich_memories.analysis.editorial_numbers import exact_number
from immich_memories.analysis.editorial_preparation_captions import (
    CAPTION_KEY_HINT,
    REFUSED_CODES,
    bearer_headers,
)
from immich_memories.analysis.editorial_structure_budget import RESIDUAL_MIN
from immich_memories.api.models import Asset
from immich_memories.processing.playback_keyframes import SampledKeyframes, sample_keyframes
from immich_memories.store.cut_measurements import (
    banked_motion_residuals,
    reading_cut_measurements,
)
from immich_memories.store.motion_lines import (
    DESCRIBED,
    UNAVAILABLE,
    MotionLine,
    initialize_motion_lines,
    remember_motion_line,
    settled_motion_lines,
)

FRAMES = 3
TILE = 320
MOTION_PRODUCER = f"motion-line-v1@{API_MODEL}/{FRAMES}-keyframes-{TILE}px"
TEXT_MAX_CHARS = 120
# Sampling is network and FFmpeg; the seat has its own bound (caption_concurrency).
SAMPLE_WORKERS = 4
PLAYBACK_UNAVAILABLE = "playback unavailable at Immich (HTTP 404)"
PROMPT = (
    "Return only JSON matching the schema. The image shows frames of one short video, in time "
    "order from left to right. In one concise sentence, describe only the plainly visible "
    "action across the frames."
)
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"description": {"type": "string", "maxLength": TEXT_MAX_CHARS}},
    "required": ["description"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class MotionSource:
    """The picture a line belongs to, the video sampled for it, and the picture's metadata."""

    asset_id: str
    playback_id: str
    digest: str


def motion_sources(
    assets: Sequence[Asset], *, residual_of: Callable[[Asset], float | None]
) -> tuple[MotionSource, ...]:
    """Every true video, and every Live Photo whose measured motion plays.

    A Live Photo nobody measured yet is left to the plain facts: its motion is not known to play.
    """
    sources = []
    for asset in assets:
        if asset.is_video:
            playback = asset.id
        elif asset.live_photo_video_id and (residual_of(asset) or 0.0) >= RESIDUAL_MIN:
            playback = asset.live_photo_video_id
        else:
            continue
        sources.append(MotionSource(asset.id, playback, source_metadata_digest(asset)))
    return tuple(sources)


def banked_residuals(store_path: Path) -> Callable[[Asset], float | None]:
    """A Live still's residual as a cut measured it, read only; None when nobody measured it."""

    def residual_of(asset: Asset) -> float | None:
        measured = read_motion_residuals(store_path, (asset,))
        return exact_number(measured[asset.id].get("residual")) if asset.id in measured else None

    return residual_of


def read_motion_residuals(store_path: Path, assets: Iterable[Asset]) -> dict[str, dict[str, Any]]:
    """What a cut already measured about these pictures' motion, keyed by picture."""
    digests = {asset.id: source_metadata_digest(asset) for asset in assets}
    if not digests:
        return {}
    return reading_cut_measurements(
        store_path, lambda c: banked_motion_residuals(c, digests, RESIDUAL_PRODUCER)
    )


def missing_motion(
    connection: sqlite3.Connection, sources: Sequence[MotionSource]
) -> tuple[MotionSource, ...]:
    initialize_motion_lines(connection)
    settled = settled_motion_lines(
        connection, {s.asset_id: s.digest for s in sources}, MOTION_PRODUCER
    )
    return tuple(s for s in sources if s.asset_id not in settled)


def filmstrip(frames: Sequence[bytes]) -> bytes:
    """The frames side by side, each centred in its own square tile, as one JPEG."""
    strip = Image.new("RGB", (TILE * len(frames), TILE))
    for index, data in enumerate(frames):
        with Image.open(io.BytesIO(data)) as source:
            image = source.convert("RGB")
        image.thumbnail((TILE, TILE))
        strip.paste(image, (index * TILE + (TILE - image.width) // 2, (TILE - image.height) // 2))
    buffer = io.BytesIO()
    strip.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def motion_text(raw: str) -> str:
    """One plain sentence from the seat's answer, or ValueError."""
    answer = json.loads(raw)
    if not isinstance(answer, dict) or set(answer) != {"description"}:
        raise ValueError("motion answer has the wrong keys")
    text = answer["description"]
    if not isinstance(text, str):
        raise ValueError("motion description is not text")
    text = " ".join(text.split()).rstrip(" ,;:")
    if not text or len(text) > TEXT_MAX_CHARS:
        raise ValueError("motion description is blank or over its cap")
    return text


def seat_asker(base_url: str, *, api_key: str, timeout: float) -> Callable[[bytes], str]:
    """Ask the caption seat about one filmstrip; its raw content, or the transport's error."""

    def ask(strip: bytes) -> str:
        payload = {
            "model": API_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/jpeg;base64," + base64.b64encode(strip).decode(),
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 140,
            "temperature": 0.0,
            "repetition_penalty": REPETITION_PENALTY,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "video_motion", "strict": True, "schema": SCHEMA},
            },
        }
        request = urllib.request.Request(  # noqa: S310
            f"{base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"} | bearer_headers(api_key),
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                body = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code in REFUSED_CODES:
                raise PermissionError(
                    f"caption endpoint {base_url} answered HTTP {exc.code}; {CAPTION_KEY_HINT}"
                ) from exc
            raise
        choice = body["choices"][0]
        if choice.get("finish_reason") != "stop":
            return ""
        return str(choice["message"]["content"])

    return ask


def playback_sampler(
    read_playback: Callable[[str, int, int], tuple[bytes, int]],
) -> Callable[[str], SampledKeyframes]:
    """Sample keyframes on several threads while the byte-range reads take turns.

    The synchronous Immich client drives one event loop, which refuses a second caller while
    the first is inside it; decoding is what the threads are for.
    """
    turn = threading.Lock()

    def read(playback_id: str, start: int, length: int) -> tuple[bytes, int]:
        with turn:
            return read_playback(playback_id, start, length)

    def sample(playback_id: str) -> SampledKeyframes:
        with tempfile.TemporaryDirectory(prefix="motion-") as workdir:
            return sample_keyframes(
                lambda start, length: read(playback_id, start, length),
                count=FRAMES,
                width=TILE,
                workdir=Path(workdir),
            )

    return sample


@dataclass
class MotionPreparation:
    """What one pass settled, what it left undone, and what the playback reads cost."""

    failures: dict[str, str] = field(default_factory=dict)
    described: int = 0
    unavailable: int = 0
    bytes_read: int = 0
    requests: int = 0
    seat_calls: int = 0


@dataclass(frozen=True, slots=True)
class _Outcome:
    line: MotionLine | None
    bytes_read: int = 0
    requests: int = 0
    seat_calls: int = 0
    failure: str = ""


def _refusal(exc: BaseException) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404


def _describe(source: MotionSource, sample, ask, seat: threading.Semaphore) -> _Outcome:
    try:
        sampled = sample(source.playback_id)
    except (httpx.HTTPError, OSError, subprocess.SubprocessError) as exc:
        if _refusal(exc):
            return _Outcome(MotionLine(UNAVAILABLE, PLAYBACK_UNAVAILABLE, 0))
        return _Outcome(None, failure=f"{type(exc).__name__}: {exc}")
    except ValueError as exc:
        return _Outcome(MotionLine(UNAVAILABLE, f"no readable keyframes: {exc}", 0))
    strip, errors = filmstrip(sampled.frames), []
    for attempt in range(2):
        try:
            with seat:
                raw = ask(strip)
        except PermissionError:
            raise
        except (OSError, KeyError, TypeError, ValueError) as exc:
            return _Outcome(None, sampled.bytes_read, sampled.requests, attempt + 1, str(exc))
        try:
            line = MotionLine(DESCRIBED, motion_text(raw), len(sampled.frames))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        return _Outcome(line, sampled.bytes_read, sampled.requests, attempt + 1)
    reason = "two invalid completions: " + "; ".join(errors)
    return _Outcome(MotionLine(UNAVAILABLE, reason, 0), sampled.bytes_read, sampled.requests, 2)


def prepare_motion_lines(
    *,
    connection: sqlite3.Connection,
    sources: Sequence[MotionSource],
    sample: Callable[[str], SampledKeyframes],
    ask: Callable[[bytes], str],
    concurrency: int,
    check_cancelled: Callable[[], None],
    progress: Callable[[str, int, int], None],
) -> MotionPreparation:
    """Bank a line, or a settled refusal, for each source; transport failures stay undone.

    A refused credential at the seat is not one video's problem, so PermissionError leaves.
    """
    initialize_motion_lines(connection)
    outcome = MotionPreparation()
    seat = threading.Semaphore(concurrency)
    step = max(SAMPLE_WORKERS, concurrency)
    with ThreadPoolExecutor(max_workers=step) as pool:
        for start in range(0, len(sources), step):
            check_cancelled()
            batch = sources[start : start + step]
            futures = [
                pool.submit(copy_context().run, _describe, source, sample, ask, seat)
                for source in batch
            ]
            for source, future in zip(batch, futures, strict=True):
                _settle(connection, source, future.result(), outcome)
            progress("motion", start + len(batch), len(sources))
    return outcome


def _settle(
    connection: sqlite3.Connection,
    source: MotionSource,
    result: _Outcome,
    outcome: MotionPreparation,
) -> None:
    outcome.bytes_read += result.bytes_read
    outcome.requests += result.requests
    outcome.seat_calls += result.seat_calls
    if result.line is None:
        outcome.failures[source.asset_id] = result.failure
        return
    remember_motion_line(
        connection,
        asset_id=source.asset_id,
        producer=MOTION_PRODUCER,
        source_digest=source.digest,
        line=result.line,
        bytes_read=result.bytes_read,
    )
    if result.line.status == DESCRIBED:
        outcome.described += 1
    else:
        outcome.unavailable += 1


def plain_motion_facts(unit: Mapping[str, Any]) -> str:
    """What the unit itself says about its motion, for a pick that has no banked line."""
    what = "video" if unit.get("kind") == "video" else "live photo"
    seconds = exact_number(unit.get("raw_seconds"))
    parts = [f"{seconds:.0f} s of {what}" if seconds else what]
    if (residual := exact_number(unit.get("residual"))) is not None:
        parts.append(f"motion {residual:.1f}")
    if unit.get("speech_regions"):
        parts.append("speech")
    return "not described; " + ", ".join(parts)


class BankedMotionLines:
    """The pick's motion evidence: the banked line, else the plain facts. Never a model call."""

    producer = MOTION_PRODUCER

    def __init__(self, *, store_path: Path, assets: Mapping[str, Asset], described: bool) -> None:
        self._store_path = store_path
        self._assets = assets
        self._described = described
        self._counts = {"requested": 0, "banked": 0, "plain_facts": 0}

    def observe(self, unit: Mapping[str, Any]) -> str:
        self._counts["requested"] += 1
        members = [unit["asset_id"], *unit.get("members", ())]
        line = self._banked([a for a in dict.fromkeys(members) if a in self._assets])
        if line is None:
            self._counts["plain_facts"] += 1
            return plain_motion_facts(unit)
        self._counts["banked"] += 1
        return f"{line.text} ({line.frames} frames across the clip)"

    def _banked(self, asset_ids: list[str]) -> MotionLine | None:
        if not self._described or not asset_ids or not self._store_path.exists():
            return None
        digests = {a: source_metadata_digest(self._assets[a]) for a in asset_ids}
        uri = f"file:{self._store_path}?mode=ro"
        try:
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                settled = settled_motion_lines(connection, digests, MOTION_PRODUCER)
        except sqlite3.Error:
            return None
        return next(
            (settled[a] for a in asset_ids if a in settled and settled[a].status == DESCRIBED),
            None,
        )

    def metrics(self) -> dict[str, Any]:
        return {"producer": self.producer} | self._counts
