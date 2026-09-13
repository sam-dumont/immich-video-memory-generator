"""`immich-memories models fetch` against a local HTTP fixture."""

from __future__ import annotations

import hashlib
import http.server
import sys
import threading
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
from click.testing import CliRunner, Result

from immich_memories.analysis.editorial_preparation_detectors import (
    DOCLING_REPO,
    DOCLING_REVISION,
)
from immich_memories.cli import main, models_cmd
from immich_memories.config_loader import Config
from immich_memories.pinned_models import fetch_pinned_model


class _Fixture:
    """A throwaway HTTP server that serves one body and counts its requests."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.requests = 0
        fixture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's name
                fixture.requests += 1
                self.send_response(200)
                self.send_header("Content-Length", str(len(fixture.body)))
                self.end_headers()
                self.wfile.write(fixture.body)

            def log_message(self, *args: object) -> None:
                return

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/dinov2-small.onnx"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


EXPORT = b"onnx-graph-bytes" * 64
EXPORT_SHA256 = hashlib.sha256(EXPORT).hexdigest()


@pytest.fixture
def served() -> Iterator[_Fixture]:
    fixture = _Fixture(EXPORT)
    try:
        yield fixture
    finally:
        fixture.close()


def test_fetch_writes_the_export_only_when_the_digest_matches(
    served: _Fixture, tmp_path: Path
) -> None:
    destination = tmp_path / "models" / "dinov2-small.onnx"

    outcome = fetch_pinned_model(url=served.url, destination=destination, sha256=EXPORT_SHA256)

    assert outcome == "downloaded"
    assert destination.read_bytes() == EXPORT


def test_a_wrong_digest_leaves_nothing_on_disk(served: _Fixture, tmp_path: Path) -> None:
    destination = tmp_path / "models" / "dinov2-small.onnx"

    with pytest.raises(ValueError, match="is not the pinned"):
        fetch_pinned_model(url=served.url, destination=destination, sha256="0" * 64)

    assert not destination.exists()
    assert list(destination.parent.iterdir()) == []


def test_an_existing_export_is_a_no_op_until_forced(served: _Fixture, tmp_path: Path) -> None:
    destination = tmp_path / "dinov2-small.onnx"
    destination.write_bytes(EXPORT)

    assert (
        fetch_pinned_model(url=served.url, destination=destination, sha256=EXPORT_SHA256)
        == "present"
    )
    assert served.requests == 0

    forced = fetch_pinned_model(
        url=served.url, destination=destination, sha256=EXPORT_SHA256, force=True
    )

    assert forced == "downloaded"
    assert served.requests == 1


def test_a_body_over_the_cap_is_refused(tmp_path: Path) -> None:
    fixture = _Fixture(b"x" * 4096)
    try:
        destination = tmp_path / "dinov2-small.onnx"
        with pytest.raises(ValueError, match="refused past"):
            fetch_pinned_model(
                url=fixture.url,
                destination=destination,
                sha256=EXPORT_SHA256,
                max_bytes=1024,
            )
    finally:
        fixture.close()

    assert not destination.exists()


def _invoke(args: list[str], config: Config) -> Result:
    runner = CliRunner()
    # WHY: both boundaries reach the developer's own home directory.
    with (
        # WHY: init_config_dir writes into the real ~/.immich-memories.
        patch("immich_memories.cli.init_config_dir"),
        # WHY: get_config reads the developer's own config file.
        patch("immich_memories.cli.get_config", return_value=config),
    ):
        return runner.invoke(main, args, catch_exceptions=False)


def test_models_fetch_lands_the_configured_path_from_the_configured_url(
    served: _Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "models" / "dinov2-small.onnx"
    # WHY: the pinned 88 MB export cannot live in the repo, so the fixture's own
    # digest stands in for it; everything else is the production command.
    monkeypatch.setattr(models_cmd, "ENCODER", replace(models_cmd.ENCODER, sha256=EXPORT_SHA256))
    config = Config(triage={"encoder": str(destination), "encoder_url": served.url})

    # WHY: --no-detectors keeps this test on the encoder. Warming the detectors
    # needs huggingface-hub, which the editorial extra carries and CI does not
    # install, so the default path would exit 1 before the encoder is checked.
    result = _invoke(["models", "fetch", "--no-detectors"], config)

    assert result.exit_code == 0
    assert destination.read_bytes() == EXPORT
    assert str(destination) in result.output


def test_models_fetch_refuses_an_export_that_is_not_the_pinned_one(
    served: _Fixture, tmp_path: Path
) -> None:
    destination = tmp_path / "models" / "dinov2-small.onnx"
    config = Config(triage={"encoder": str(destination), "encoder_url": served.url})

    result = _invoke(["models", "fetch"], config)

    assert result.exit_code == 1
    assert not destination.exists()
    assert "not the pinned" in result.output


def _recording_hub(calls: list[tuple[str, str, str]]) -> ModuleType:
    module = ModuleType("huggingface_hub")

    def hf_hub_download(repo_id: str, filename: str, *, revision: str, **kwargs: object) -> str:
        calls.append((repo_id, revision, filename))
        return f"/cache/{repo_id}/{filename}"

    module.hf_hub_download = hf_hub_download  # type: ignore[attr-defined]
    return module


def _pinned_everywhere(served: _Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """One config whose every pinned artifact resolves to the local fixture.

    WHY: the real pins are an 88 MB encoder and a 22.5 MB detector export that
    cannot live in the repo, so the fixture's own digest stands in for both.
    """
    monkeypatch.setattr(models_cmd, "ENCODER", replace(models_cmd.ENCODER, sha256=EXPORT_SHA256))
    monkeypatch.setattr(
        models_cmd, "MARQO_ONNX", replace(models_cmd.MARQO_ONNX, sha256=EXPORT_SHA256)
    )
    return Config(
        triage={"encoder": str(tmp_path / "dinov2.onnx"), "encoder_url": served.url},
        editorial={
            "preparation": {
                "marqo_onnx": str(tmp_path / "nsfw-marqo-384.onnx"),
                "marqo_onnx_url": served.url,
            }
        },
    )


def test_models_fetch_lands_every_artifact_a_first_run_needs(
    served: _Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str, str]] = []
    # WHY: hf_hub_download reaches Hugging Face, and huggingface-hub is an
    # `editorial`-extra dependency the unit environment does not install.
    monkeypatch.setitem(sys.modules, "huggingface_hub", _recording_hub(calls))
    config = _pinned_everywhere(served, tmp_path, monkeypatch)

    result = _invoke(["models", "fetch"], config)

    assert result.exit_code == 0
    assert (tmp_path / "dinov2.onnx").read_bytes() == EXPORT
    assert (tmp_path / "nsfw-marqo-384.onnx").read_bytes() == EXPORT
    assert calls == [(DOCLING_REPO, DOCLING_REVISION, "model.onnx")]


def test_models_fetch_refuses_a_detector_export_that_is_not_the_pinned_one(
    served: _Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(models_cmd, "ENCODER", replace(models_cmd.ENCODER, sha256=EXPORT_SHA256))
    config = Config(
        triage={"encoder": str(tmp_path / "dinov2.onnx"), "encoder_url": served.url},
        editorial={
            "preparation": {
                "marqo_onnx": str(tmp_path / "nsfw-marqo-384.onnx"),
                "marqo_onnx_url": served.url,
            }
        },
    )

    result = _invoke(["models", "fetch"], config)

    assert result.exit_code == 1
    assert not (tmp_path / "nsfw-marqo-384.onnx").exists()
    assert "nsfw_marqo" in result.output
    assert "not the pinned" in result.output


def test_no_detectors_leaves_the_detector_artifacts_alone(
    served: _Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str, str]] = []
    # WHY: the same two boundaries as above — the Hub and the optional extra.
    monkeypatch.setitem(sys.modules, "huggingface_hub", _recording_hub(calls))
    config = _pinned_everywhere(served, tmp_path, monkeypatch)

    result = _invoke(["models", "fetch", "--no-detectors"], config)

    assert result.exit_code == 0
    assert calls == []
    assert not (tmp_path / "nsfw-marqo-384.onnx").exists()
