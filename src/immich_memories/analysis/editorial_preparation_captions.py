"""Portable compact-v3 caption requests, sharing the accepted wire and outcome contract."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import asdict, dataclass, replace

from PIL import Image

from immich_memories.analysis import editorial_description_outcomes as caption_outcomes
from immich_memories.analysis.editorial_description_contract import (
    API_MODEL,
    DESCRIPTION_MODEL,
    DESCRIPTION_SOURCE,
    DescriptionEnvelope,
)
from immich_memories.analysis.editorial_description_contract import (
    validate_envelope as _validate_envelope,
)
from immich_memories.analysis.editorial_description_wire import (
    request_payload as _wire_payload,
)
from immich_memories.analysis.editorial_description_wire import (
    tile_preview,
)
from immich_memories.analysis.llm_preparation_usage import record_preparation_attempt
from immich_memories.store.caption_provenance import (
    CaptionOrigin,
    remember_origin,
    served_facts,
)
from immich_memories.store.editorial_preparation import now

REFUSED_CODES = frozenset({401, 403})
_REFUSED_ERRORS = tuple(f"http_{code}" for code in sorted(REFUSED_CODES))
CAPTION_KEY_HINT = (
    "set advanced.editorial.preparation.caption_api_key "
    "(IMMICH_MEMORIES_EDITORIAL__PREPARATION__CAPTION_API_KEY)"
)


def _request_payload(image: bytes, *, api_model: str) -> dict[str, object]:
    return _wire_payload(image, api_model=api_model)


@dataclass(frozen=True, slots=True)
class CallOutcome:
    envelope: DescriptionEnvelope | None
    error: str | None
    elapsed_seconds: float
    finish_reason: str | None
    completion_tokens: int | None
    prompt_tokens: int | None
    raw_sha256: str | None
    raw_content: str | None = None
    request_sha256: str | None = None
    image_sha256: str | None = None


def bearer_headers(api_key: str) -> dict[str, str]:
    """The Authorization header a protected caption endpoint needs, or nothing at all.

    An empty key leaves the request exactly as it was, so an unauthenticated
    server on localhost never sees a header it would have to ignore.
    """
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _model_inventory(base_url: str, *, timeout: float, api_key: str) -> list[dict]:
    request = urllib.request.Request(f"{base_url}/models", headers=bearer_headers(api_key))  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code not in REFUSED_CODES:
            raise
        # The endpoint is there and answering; it wants a credential. Another URL is not the fix.
        raise PermissionError(
            f"caption endpoint {base_url} answered HTTP {exc.code}; {CAPTION_KEY_HINT}"
        ) from exc
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("public description endpoint returned no model inventory")
    return [row for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)]


def _ask_one(
    base_url: str, image: bytes, *, timeout: float, api_key: str, stage: str = "caption"
) -> CallOutcome:
    wire = json.dumps(_request_payload(image, api_model=API_MODEL)).encode()
    request = urllib.request.Request(  # noqa: S310
        f"{base_url}/chat/completions",
        data=wire,
        headers={"Content-Type": "application/json"} | bearer_headers(api_key),
    )
    request_sha256 = hashlib.sha256(wire).hexdigest()
    image_sha256 = hashlib.sha256(image).hexdigest()
    raw_content: str | None = None
    started = time.monotonic()
    finish_reason: str | None = None
    raw_sha256: str | None = None
    completion_tokens: int | None = None
    prompt_tokens: int | None = None
    body: object = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = json.loads(response.read())
        if not isinstance(body, dict):
            raise ValueError("caption response is not an object")
        usage = body.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        completion_tokens = _optional_int(usage.get("completion_tokens"))
        prompt_tokens = _optional_int(usage.get("prompt_tokens"))
        choice = body["choices"][0]
        finish_reason = choice.get("finish_reason")
        content = choice["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("model content is not text")
        raw_content = content
        raw_sha256 = hashlib.sha256(content.encode()).hexdigest()
        if finish_reason != "stop":
            raise ValueError(f"model finish reason is {finish_reason!r}")
        envelope = _validate_envelope(json.loads(content))
        return CallOutcome(
            envelope=envelope,
            error=None,
            elapsed_seconds=time.monotonic() - started,
            finish_reason=finish_reason,
            completion_tokens=completion_tokens,
            prompt_tokens=prompt_tokens,
            raw_sha256=raw_sha256,
            raw_content=raw_content,
            request_sha256=request_sha256,
            image_sha256=image_sha256,
        )
    except urllib.error.HTTPError as exc:
        error = f"http_{exc.code}"
        if exc.code in REFUSED_CODES:
            error = f"{error}; {CAPTION_KEY_HINT}"
    except json.JSONDecodeError:
        error = "unparsed"
    except (KeyError, TypeError, ValueError) as exc:
        error = f"invalid:{exc}"
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        error = type(exc).__name__
    finally:
        record_preparation_attempt(body, stage=stage, elapsed_seconds=time.monotonic() - started)
    return CallOutcome(
        envelope=None,
        error=error,
        elapsed_seconds=time.monotonic() - started,
        finish_reason=finish_reason,
        completion_tokens=completion_tokens,
        prompt_tokens=prompt_tokens,
        raw_sha256=raw_sha256,
        raw_content=raw_content,
        request_sha256=request_sha256,
        image_sha256=image_sha256,
    )


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def check_provider(
    base_url: str, timeout: float, check_cancelled: Callable[[], None], *, api_key: str = ""
) -> CaptionOrigin:
    """Accept the endpoint, and describe the build behind it out of its own answers.

    The alias and the URL are configuration: every shipped recipe advertises the
    same alias on the same port, so neither separates mlxcel from llama.cpp. The
    served `/models` row and the three schema controls are the two things here
    that came out of the weights. Measured against both captioners this project
    ships a recipe for, serving the same 500M model: mlxcel answers
    `owned_by=user` and no `meta`, llama.cpp answers `owned_by=llamacpp`,
    `meta.ftype=Q8_0`, `meta.n_params=409252800`, and the control digests are
    f5a1cdf9df7fe125 against 824e389c39e457a6 — each stable across repeats,
    different between the two. The digest is the load-bearing half: two builds
    that word `setting` the same way are not a harmful mix, and two that word it
    differently cannot hide behind one alias on one port.
    """
    check_cancelled()
    inventory = _model_inventory(base_url, timeout=timeout, api_key=api_key)
    served = next((row for row in inventory if row["id"] == API_MODEL), None)
    if served is None:
        raise ValueError(f"caption endpoint must advertise {API_MODEL}")
    # Preserve the accepted three schema controls before sending library previews.
    answers = []
    for rgb in ((200, 20, 20), (20, 40, 200), (128, 128, 128)):
        check_cancelled()
        buffer = io.BytesIO()
        Image.new("RGB", (400, 400), rgb).save(buffer, "JPEG", quality=90)
        control = _ask_one(
            base_url,
            tile_preview(buffer.getvalue()),
            timeout=timeout,
            api_key=api_key,
            stage="caption_controls",
        )
        if control.envelope is None:
            # A refused control says nothing about the schema; it never reached it.
            if control.error and control.error.startswith(_REFUSED_ERRORS):
                raise PermissionError(f"caption endpoint {base_url}: {control.error}")
            raise ValueError("caption endpoint failed the compact-v3 schema control")
        answers.append(control.raw_sha256 or "")
    control_digest = hashlib.sha256("|".join(answers).encode()).hexdigest()[:16]
    return CaptionOrigin(
        model_id=API_MODEL,
        endpoint=base_url,
        served=served_facts(served),
        control_digest=control_digest,
    )


def _describe(
    asset_id: str,
    *,
    preview_for: Callable[[str], bytes],
    base_url: str,
    timeout: float,
    api_key: str,
    check_cancelled: Callable[[], None],
) -> tuple[str, bytes, tuple[CallOutcome, ...]]:
    check_cancelled()
    preview = preview_for(asset_id)
    image = tile_preview(preview)
    outcomes = []
    for _attempt in range(2):
        check_cancelled()
        outcome = _ask_one(base_url, image, timeout=timeout, api_key=api_key)
        outcomes.append(outcome)
        if outcome.envelope is not None:
            break
    return asset_id, preview, tuple(outcomes)


def prepare_captions(
    *,
    connection: sqlite3.Connection,
    asset_ids: Sequence[str],
    preview_for: Callable[[str], bytes],
    base_url: str,
    timeout: float,
    concurrency: int,
    check_cancelled: Callable[[], None],
    progress: Callable[[str, int, int], None],
    api_key: str = "",
    artifact_id: str = "",
) -> dict[str, str]:
    """Bank successes and only verified two-completion failures, with bounded concurrency."""
    if not asset_ids:
        return {}
    origin = replace(
        check_provider(base_url, timeout, check_cancelled, api_key=api_key),
        artifact_id=artifact_id,
    )
    failures = {}
    # Bound submitted work too: cancellation must not drain a whole library queue.
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for start in range(0, len(asset_ids), concurrency):
            check_cancelled()
            futures = [
                pool.submit(
                    copy_context().run,
                    _describe,
                    asset_id,
                    preview_for=preview_for,
                    base_url=base_url,
                    timeout=timeout,
                    api_key=api_key,
                    check_cancelled=check_cancelled,
                )
                for asset_id in asset_ids[start : start + concurrency]
            ]
            for asset_id, future in zip(
                asset_ids[start : start + concurrency], futures, strict=True
            ):
                try:
                    _asset_id, preview, outcomes = future.result()
                    outcome = outcomes[-1]
                    if outcome.envelope is not None:
                        _remember_caption(connection, asset_id, outcome.envelope, origin)
                    elif caption_outcomes.bounded_invalid_attempts([asdict(o) for o in outcomes]):
                        row = caption_outcomes.make_unavailable(
                            asset_id, preview, [asdict(o) for o in outcomes], written_at=now()
                        )
                        caption_outcomes.remember_unavailable(connection, row, preview)
                    else:
                        failures[asset_id] = (
                            outcome.error or "caption failed without bounded completion evidence"
                        )
                except Exception as exc:
                    failures[asset_id] = f"{type(exc).__name__}: {exc}"
            progress("captions", min(start + concurrency, len(asset_ids)), len(asset_ids))
    return failures


def _remember_caption(
    connection: sqlite3.Connection,
    asset_id: str,
    envelope: DescriptionEnvelope,
    origin: CaptionOrigin | None = None,
) -> None:
    # Partial/conflicting rows are an integrity failure, never silently overwritten.
    timestamp = now()
    with connection:
        connection.execute(
            "INSERT INTO descriptions (asset_id,model,text,source,written_at) VALUES (?,?,?,?,?)",
            (asset_id, DESCRIPTION_MODEL, envelope.description, DESCRIPTION_SOURCE, timestamp),
        )
        connection.execute(
            "INSERT INTO description_fields (asset_id,model,field,value,written_at) VALUES (?,?,?,?,?)",
            (asset_id, DESCRIPTION_MODEL, "setting", envelope.setting, timestamp),
        )
        if origin is not None:
            remember_origin(connection, asset_id, DESCRIPTION_MODEL, origin)
