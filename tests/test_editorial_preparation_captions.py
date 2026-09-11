"""Real caption orchestration with synthetic responses, including durable failures."""

import io
import sqlite3
from dataclasses import asdict

import pytest
from PIL import Image

from immich_memories.analysis import editorial_preparation_captions as captions
from immich_memories.analysis.editorial_description_contract import (
    DESCRIPTION_MODEL,
    validate_envelope,
)
from immich_memories.analysis.editorial_description_outcomes import digest, unavailable_for
from immich_memories.analysis.editorial_description_wire import request_bytes, tile_preview
from immich_memories.operations.cancellation import (
    PipelineCancelled,
    cancellation_scope,
    check_cancelled,
)
from immich_memories.store.editorial_preparation import initialize


def preview():
    out = io.BytesIO()
    Image.new("RGB", (100, 80), (110, 70, 30)).save(out, "JPEG")
    return out.getvalue()


def outcome(image, *, error="invalid:bad JSON", finish_reason="stop", envelope=None):
    raw = "not-json"
    return captions.CallOutcome(
        envelope=envelope,
        error=error,
        elapsed_seconds=0.1,
        finish_reason=finish_reason,
        completion_tokens=4,
        prompt_tokens=30,
        raw_sha256=digest(raw.encode()),
        raw_content=raw,
        request_sha256=digest(request_bytes(image)),
        image_sha256=digest(image),
    )


def run(connection, **kwargs):
    return captions.prepare_captions(
        connection=connection,
        asset_ids=("a",),
        preview_for=lambda _: preview(),
        base_url="http://localhost:8092/v1",
        timeout=1,
        concurrency=2,
        check_cancelled=check_cancelled,
        progress=lambda *_: None,
        **kwargs,
    )


def test_two_actual_invalid_completions_become_bound_unavailable(monkeypatch):
    calls = []
    monkeypatch.setattr(captions, "check_provider", lambda *_: None)

    def ask(_url, image, **_kwargs):
        calls.append(image)
        return outcome(image)

    monkeypatch.setattr(captions, "_ask_one", ask)
    with sqlite3.connect(":memory:") as connection:
        initialize(connection)
        assert run(connection) == {}
        assert len(calls) == 2
        found = unavailable_for(connection, ("a",), preview_for=lambda _: preview())
        assert tuple(found) == ("a",)
        assert connection.execute("SELECT count(*) FROM descriptions").fetchone() == (0,)


def test_transport_failures_never_fabricate_terminal_unavailable(monkeypatch):
    monkeypatch.setattr(captions, "check_provider", lambda *_: None)
    monkeypatch.setattr(
        captions,
        "_ask_one",
        lambda _url, image, **_: outcome(image, error="TimeoutError", finish_reason=None),
    )
    with sqlite3.connect(":memory:") as connection:
        initialize(connection)
        assert run(connection) == {"a": "TimeoutError"}
        assert unavailable_for(connection, ("a",), preview_for=lambda _: preview()) == {}


def test_success_uses_exact_wire_and_writes_only_description_and_setting(monkeypatch):
    monkeypatch.setattr(captions, "check_provider", lambda *_: None)
    envelope = validate_envelope({"description": "People sit together.", "setting": "a room"})

    def ask(_url, image, **_):
        assert image == tile_preview(preview())
        return outcome(image, envelope=envelope, error=None)

    monkeypatch.setattr(captions, "_ask_one", ask)
    with sqlite3.connect(":memory:") as connection:
        initialize(connection)
        assert run(connection) == {}
        assert connection.execute("SELECT model,text FROM descriptions").fetchone() == (
            DESCRIPTION_MODEL,
            "People sit together.",
        )
        assert connection.execute("SELECT field,value FROM description_fields").fetchall() == [
            ("setting", "a room")
        ]


def test_thread_worker_inherits_cancellation_context_and_does_not_retry(monkeypatch):
    monkeypatch.setattr(captions, "check_provider", lambda *_: None)
    requested = []

    def ask(_url, image, **_):
        requested.append(image)
        return outcome(image)

    monkeypatch.setattr(captions, "_ask_one", ask)

    def check():
        if requested:
            raise PipelineCancelled()

    with sqlite3.connect(":memory:") as connection:
        initialize(connection)
        with pytest.raises(PipelineCancelled), cancellation_scope(check):
            run(connection)
    assert len(requested) == 1


def test_actual_call_outcome_keys_match_durable_failure_contract():
    row = asdict(outcome(tile_preview(preview())))
    assert len(row) == 10
