"""Read the optional render worker's health without submitting a film."""

import httpx

from immich_memories.config import Config
from immich_memories.generate import GenerationError
from immich_memories.generate_delivery import _safe_delivery_message
from immich_memories.preflight import CheckResult, CheckStatus
from immich_memories.processing.remote_render import RemoteRenderClient


def check_render_worker(config: Config) -> CheckResult:
    """Report reachability, version compatibility and the available render hardware."""
    if not config.render.enabled:
        return CheckResult("Render worker", CheckStatus.SKIPPED, "Rendering locally")
    settings = config.render.model_copy(update={"timeout_seconds": 10})
    try:
        with RemoteRenderClient(settings) as worker:
            health = worker.health()
    except (GenerationError, httpx.HTTPError, ValueError, TypeError, OSError) as exc:
        return CheckResult(
            "Render worker",
            CheckStatus.WARNING if settings.fallback_to_local else CheckStatus.ERROR,
            "Worker unavailable; local fallback enabled"
            if settings.fallback_to_local
            else "Worker unavailable",
            _safe_delivery_message(exc, config),
        )
    details = (
        f"Titles: {health.get('titles', 'unknown')}; "
        f"encoders: {', '.join(health.get('encoders') or []) or 'software only'}"
    )
    if settings.worker_base_url.startswith("http://"):
        details = f"Cleartext HTTP transport (allow_insecure_http); {details}"
    return CheckResult(
        "Render worker",
        CheckStatus.OK if health.get("accelerated") else CheckStatus.WARNING,
        "Connected; GPU rendering available"
        if health.get("accelerated")
        else "Connected; full GPU acceleration unavailable",
        _safe_delivery_message(details, config),
    )
