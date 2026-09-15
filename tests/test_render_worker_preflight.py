"""Configured render workers appear in preflight without receiving footage."""

import httpx
import pytest

from immich_memories import __version__
from immich_memories.config import Config
from immich_memories.preflight import CheckStatus


@pytest.mark.parametrize("accelerated", [True, False])
def test_preflight_reports_render_capabilities_without_sending_a_render(monkeypatch, accelerated):
    from immich_memories.preflight_render import check_render_worker

    config = Config(
        render={"worker_base_url": "https://worker.example.com", "worker_token": "test-token"}
    )
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "app_version": __version__,
                "contract_version": 1,
                "ready": True,
                "accelerated": accelerated,
                "titles": "CUDA",
                "encoders": ["h264_nvenc"],
            },
        )

    real_client = httpx.Client
    # WHY: replace the external HTTP endpoint, retaining authentication and JSON parsing.
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    result = check_render_worker(config)
    assert result.status is (CheckStatus.OK if accelerated else CheckStatus.WARNING)
    assert "CUDA" in result.details and "h264_nvenc" in result.details
    assert [(r.method, r.url.path) for r in calls] == [("GET", "/health")]
    assert calls[0].headers["authorization"] == "Bearer test-token"
    assert not calls[0].content


@pytest.mark.parametrize("fallback", [False, True])
def test_preflight_failure_respects_local_fallback_and_redacts_the_token(monkeypatch, fallback):
    from immich_memories.preflight_render import check_render_worker

    config = Config(
        render={
            "worker_base_url": "https://worker.example.com",
            "worker_token": "test-private-token",
            "fallback_to_local": fallback,
        }
    )
    real_client = httpx.Client
    # WHY: simulate an external worker rejecting authentication and echoing its input.
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda _: httpx.Response(401, text="test-private-token")),
            **kwargs,
        ),
    )
    result = check_render_worker(config)
    assert result.status is (CheckStatus.WARNING if fallback else CheckStatus.ERROR)
    assert "HTTP 401" in result.details
    assert "test-private-token" not in str(result)
