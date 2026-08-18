"""Phase-1 thumbnail prefetch: de-dup must work without the UI's pre-cached previews."""

from __future__ import annotations

import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

try:
    import cv2
except ImportError:
    pytest.skip("cv2 not available", allow_module_level=True)

from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline
from immich_memories.api.immich import SyncImmichClient
from immich_memories.cache.thumbnail_cache import ThumbnailCache
from immich_memories.config_loader import Config
from immich_memories.config_models import AnalysisConfig
from tests.conftest import make_clip


def _jpeg(split: str) -> bytes:
    """A 64x64 half-black/half-white JPEG; the split axis makes distinct hashes."""
    img = np.zeros((64, 64, 3), dtype=np.uint8)
    if split == "vertical":
        img[:, 32:] = 255
    else:
        img[32:, :] = 255
    ok, encoded = cv2.imencode(".jpg", img)
    assert ok
    return encoded.tobytes()


class _ThumbnailServer:
    """Localhost stand-in for the Immich thumbnail endpoint (the read boundary)."""

    def __init__(
        self,
        thumbnails: dict[str, bytes],
        failing: set[str] | None = None,
        latency: float = 0.0,
    ) -> None:
        self.thumbnails = thumbnails
        self.failing = failing or set()
        self.latency = latency
        self.requested: list[str] = []
        self._lock = threading.Lock()
        self._in_flight = 0
        self.max_in_flight = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parts = self.path.split("?")[0].strip("/").split("/")
                asset_id = parts[2] if len(parts) == 4 and parts[3] == "thumbnail" else None
                with server._lock:
                    server._in_flight += 1
                    server.max_in_flight = max(server.max_in_flight, server._in_flight)
                    if asset_id:
                        server.requested.append(asset_id)
                try:
                    time.sleep(server.latency)
                    if asset_id in server.failing or asset_id not in server.thumbnails:
                        # 404 is non-retryable, so failures stay fast in tests.
                        self.send_response(404)
                        self.end_headers()
                    else:
                        body = server.thumbnails[asset_id]
                        self.send_response(200)
                        self.send_header("Content-Type", "image/jpeg")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                finally:
                    with server._lock:
                        server._in_flight -= 1

            def log_message(self, _format: str, *args: object) -> None:
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        host, port = self._httpd.server_address[:2]
        self.base_url = f"http://{host}:{port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture()
def thumbnail_server():
    server = _ThumbnailServer(
        {
            "dup-a": _jpeg("vertical"),
            "dup-b": _jpeg("vertical"),
            "other": _jpeg("horizontal"),
        }
    )
    yield server
    server.close()


def _cli_pipeline(
    server: _ThumbnailServer, cache_dir: Path, analysis_config: AnalysisConfig | None = None
) -> SmartPipeline:
    """Build the pipeline the way the CLI does: fresh, empty thumbnail cache."""
    # WHY: the analysis DB is a write boundary; returning no cached analysis
    # keeps planning on the metadata fallback path.
    analysis_cache = MagicMock()
    analysis_cache.get_analysis.return_value = None
    client = SyncImmichClient(base_url=server.base_url, api_key="test-key", timeout=5.0)
    return SmartPipeline(
        client=client,
        analysis_cache=analysis_cache,
        thumbnail_cache=ThumbnailCache(cache_dir=cache_dir),
        config=PipelineConfig(target_clips=10, avg_clip_duration=5.0),
        analysis_config=analysis_config or AnalysisConfig(),
        app_config=Config(),
    )


def test_cli_path_deduplicates_near_duplicates_without_precached_thumbnails(
    thumbnail_server, tmp_path
):
    clips = [make_clip("dup-a"), make_clip("dup-b"), make_clip("other")]
    pipeline = _cli_pipeline(thumbnail_server, tmp_path / "thumbs")

    planned = pipeline.run_planning_analysis(clips)

    kept = {entry.clip.asset.id for entry in planned}
    assert "other" in kept
    assert len(kept & {"dup-a", "dup-b"}) == 1
    assert sorted(thumbnail_server.requested) == ["dup-a", "dup-b", "other"]

    pipeline.run_planning_analysis(clips)
    assert len(thumbnail_server.requested) == 3, "second run must reuse the cache"


def test_thumbnail_fetch_failure_degrades_gracefully(thumbnail_server, tmp_path, caplog):
    thumbnail_server.failing.add("other")
    clips = [make_clip("dup-a"), make_clip("dup-b"), make_clip("other")]
    pipeline = _cli_pipeline(thumbnail_server, tmp_path / "thumbs")

    with caplog.at_level(logging.WARNING, logger="immich_memories.analysis"):
        planned = pipeline.run_planning_analysis(clips)

    kept = {entry.clip.asset.id for entry in planned}
    assert "other" in kept
    assert len(kept & {"dup-a", "dup-b"}) == 1
    assert any("1 of 3 thumbnails could not be fetched" in rec.message for rec in caplog.records)


def test_thumbnails_are_fetched_with_bounded_workers(tmp_path):
    server = _ThumbnailServer({f"clip-{i}": _jpeg("vertical") for i in range(6)}, latency=0.1)
    try:
        pipeline = _cli_pipeline(server, tmp_path / "thumbs", AnalysisConfig(download_workers=2))
        pipeline.run_planning_analysis([make_clip(f"clip-{i}") for i in range(6)])
    finally:
        server.close()

    assert 1 < server.max_in_flight <= 2
