"""Tests for provider preflight diagnostics."""

from __future__ import annotations

import hashlib
import http.server
import json
import platform
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

from immich_memories.analysis import editorial_preparation_detectors as detectors
from immich_memories.analysis.editorial_description_contract import API_MODEL
from immich_memories.api.immich import ImmichAPIError
from immich_memories.config_loader import Config
from immich_memories.preflight import (
    CAPTION_SETUP_PAGE,
    CheckResult,
    CheckStatus,
    check_caption_endpoint,
    check_host_paths,
    check_immich,
    check_llm,
    check_notifications,
    check_title_rendering,
    run_preflight_checks,
)
from immich_memories.preflight_run import check_detector_export, check_encoder


def test_immich_api_error_returns_sanitized_diagnostic_result() -> None:
    api_key = "preflight-api-secret"
    config = Config(immich={"url": "https://immich.example.com", "api_key": api_key})
    error = ImmichAPIError(
        f"upstream rejected {api_key}",
        status_code=400,
        correlation_id="corr-123",
        details={"requestHeaders": {"x-api-key": api_key}},
    )
    # WHY: replace the external Immich connection while exercising real preflight mapping.
    client = MagicMock()
    client.__enter__.return_value = client
    client.get_current_user.side_effect = error

    with patch("immich_memories.api.immich.SyncImmichClient", return_value=client):
        result = check_immich(config)

    assert result == CheckResult(
        name="Immich",
        status=CheckStatus.ERROR,
        message="Connection failed",
        details="upstream rejected ***; HTTP 400; Correlation ID: corr-123",
    )
    assert api_key not in result.message
    assert api_key not in (result.details or "")
    assert "requestHeaders" not in (result.details or "")


def test_immich_connection_error_does_not_expose_configured_api_key() -> None:
    api_key = "connection-api-secret"
    config = Config(immich={"url": "https://immich.example.com", "api_key": api_key})
    # WHY: replace the external Immich connection with a credential-bearing transport error.
    client = MagicMock()
    client.__enter__.side_effect = OSError(f"connection rejected {api_key}")

    with patch("immich_memories.api.immich.SyncImmichClient", return_value=client):
        result = check_immich(config)

    assert result.status is CheckStatus.ERROR
    assert result.message == "Connection failed"
    assert result.details == "connection rejected ***"
    assert api_key not in (result.details or "")


def test_llm_preflight_reports_missing_configured_model() -> None:
    """A model-specific 404 is not mislabeled as a generic connection error."""
    config = Config(
        llm={
            "provider": "openai-compatible",
            "base_url": "http://localhost:9999/v1",
            "model": "removed-vlm",
        },
    )
    response = MagicMock()
    response.status_code = 404
    response.json.return_value = {"error": {"message": "model removed-vlm not found"}}
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.return_value = response

    with patch("immich_memories.preflight.httpx.Client", return_value=client):
        result = check_llm(config)

    assert result.status is CheckStatus.WARNING
    assert result.message == "Configured model unavailable: removed-vlm"
    assert "model removed-vlm not found" not in (result.details or "")
    assert "localhost:9999" not in (result.details or "")


def test_llm_preflight_reports_missing_chat_route() -> None:
    """A route-level 404 remains distinct from a removed model."""
    config = Config(
        llm={
            "provider": "openai-compatible",
            "base_url": "http://localhost:9999/v1",
            "model": "vlm",
        },
    )
    response = MagicMock()
    response.status_code = 404
    response.json.return_value = {}
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.return_value = response

    with patch("immich_memories.preflight.httpx.Client", return_value=client):
        result = check_llm(config)

    assert result.status is CheckStatus.WARNING
    assert result.message == "Chat-completions route unavailable"
    assert "localhost:9999" not in (result.details or "")


def _messages_host(models=None, probe_status=200, probe_body=None):
    """A Messages API host: what it lists, and what a one-token ask gets back."""
    # WHY: the LLM server is the external boundary; this stands in for its two routes.
    listing = MagicMock()
    listing.status_code = 200 if models is not None else 404
    listing.json.return_value = {"data": models} if models is not None else {}
    probe = MagicMock()
    probe.status_code = probe_status
    probe.json.return_value = probe_body or {"content": [{"type": "text", "text": "h"}]}
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.get.return_value = listing
    client.post.return_value = probe
    return client


def test_llm_preflight_reports_the_model_list_an_anthropic_host_publishes() -> None:
    config = Config(llm={"provider": "anthropic", "model": "claude-sonnet-4-5", "api_key": "k"})
    client = _messages_host([{"id": "claude-sonnet-4-5"}, {"id": "claude-opus-4-1"}])

    with patch("immich_memories.preflight.httpx.Client", return_value=client):
        result = check_llm(config)

    assert result.status is CheckStatus.OK
    assert result.message == "Connected (anthropic, 2 models)"
    assert client.get.call_args[0][0] == "https://api.anthropic.com/v1/models"
    client.post.assert_not_called()


def test_llm_preflight_names_a_model_the_anthropic_host_does_not_serve() -> None:
    config = Config(llm={"provider": "anthropic", "model": "claude-retired", "api_key": "k"})
    client = _messages_host([{"id": "claude-sonnet-4-5"}])

    with patch("immich_memories.preflight.httpx.Client", return_value=client):
        result = check_llm(config)

    assert result.status is CheckStatus.WARNING
    assert result.message == "Connected but missing model: claude-retired"


def test_llm_preflight_falls_back_to_one_token_where_no_catalogue_is_served() -> None:
    """A gateway that serves only /v1/messages is still reachable, and says so."""
    config = Config(
        llm={
            "provider": "anthropic",
            "base_url": "https://gateway.example.invalid/anthropic",
            "model": "some-model",
            "api_key": "k",
        }
    )
    client = _messages_host(models=None)

    with patch("immich_memories.preflight.httpx.Client", return_value=client):
        result = check_llm(config)

    assert result.status is CheckStatus.OK
    assert result.message == "Connected (anthropic)"
    (url,) = client.post.call_args[0]
    assert url == "https://gateway.example.invalid/anthropic/v1/messages"
    assert client.post.call_args[1]["json"]["max_tokens"] == 1


def test_llm_preflight_reports_a_rejected_key_on_the_messages_route() -> None:
    config = Config(llm={"provider": "anthropic", "model": "claude-sonnet-4-5", "api_key": "bad"})
    client = _messages_host(models=None, probe_status=401, probe_body={"error": {"type": "auth"}})

    with patch("immich_memories.preflight.httpx.Client", return_value=client):
        result = check_llm(config)

    assert result.status is CheckStatus.ERROR
    assert result.message == "Authentication failed"


def test_llm_preflight_checks_the_route_a_named_preset_actually_uses() -> None:
    """`zai` resolves to the Messages API, so the check must not probe the other one."""
    config = Config(llm={"provider": "zai", "model": "glm-5.3-flash", "api_key": "k"})
    client = _messages_host([{"id": "glm-5.3-flash"}])

    with patch("immich_memories.preflight.httpx.Client", return_value=client):
        result = check_llm(config)

    assert result.status is CheckStatus.OK
    assert client.get.call_args[0][0] == "https://api.z.ai/api/anthropic/v1/models"


def test_notification_preflight_warns_on_sanitized_failure_cooldown(tmp_path) -> None:
    from immich_memories.automation.notification_state import (
        NotificationFailureCategory,
        NotificationStateStore,
    )

    credential_url = "https://notify.test/provider-secret"
    config = Config(
        cache={"database": str(tmp_path / "preflight.db")},
        notifications={"enabled": True, "urls": [credential_url], "cooldown_hours": 24},
    )
    NotificationStateStore(config.cache.database_path).record_failure(
        NotificationFailureCategory.QUOTA
    )

    result = check_notifications(config)

    assert result.status is CheckStatus.WARNING
    assert result.message == "Delivery paused after quota failure"
    assert "24h" in (result.details or "")
    assert credential_url not in f"{result.message} {result.details}"


def test_notification_preflight_is_optional_when_disabled() -> None:
    result = check_notifications(Config())

    assert result.status is CheckStatus.SKIPPED
    assert result.message == "Notifications disabled"


def test_title_rendering_preflight_says_what_the_pil_fallback_costs() -> None:
    """A self-hoster on a platform with no wheel must learn it here, not after a run.

    Quadrants 1.3.0 publishes wheels for Linux x86_64, Linux aarch64, macOS arm64
    and Windows AMD64 on cp310-cp313, and no sdist. An Intel Mac or Python 3.14
    therefore gets the PIL renderer, and this line is where that is said.
    """
    # WHY: replaces the installed-package probe with what such a platform sees.
    with patch("immich_memories.preflight.importlib.util.find_spec", return_value=None):
        result = check_title_rendering(Config())

    assert result.status is CheckStatus.WARNING
    assert result.message == "PIL renderer: animated backgrounds, raster text (no SDF effects)"
    details = result.details or ""
    assert "quadrants publishes no wheel" in details
    assert "Python 3.10-3.13" in details
    # The platform it could not find a wheel for, so the reader knows it is theirs.
    assert f"{sys.platform}/{platform.machine()}" in details


def _probes(*working: str):
    """The dispatch probe's answers on a machine where only `working` backends start."""
    from immich_memories.titles.kernel_backend_probe import KernelProbeOutcome, KernelProbeResult

    def probe(name: str) -> KernelProbeResult:
        if name in working:
            return KernelProbeResult(KernelProbeOutcome.SUCCESS)
        return KernelProbeResult(KernelProbeOutcome.DISPATCH_FAILED, "fell_back_to_cpu")

    return probe


def test_title_rendering_preflight_names_the_gpu_the_kernels_start_on() -> None:
    # WHY: the dispatch probe is a child process; a unit test must not spawn one.
    with patch(
        "immich_memories.titles.kernel_backend_probe.probe_backend_dispatch",
        _probes("cpu", "metal", "cuda", "vulkan"),
    ):
        result = check_title_rendering(Config())

    gpu = "Metal" if platform.system() == "Darwin" else "CUDA"
    assert result.status is CheckStatus.OK
    assert result.message == f"GPU kernels on {gpu} (quadrants): animated title screens"


def test_title_rendering_preflight_says_a_container_without_a_gpu_draws_on_the_cpu() -> None:
    """A Docker container with no card passed in: the kernels run, on the processor (#1202).

    The row used to say "GPU kernels" there, because it only asked whether the
    CPU could run a kernel at all.
    """
    # WHY: the dispatch probe is a child process; this is what a GPU-less container answers.
    with patch(
        "immich_memories.titles.kernel_backend_probe.probe_backend_dispatch", _probes("cpu")
    ):
        result = check_title_rendering(Config())

    assert result.status is CheckStatus.WARNING
    assert result.message == "Kernels on the CPU (quadrants): no GPU backend started"
    assert "found no device" in (result.details or "")


def test_title_rendering_preflight_reports_a_cpu_that_cannot_run_a_kernel() -> None:
    """An installed wheel is not proof: a Celeron J4125 has no AVX and dies on the
    first kernel the library compiles (#910). Preflight has to say so before the run,
    not leave the user with a dead process at title generation.
    """
    crash = (
        "kernel backend crashed on this CPU: illegal instruction; "
        "titles fall back to the PIL renderer"
    )
    # WHY: the dispatch probe is a child process; this is the answer a no-AVX box gives.
    with patch(
        "immich_memories.titles.kernel_backend_probe.kernel_dispatch_failure",
        return_value=crash,
    ):
        result = check_title_rendering(Config())

    assert result.status is CheckStatus.WARNING
    # The reason is the message, not the details: `preflight` only prints details under -v,
    # and a NAS user meeting this needs the sentence on the first run.
    assert result.message == crash
    assert result.details == "PIL renderer: animated backgrounds, raster text (no SDF effects)"


def test_preflight_run_lists_every_absent_optional_feature() -> None:
    """The degraded-install summary is the whole point: one line per lost feature."""
    # WHY: replaces the installed-package probe with what a bare pip install sees.
    with patch("immich_memories.preflight.importlib.util.find_spec", return_value=None):
        checks = run_preflight_checks(Config())

    degraded = {c.name for c in checks if c.status is CheckStatus.WARNING}
    assert {"Title rendering"} <= degraded


class _CaptionEndpoint:
    """A local stand-in for the caption server's `/models` inventory."""

    def __init__(self, model_ids: list[str], token: str | None = None) -> None:
        body = json.dumps({"data": [{"id": name} for name in model_ids]}).encode()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's name
                if token is not None and self.headers.get("Authorization") != f"Bearer {token}":
                    self.send_error(401, "Unauthorized")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                return

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


def test_encoder_check_names_the_fetch_command_when_the_export_is_absent(tmp_path) -> None:
    config = Config(triage={"encoder": str(tmp_path / "dinov2-small.onnx")})

    result = check_encoder(config)

    assert result.status is CheckStatus.ERROR
    assert "models fetch" in (result.details or "")


def test_encoder_check_rejects_an_export_that_is_not_the_pinned_one(tmp_path) -> None:
    path = tmp_path / "dinov2-small.onnx"
    path.write_bytes(b"some other onnx export")
    config = Config(triage={"encoder": str(path)})

    result = check_encoder(config)

    assert result.status is CheckStatus.ERROR
    assert "pinned" in result.message.lower()


def test_detector_check_names_the_fetch_command_when_the_export_is_absent(tmp_path) -> None:
    config = Config(editorial={"preparation": {"marqo_onnx": str(tmp_path / "marqo.onnx")}})

    result = check_detector_export(config)

    assert result.status is CheckStatus.ERROR
    assert "models fetch" in (result.details or "")


def test_detector_check_rejects_an_export_that_is_not_the_pinned_one(tmp_path) -> None:
    path = tmp_path / "marqo.onnx"
    path.write_bytes(b"some other onnx export")
    config = Config(editorial={"preparation": {"marqo_onnx": str(path)}})

    result = check_detector_export(config)

    assert result.status is CheckStatus.ERROR
    assert "pinned" in result.message.lower()


def test_detector_check_accepts_the_pinned_export(tmp_path, monkeypatch) -> None:
    path = tmp_path / "marqo.onnx"
    path.write_bytes(b"the pinned onnx export")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    # WHY: the real 22.5 MB export cannot live in the repo, so this file's own
    # digest stands in for the pin; the check itself is the production one.
    monkeypatch.setattr(detectors, "MARQO_ONNX_SHA256", digest)
    config = Config(editorial={"preparation": {"marqo_onnx": str(path)}})

    result = check_detector_export(config)

    assert result.status is CheckStatus.OK
    assert result.details == str(path)


def test_caption_check_passes_when_the_endpoint_advertises_the_alias() -> None:
    endpoint = _CaptionEndpoint([API_MODEL])
    try:
        config = Config(editorial={"preparation": {"caption_base_url": endpoint.base_url}})
        result = check_caption_endpoint(config)
    finally:
        endpoint.close()

    assert result.status is CheckStatus.OK
    assert API_MODEL in result.message


def test_caption_check_fails_when_the_endpoint_serves_another_model() -> None:
    endpoint = _CaptionEndpoint(["some-other-vlm"])
    try:
        config = Config(editorial={"preparation": {"caption_base_url": endpoint.base_url}})
        result = check_caption_endpoint(config)
    finally:
        endpoint.close()

    assert result.status is CheckStatus.ERROR
    assert API_MODEL in (result.details or result.message)


def test_caption_check_sends_the_configured_key() -> None:
    endpoint = _CaptionEndpoint([API_MODEL], "caption-token")
    try:
        config = Config(
            editorial={
                "preparation": {
                    "caption_base_url": endpoint.base_url,
                    "caption_api_key": "caption-token",
                }
            }
        )
        result = check_caption_endpoint(config)
    finally:
        endpoint.close()

    assert result.status is CheckStatus.OK


def test_caption_check_names_the_key_when_the_endpoint_refuses() -> None:
    endpoint = _CaptionEndpoint([API_MODEL], "caption-token")
    try:
        config = Config(editorial={"preparation": {"caption_base_url": endpoint.base_url}})
        result = check_caption_endpoint(config)
    finally:
        endpoint.close()

    assert result.status is CheckStatus.ERROR
    assert "caption_api_key" in (result.details or "")


def test_caption_check_names_the_setup_page_when_no_server_answers() -> None:
    config = Config(editorial={"preparation": {"caption_base_url": "http://127.0.0.1:1/v1"}})

    result = check_caption_endpoint(config)

    assert result.status is CheckStatus.ERROR
    assert CAPTION_SETUP_PAGE in (result.details or "")


def test_caption_check_names_the_setup_page_when_the_alias_is_missing() -> None:
    endpoint = _CaptionEndpoint(["some-other-vlm"])
    try:
        config = Config(editorial={"preparation": {"caption_base_url": endpoint.base_url}})
        result = check_caption_endpoint(config)
    finally:
        endpoint.close()

    assert result.status is CheckStatus.ERROR
    assert CAPTION_SETUP_PAGE in (result.details or "")


def test_a_path_carried_from_another_host_is_one_warning(tmp_path: Path) -> None:
    """The same config on a second machine: the paths came with it, the volumes did not."""
    elsewhere = tmp_path / "Users" / "someone"
    config = Config(
        output={"directory": str(elsewhere / "Videos" / "Memories")},
        editorial={"preparation": {"detector_python": str(elsewhere / "venv" / "bin" / "python")}},
    )

    result = check_host_paths(config)

    assert result.status is CheckStatus.WARNING
    assert "2 configured paths" in result.message
    assert "output.directory" in (result.details or "")
    assert "editorial.preparation.detector_python" in (result.details or "")


def test_a_path_the_app_will_create_itself_is_not_a_missing_host_path(tmp_path: Path) -> None:
    """`output.directory` is made on first write; its parent is what has to be here."""
    config = Config(output={"directory": str(tmp_path / "Memories")})

    result = check_host_paths(config)

    assert result.status is CheckStatus.OK


def test_hardware_row_names_the_nvidia_video_capability_when_a_card_is_present() -> None:
    """A GPU node that encodes in software is a misconfiguration, and preflight is
    where a self-hoster should meet it rather than after a long run (#936)."""
    from immich_memories.preflight import check_hardware
    from immich_memories.processing.hardware import HWAccelBackend, HWAccelCapabilities

    with (
        # WHY: no test may probe real ffmpeg encoders; this is the detection boundary
        patch(
            "immich_memories.processing.hardware.detect_hardware_acceleration",
            return_value=HWAccelCapabilities(backend=HWAccelBackend.NONE),
        ),
        # WHY: the container runtime's own marker that a card was handed to this pod
        patch.dict("os.environ", {"NVIDIA_VISIBLE_DEVICES": "all"}),
    ):
        result = check_hardware()

    assert result.status is CheckStatus.WARNING
    assert "NVIDIA_DRIVER_CAPABILITIES=compute,video,utility" in result.message


def test_hardware_row_says_nothing_about_nvidia_on_a_card_less_host() -> None:
    from immich_memories.preflight import check_hardware
    from immich_memories.processing.hardware import HWAccelBackend, HWAccelCapabilities

    with (
        patch(
            "immich_memories.processing.hardware.detect_hardware_acceleration",
            return_value=HWAccelCapabilities(backend=HWAccelBackend.NONE),
        ),
        # WHY: nvidia-smi is the second witness; a GPU-less host has no such binary
        patch("subprocess.run", side_effect=FileNotFoundError),
        patch.dict("os.environ", {"NVIDIA_VISIBLE_DEVICES": ""}),
    ):
        result = check_hardware()

    assert result.message == "No GPU acceleration"
