"""Real caption orchestration with synthetic responses, including durable failures."""

import http.server
import io
import json
import sqlite3
import threading
from dataclasses import asdict

import pytest
from PIL import Image

from immich_memories.analysis import editorial_preparation_captions as captions
from immich_memories.analysis.editorial_description_contract import (
    API_MODEL,
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
        timeout=5,
        concurrency=2,
        check_cancelled=check_cancelled,
        progress=lambda *_: None,
        **{"base_url": "http://localhost:8092/v1", **kwargs},
    )


def test_two_actual_invalid_completions_become_bound_unavailable(monkeypatch):
    calls = []
    monkeypatch.setattr(captions, "check_provider", lambda *_, **__: None)

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
    monkeypatch.setattr(captions, "check_provider", lambda *_, **__: None)
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
    monkeypatch.setattr(captions, "check_provider", lambda *_, **__: None)
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
    monkeypatch.setattr(captions, "check_provider", lambda *_, **__: None)
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


class _CaptionServer:
    """A caption endpoint, optionally gated on a bearer token, that records what it was sent."""

    def __init__(self, token: str | None, *, open_probe: bool = False) -> None:
        self.seen_authorization: list[str | None] = []
        inventory = json.dumps(
            {"data": [{"id": API_MODEL, "revision": "served-revision"}]}
        ).encode()
        completion = json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {"description": "Two people walk a dog.", "setting": "a park"}
                            )
                        },
                    }
                ],
                "usage": {"completion_tokens": 22, "prompt_tokens": 310},
            }
        ).encode()
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self, body: bytes) -> None:
                server.seen_authorization.append(self.headers.get("Authorization"))
                if token is not None and self.headers.get("Authorization") != f"Bearer {token}":
                    server.unauthorized += 1
                    self.send_error(401, "Unauthorized")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's name
                if open_probe:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(inventory)))
                    self.end_headers()
                    self.wfile.write(inventory)
                    return
                self._answer(inventory)

            def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's name
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self._answer(completion)

            def log_message(self, *args: object) -> None:
                return

        self.unauthorized = 0
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/v1"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def token_gated_endpoint():
    server = _CaptionServer("caption-token")
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def open_endpoint():
    server = _CaptionServer(None)
    try:
        yield server
    finally:
        server.close()


def test_configured_key_authorizes_the_probe_and_the_completion(token_gated_endpoint):
    with sqlite3.connect(":memory:") as connection:
        initialize(connection)
        failures = run(
            connection,
            base_url=token_gated_endpoint.base_url,
            api_key="caption-token",
        )

        assert failures == {}
        assert token_gated_endpoint.unauthorized == 0
        assert connection.execute("SELECT text FROM descriptions").fetchone() == (
            "Two people walk a dog.",
        )


def test_no_configured_key_leaves_the_request_headers_untouched(open_endpoint):
    with sqlite3.connect(":memory:") as connection:
        initialize(connection)
        failures = run(connection, base_url=open_endpoint.base_url)

        assert failures == {}
        assert connection.execute("SELECT count(*) FROM descriptions").fetchone() == (1,)
    assert set(open_endpoint.seen_authorization) == {None}


def test_each_caption_keeps_the_served_model_and_declared_artifact(open_endpoint):
    from immich_memories.store.caption_provenance import origins_for

    with sqlite3.connect(":memory:") as connection:
        initialize(connection)
        assert (
            run(
                connection,
                base_url=open_endpoint.base_url,
                artifact_id="SmolVLM2-Q8_0@revision-one",
            )
            == {}
        )
        origin = origins_for(connection, ("a",), DESCRIPTION_MODEL)["a"]

    assert origin["model_id"] == API_MODEL
    assert origin["endpoint"] == open_endpoint.base_url
    assert origin["artifact_id"] == "SmolVLM2-Q8_0@revision-one"
    assert origin["reported_build"] == {"revision": "served-revision"}
    assert "caption-token" not in json.dumps(origin)


def test_a_refused_probe_names_the_setting_that_carries_the_token(token_gated_endpoint):
    with sqlite3.connect(":memory:") as connection:
        initialize(connection)
        with pytest.raises(PermissionError) as refused:
            run(connection, base_url=token_gated_endpoint.base_url)

    assert "401" in str(refused.value)
    assert "caption_api_key" in str(refused.value)


def test_a_gate_on_completions_alone_still_names_the_setting():
    """Plenty of gateways leave `/models` open and charge for inference."""
    server = _CaptionServer("caption-token", open_probe=True)
    try:
        with sqlite3.connect(":memory:") as connection:
            initialize(connection)
            with pytest.raises(PermissionError) as refused:
                run(connection, base_url=server.base_url)
    finally:
        server.close()

    assert "401" in str(refused.value)
    assert "caption_api_key" in str(refused.value)
