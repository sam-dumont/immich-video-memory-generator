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
from immich_memories.analysis.llm_metrics import collecting
from immich_memories.analysis.llm_usage_record import USAGE_FILE, write_llm_usage
from immich_memories.operations.cancellation import (
    PipelineCancelled,
    cancellation_scope,
    check_cancelled,
)
from immich_memories.operations.caption_origins import caption_origin_summary
from immich_memories.store.caption_provenance import CaptionOrigin, origins_for
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


def _probed():
    """What an accepted probe hands back, for tests that replace the probe itself."""
    return CaptionOrigin(model_id=API_MODEL, endpoint="http://localhost:8092/v1")


def run(connection, **kwargs):
    return captions.prepare_captions(
        connection=connection,
        preview_for=lambda _: preview(),
        timeout=5,
        concurrency=2,
        check_cancelled=check_cancelled,
        progress=lambda *_: None,
        **{"base_url": "http://localhost:8092/v1", "asset_ids": ("a",), **kwargs},
    )


def test_two_actual_invalid_completions_become_bound_unavailable(monkeypatch):
    calls = []
    monkeypatch.setattr(captions, "check_provider", lambda *_, **__: _probed())

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
    monkeypatch.setattr(captions, "check_provider", lambda *_, **__: _probed())
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
    monkeypatch.setattr(captions, "check_provider", lambda *_, **__: _probed())
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
    monkeypatch.setattr(captions, "check_provider", lambda *_, **__: _probed())
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


def test_controls_and_worker_captions_reach_attempt_and_run_totals(open_endpoint):
    with sqlite3.connect(":memory:") as connection:
        initialize(connection)
        with collecting() as run_usage, collecting() as attempt_usage:
            assert run(connection, base_url=open_endpoint.base_url) == {}

    for usage in (attempt_usage, run_usage):
        assert usage.calls == 4
        assert usage.prompt_tokens == 4 * 310
        assert usage.completion_tokens == 4 * 22


def test_usage_report_separates_controls_from_captions(tmp_path, open_endpoint):
    with sqlite3.connect(":memory:") as connection:
        initialize(connection)
        with collecting() as usage:
            assert run(connection, base_url=open_endpoint.base_url) == {}
    write_llm_usage(tmp_path, usage)

    report = json.loads((tmp_path / USAGE_FILE).read_text())
    assert report["by_stage"]["caption_controls"]["calls"] == 3
    assert report["by_stage"]["caption"]["calls"] == 1
    assert report["by_model"]["served-caption-revision"]["calls"] == 4


def test_invalid_reply_is_counted_and_missing_usage_is_not_reported_as_free(tmp_path):
    reply = {
        "model": "served-caption-revision",
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
        "usage": {"prompt_tokens": 310, "completion_tokens": 22},
    }
    invalid = {**reply, "choices": [{"finish_reason": "stop", "message": {"content": "bad JSON"}}]}
    server = _CaptionServer(None, replies=[reply] * 3 + [invalid, {**reply, "usage": None}])
    try:
        with sqlite3.connect(":memory:") as connection:
            initialize(connection)
            with collecting() as usage:
                assert run(connection, base_url=server.base_url) == {}
        write_llm_usage(tmp_path, usage)
    finally:
        server.close()

    report = json.loads((tmp_path / USAGE_FILE).read_text())
    assert report["calls"] == 5
    assert report["prompt_tokens"] == 4 * 310
    assert report["completion_tokens"] == 4 * 22
    assert report["unmetered_calls"] == 1
    assert report["usage_complete"] is False
    assert report["by_stage"]["caption"]["calls"] == 2
    assert report["by_stage"]["caption"]["unmetered_calls"] == 1


class _CaptionServer:
    """A caption endpoint, optionally gated on a bearer token, that records what it was sent."""

    def __init__(
        self,
        token: str | None,
        *,
        open_probe: bool = False,
        served: dict | None = None,
        answer: dict | None = None,
        replies: list[dict] | None = None,
    ) -> None:
        self.seen_authorization: list[str | None] = []
        inventory = json.dumps(
            {"data": [{"id": API_MODEL} | (served or {"owned_by": "served-revision"})]}
        ).encode()
        completion = json.dumps(
            {
                "model": "served-caption-revision",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                answer
                                or {"description": "Two people walk a dog.", "setting": "a park"}
                            )
                        },
                    }
                ],
                "usage": {"completion_tokens": 22, "prompt_tokens": 310},
            }
        ).encode()
        completions = [json.dumps(reply).encode() for reply in replies] if replies else [completion]
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
                self._answer(completions.pop(0) if len(completions) > 1 else completions[0])

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


def test_each_caption_keeps_what_the_endpoint_said_about_its_weights(open_endpoint):
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
        origin = origins_for(connection, ("a",), DESCRIPTION_MODEL)["origins"][0]

    assert origin["model_id"] == API_MODEL
    assert origin["endpoint"] == open_endpoint.base_url
    assert origin["artifact_id"] == "SmolVLM2-Q8_0@revision-one"
    assert origin["served"] == {"owned_by": "served-revision"}
    assert len(origin["control_digest"]) == 16
    assert "caption-token" not in json.dumps(origin)


def test_two_captioners_behind_one_alias_leave_two_origins_in_the_bank():
    """The failure #933 names: one bank, two builds, nothing marking the seam.

    Both servers advertise the required alias, so neither the model id nor the
    URL can separate them; what separates them is what they answered.
    """
    mlx = _CaptionServer(
        None,
        served={"owned_by": "user"},
        answer={"description": "Two people walk a dog.", "setting": "a park"},
    )
    gguf = _CaptionServer(
        None,
        served={
            "aliases": [API_MODEL],
            "owned_by": "llamacpp",
            "meta": {"ftype": "Q8_0", "n_params": 409252800},
        },
        answer={"description": "A dog crosses grass.", "setting": "an open field"},
    )
    try:
        with sqlite3.connect(":memory:") as connection:
            initialize(connection)
            assert run(connection, asset_ids=("a",), base_url=mlx.base_url) == {}
            assert run(connection, asset_ids=("b",), base_url=gguf.base_url) == {}
            grouped = origins_for(connection, ("a", "b"), DESCRIPTION_MODEL)
    finally:
        mlx.close()
        gguf.close()

    first, second = grouped["origins"]
    assert [first["assets"], second["assets"]] == [1, 1]
    assert first["served"] != second["served"]
    assert first["control_digest"] != second["control_digest"]
    # One asset is on the leading origin and goes unnamed; the other must be named.
    ((named, index),) = grouped["by_asset"].items()
    assert index == 1
    assert named in {"a", "b"}
    summary = caption_origin_summary(grouped)
    assert "2 distinct over 2 captions MIXED" in summary
    assert f"aliases={API_MODEL}, meta.ftype=Q8_0" in summary
    assert "meta.n_params=409252800, owned_by=llamacpp" in summary
    assert "owned_by=user" in summary


def test_the_same_server_probed_twice_is_the_same_origin(open_endpoint):
    """Origins are only a mixed-bank signal while one server keeps answering as one."""
    probe = (open_endpoint.base_url, 5, check_cancelled)
    first, second = captions.check_provider(*probe), captions.check_provider(*probe)

    assert first == second
    assert len({first, second}) == 1


def test_an_endpoint_advertising_another_model_is_refused():
    server = _CaptionServer(None, served={"id": "some-other-captioner"})
    try:
        with sqlite3.connect(":memory:") as connection, pytest.raises(ValueError) as refused:
            initialize(connection)
            run(connection, base_url=server.base_url)
    finally:
        server.close()

    assert API_MODEL in str(refused.value)


def test_a_run_with_no_pictures_at_all_groups_nothing():
    with sqlite3.connect(":memory:") as connection:
        initialize(connection)
        assert origins_for(connection, (), DESCRIPTION_MODEL) == {}


def test_one_captioner_over_two_pictures_is_one_origin_and_names_no_asset(open_endpoint):
    with sqlite3.connect(":memory:") as connection:
        initialize(connection)
        assert run(connection, asset_ids=("a", "b"), base_url=open_endpoint.base_url) == {}
        grouped = origins_for(connection, ("a", "b"), DESCRIPTION_MODEL)

    assert grouped["by_asset"] == {}
    assert [origin["assets"] for origin in grouped["origins"]] == [2]
    assert "1 distinct over 2 captions [" in caption_origin_summary(grouped)


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
