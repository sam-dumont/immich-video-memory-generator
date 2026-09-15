"""Preflight checks for validating provider connections."""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import httpx
from pydantic import BaseModel

from immich_memories.analysis.llm_providers import ANTHROPIC_VERSION, resolved_llm_config
from immich_memories.analysis.provider_health import (
    ProviderHealth,
    ProviderState,
    classify_provider_response,
)
from immich_memories.api.compatibility import UnsupportedImmichVersion
from immich_memories.config import Config
from immich_memories.security import sanitize_error_message

logger = logging.getLogger(__name__)


class CheckStatus(Enum):
    """Status of a preflight check."""

    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class CheckResult:
    """Result of a single preflight check."""

    name: str
    status: CheckStatus
    message: str
    details: str | None = None


def check_immich(config: Config) -> CheckResult:
    """Check Immich server connection and API key validity.

    Args:
        config: Configuration to use.

    Returns:
        CheckResult with status and details.
    """
    if not config.immich.url:
        return CheckResult(
            name="Immich",
            status=CheckStatus.ERROR,
            message="URL not configured",
            details="Set immich.url in config or IMMICH_MEMORIES_IMMICH__URL env var",
        )

    if not config.immich.api_key:
        return CheckResult(
            name="Immich",
            status=CheckStatus.ERROR,
            message="API key not configured",
            details="Set immich.api_key in config or IMMICH_MEMORIES_IMMICH__API_KEY env var",
        )

    from immich_memories.api.immich import ImmichAPIError, SyncImmichClient

    try:
        with SyncImmichClient(
            base_url=config.immich.url,
            api_key=config.immich.api_key,
            api_version=config.immich.api_version,
        ) as client:
            resolved_version = client.get_api_version()
            user = client.get_current_user()
            return CheckResult(
                name="Immich",
                status=CheckStatus.OK,
                message=f"Connected as {user.name or user.email}",
                details=f"Server: {config.immich.url}; API: {resolved_version.value}",
            )
    except UnsupportedImmichVersion as e:
        safe_message = sanitize_error_message(str(e)).replace(config.immich.api_key, "***")
        return CheckResult(
            name="Immich",
            status=CheckStatus.ERROR,
            message="Unsupported Immich version",
            details=safe_message,
        )
    except ImmichAPIError as e:
        safe_message = sanitize_error_message(str(e)).replace(config.immich.api_key, "***")
        diagnostics = [safe_message]
        if e.status_code is not None:
            diagnostics.append(f"HTTP {e.status_code}")
        if e.correlation_id:
            safe_correlation = sanitize_error_message(e.correlation_id).replace(
                config.immich.api_key, "***"
            )
            diagnostics.append(f"Correlation ID: {safe_correlation}")
        return CheckResult(
            name="Immich",
            status=CheckStatus.ERROR,
            message="Connection failed",
            details="; ".join(diagnostics),
        )
    except (httpx.TimeoutException, httpx.HTTPStatusError, OSError) as e:
        return CheckResult(
            name="Immich",
            status=CheckStatus.ERROR,
            message="Connection failed",
            details=sanitize_error_message(str(e)).replace(config.immich.api_key, "***"),
        )


_UNREACHABLE = "Check the configured LLM base URL and provider availability"


def _transport_failure(exc: Exception, unreachable: str = _UNREACHABLE) -> CheckResult:
    """One answer for every way a provider can fail to answer at all."""
    if isinstance(exc, httpx.ConnectError):
        return CheckResult(
            name="LLM", status=CheckStatus.WARNING, message="Cannot connect", details=unreachable
        )
    return CheckResult(
        name="LLM",
        status=CheckStatus.WARNING,
        message="Connection error",
        details=type(exc).__name__,
    )


def _check_ollama(base_url: str, model: str) -> CheckResult:
    """Check Ollama server availability via /api/tags.

    Args:
        base_url: Ollama server URL.
        model: Configured model name.

    Returns:
        CheckResult with status and details.
    """
    try:
        normalized = base_url.rstrip("/")

        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{normalized}/api/tags")
            response.raise_for_status()
            data = response.json()

            models = data.get("models", [])
            model_names = [m.get("name", "") for m in models]

            if model and model not in model_names:
                base_name = model.split(":")[0]
                if not any(m.startswith(base_name) for m in model_names):
                    return CheckResult(
                        name="LLM",
                        status=CheckStatus.WARNING,
                        message=f"Connected but missing model: {model}",
                        details=f"Available: {', '.join(model_names[:5])}{'...' if len(model_names) > 5 else ''}",
                    )

            return CheckResult(
                name="LLM",
                status=CheckStatus.OK,
                message=f"Connected (ollama, {len(models)} models)",
                details=f"Model: {model}",
            )

    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError, OSError) as exc:
        return _transport_failure(
            exc, "Check the configured LLM base URL and that Ollama is running"
        )


def _llm_health_failure(
    health: ProviderHealth,
    model: str,
    route: str = "Chat-completions",
    path: str = "/chat/completions",
) -> CheckResult | None:
    """Translate provider health into a safe, actionable preflight failure."""
    if health.state is ProviderState.AUTH_FAILED:
        return CheckResult(
            name="LLM",
            status=CheckStatus.ERROR,
            message="Authentication failed",
            details="The configured API key was rejected",
        )
    if health.state is ProviderState.MODEL_MISSING:
        return CheckResult(
            name="LLM",
            status=CheckStatus.WARNING,
            message=f"Configured model unavailable: {model}",
            details=f"Model: {model}",
        )
    if health.state is ProviderState.ROUTE_MISSING:
        return CheckResult(
            name="LLM",
            status=CheckStatus.WARNING,
            message=f"{route} route unavailable",
            details=f"Check that the configured base URL exposes {path}",
        )
    if health.available:
        return None
    return CheckResult(
        name="LLM",
        status=CheckStatus.WARNING,
        message=health.message,
        details="Check the configured LLM provider",
    )


def _check_openai_compatible(base_url: str, model: str, api_key: str) -> CheckResult:
    """Check OpenAI-compatible server via test completion.

    Args:
        base_url: API base URL (e.g. http://localhost:8080/v1).
        model: Model name.
        api_key: API key (may be empty for local servers).

    Returns:
        CheckResult with status and details.
    """
    try:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        with httpx.Client(timeout=10.0, headers=headers) as client:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            }
            response = client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload,
            )

            try:
                response_body = response.json()
            except ValueError:
                response_body = {}
            health = classify_provider_response(response.status_code, response_body, model)
            if failure := _llm_health_failure(health, model):
                return failure

            return CheckResult(
                name="LLM",
                status=CheckStatus.OK,
                message="Connected (openai-compatible)",
                details=f"Model: {model}",
            )

    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError, OSError) as exc:
        return _transport_failure(exc)


def _anthropic_headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "anthropic-version": ANTHROPIC_VERSION}
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def _listed_model_ids(client: httpx.Client, base_url: str) -> list[str] | None:
    """The model ids the host publishes, or None when it publishes none.

    Anthropic and z.ai's compatible route both answer `GET /v1/models` with
    `{"data": [{"id": ...}]}` (measured 2026-09-14). A host behind a gateway
    that serves only `/v1/messages` answers something else, and then the probe
    below is the only way to know it is there.
    """
    try:
        response = client.get(f"{base_url}/v1/models")
        if response.status_code != 200:
            return None
        data = response.json().get("data")
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    return [str(entry["id"]) for entry in data if isinstance(entry, dict) and "id" in entry]


def _catalogue_result(listed: list[str], model: str) -> CheckResult:
    if model and model not in listed:
        shown = ", ".join(listed[:5])
        return CheckResult(
            name="LLM",
            status=CheckStatus.WARNING,
            message=f"Connected but missing model: {model}",
            details=f"Available: {shown}{'...' if len(listed) > 5 else ''}",
        )
    return CheckResult(
        name="LLM",
        status=CheckStatus.OK,
        message=f"Connected (anthropic, {len(listed)} models)",
        details=f"Model: {model}",
    )


def _one_token_probe(client: httpx.Client, base_url: str, model: str) -> CheckResult:
    """Ask the host for a single token, which is the cheapest proof it answers."""
    response = client.post(
        f"{base_url}/v1/messages",
        json={"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]},
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    health = classify_provider_response(response.status_code, body, model)
    failure = _llm_health_failure(health, model, "Messages", "/v1/messages")
    return failure or CheckResult(
        name="LLM",
        status=CheckStatus.OK,
        message="Connected (anthropic)",
        details=f"Model: {model}",
    )


def _check_anthropic(base_url: str, model: str, api_key: str) -> CheckResult:
    """Check a Messages API host: its model list where it has one, a probe where it does not."""
    normalized = base_url.rstrip("/")
    try:
        with httpx.Client(timeout=10.0, headers=_anthropic_headers(api_key)) as client:
            listed = _listed_model_ids(client, normalized)
            if listed:
                return _catalogue_result(listed, model)
            return _one_token_probe(client, normalized, model)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError, OSError) as exc:
        return _transport_failure(exc)


def check_llm(config: Config) -> CheckResult:
    """Check LLM provider availability.

    Dispatches on the resolved provider:
    - "ollama": GET /api/tags
    - "anthropic": GET /v1/models, or a one-token POST /v1/messages
    - "openai-compatible": POST /chat/completions with minimal payload

    Args:
        config: Configuration to use.

    Returns:
        CheckResult with status and details.
    """
    try:
        reader = config.editorial.resolve_reader(config.llm.model)
    except ValueError as exc:
        return CheckResult(name="LLM", status=CheckStatus.ERROR, message=str(exc))
    if reader == "rules":
        return CheckResult(
            name="LLM", status=CheckStatus.SKIPPED, message="Rules reader does not use an LLM"
        )
    # A named provider is its adapter plus a URL, and the check has to reach
    # the endpoint the run will: `zai` and `openai` both resolve to one of the
    # two adapters, and to the vendor URL where none was set.
    llm = resolved_llm_config(config.llm)
    base_url = llm.base_url
    model = llm.model

    if not base_url:
        return CheckResult(
            name="LLM",
            status=CheckStatus.SKIPPED,
            message="Not configured",
            details="No base_url set",
        )

    if llm.provider == "ollama":
        return _check_ollama(base_url, model)
    if llm.provider == "anthropic":
        return _check_anthropic(base_url, model, llm.api_key)
    return _check_openai_compatible(base_url, model, llm.api_key)


def check_hardware() -> CheckResult:
    """Check hardware acceleration availability.

    Returns:
        CheckResult with status and details.
    """
    try:
        from immich_memories.processing.hardware import (
            HWAccelBackend,
            detect_hardware_acceleration,
            nvenc_capability_hint,
        )

        caps = detect_hardware_acceleration()

        if caps.backend == HWAccelBackend.NONE:
            # The capability line leads when there is a card, because `preflight`
            # prints details only under -v and a GPU node encoding in software is
            # a misconfiguration rather than the expected answer (#936).
            hint = nvenc_capability_hint()
            return CheckResult(
                name="Hardware",
                status=CheckStatus.WARNING,
                message=hint or "No GPU acceleration",
                details="Video encoding will use CPU (slower)",
            )

        features = []
        if caps.supports_h264_encode:
            features.append("H.264 encode")
        if caps.supports_h265_encode:
            features.append("H.265 encode")
        if caps.opencv_cuda:
            features.append("OpenCV CUDA")

        return CheckResult(
            name="Hardware",
            status=CheckStatus.OK,
            message=f"{caps.backend.value.upper()} ({caps.device_name or 'Unknown'})",
            details=", ".join(features) if features else "Basic acceleration",
        )

    except (ImportError, RuntimeError, OSError) as e:
        return CheckResult(
            name="Hardware",
            status=CheckStatus.WARNING,
            message="Detection failed",
            details=str(e),
        )


def check_title_rendering(config: Config) -> CheckResult:
    """Report whether title screens get the GPU renderer or the PIL fallback."""
    if not config.title_screens.enabled:
        return CheckResult(
            name="Title rendering",
            status=CheckStatus.SKIPPED,
            message="Title screens disabled",
        )
    return _kernel_library_check()


_PIL_RENDERER_MESSAGE = "PIL renderer: animated backgrounds, raster text (no SDF effects)"


def _kernel_library_check() -> CheckResult:
    """Name the title renderer this machine will use, and why.

    Two ways to lose the kernels, and a self-hoster should meet both here rather
    than after a long run: no wheel for the platform, answered by `find_spec`,
    and a wheel that cannot run here, answered by the child dispatch probe. The
    second is the expensive one, and it is why an installed package is not the
    answer on its own: a CPU without AVX dies on the first kernel (#910).
    """
    # WHY the probe module and not the seam behind it: importing the seam is
    # importing the library, and on a processor without AVX that is the crash
    # this check exists to report (#910).
    from immich_memories.titles.kernel_backend_probe import KERNEL_LIBRARY

    if importlib.util.find_spec(KERNEL_LIBRARY) is None:
        return CheckResult(
            name="Title rendering",
            status=CheckStatus.WARNING,
            message=_PIL_RENDERER_MESSAGE,
            details=(
                f"{KERNEL_LIBRARY} publishes no wheel for {_platform_tag()}. "
                "Wheels exist for Linux x86_64, Linux aarch64, macOS arm64 and Windows AMD64 "
                "on Python 3.10-3.13; everywhere else title screens are PIL-rendered, "
                "which the log says once at startup."
            ),
        )

    from immich_memories.titles.kernel_backend_probe import kernel_dispatch_failure

    if reason := kernel_dispatch_failure():
        # The reason leads, because `preflight` prints details only under -v and this
        # is the line that tells a self-hoster their processor is the problem.
        return CheckResult(
            name="Title rendering",
            status=CheckStatus.WARNING,
            message=reason,
            details=_PIL_RENDERER_MESSAGE,
        )
    return CheckResult(
        name="Title rendering",
        status=CheckStatus.OK,
        message=f"GPU kernels ({KERNEL_LIBRARY}): animated title screens",
    )


def _platform_tag() -> str:
    """This interpreter as the two things a wheel is chosen by."""
    import platform

    return (
        f"{sys.platform}/{platform.machine()} on Python "
        f"{sys.version_info.major}.{sys.version_info.minor}"
    )


def check_encoder(config: Config) -> CheckResult:
    """Report the digest-pinned DINOv2 export the six context heads run on."""
    if not config.editorial.preparation.demands_models:
        return CheckResult(
            name="Encoder", status=CheckStatus.SKIPPED, message="Not required by metadata_only"
        )
    from immich_memories.triage.encoder import DINOV2_SMALL_ONNX_SHA256

    path = config.triage.encoder_path
    if not path.is_file():
        return CheckResult(
            name="Encoder",
            status=CheckStatus.ERROR,
            message="Pinned DINOv2 export missing",
            details=f"{path}; run: immich-memories models fetch",
        )
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != DINOV2_SMALL_ONNX_SHA256:
        return CheckResult(
            name="Encoder",
            status=CheckStatus.ERROR,
            message="Not the pinned DINOv2 export",
            details=f"{path}: {digest[:12]} is not {DINOV2_SMALL_ONNX_SHA256[:12]}",
        )
    return CheckResult(
        name="Encoder",
        status=CheckStatus.OK,
        message="Pinned DINOv2 export verified",
        details=str(path),
    )


def check_detector_export(config: Config) -> CheckResult:
    """Report the digest-pinned sensitive-content export the flag detector runs on.

    It is checked here because the alternative is finding out during the cut:
    the detector worker is a separate process reached hours into preparation.
    """
    if not config.editorial.preparation.demands_models:
        return CheckResult(
            name="Sensitive-content detector",
            status=CheckStatus.SKIPPED,
            message="Not required by metadata_only",
        )
    from immich_memories.analysis.editorial_preparation_detectors import (
        MARQO_ONNX_ID,
        MARQO_ONNX_SHA256,
    )

    path = config.editorial.preparation.marqo_onnx_path
    if not path.is_file():
        return CheckResult(
            name="Sensitive-content detector",
            status=CheckStatus.ERROR,
            message=f"Pinned {MARQO_ONNX_ID} export missing",
            details=f"{path}; run: immich-memories models fetch",
        )
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != MARQO_ONNX_SHA256:
        return CheckResult(
            name="Sensitive-content detector",
            status=CheckStatus.ERROR,
            message=f"Not the pinned {MARQO_ONNX_ID} export",
            details=f"{path}: {digest[:12]} is not {MARQO_ONNX_SHA256[:12]}",
        )
    return CheckResult(
        name="Sensitive-content detector",
        status=CheckStatus.OK,
        message="Pinned sensitive-content export verified",
        details=str(path),
    )


# Nothing ships a captioner, so a failing row has to say where the recipes are.
# A path, not a URL: the docs travel with the checkout and with the image.
CAPTION_SETUP_PAGE = "docs/deploy/installation/caption-server.md"


def _caption_endpoint_unreachable(base_url: str, error: Exception) -> CheckResult:
    return CheckResult(
        name="Captions",
        status=CheckStatus.ERROR,
        message="Caption endpoint unreachable",
        details=(
            f"{base_url}: {sanitize_error_message(str(error))}; "
            f"set one up with {CAPTION_SETUP_PAGE}"
        ),
    )


def check_caption_endpoint(config: Config) -> CheckResult:
    """Report whether the configured caption server advertises the accepted alias."""
    if not config.editorial.preparation.demands_captions:
        return CheckResult(
            name="Captions",
            status=CheckStatus.SKIPPED,
            message=f"Not required by {config.editorial.preparation.tier}",
        )
    from immich_memories.analysis.editorial_description_contract import API_MODEL
    from immich_memories.analysis.editorial_preparation_captions import (
        CAPTION_KEY_HINT,
        REFUSED_CODES,
        bearer_headers,
    )

    preparation = config.editorial.preparation
    base_url = preparation.caption_base_url
    try:
        response = httpx.get(
            f"{base_url}/models",
            timeout=5.0,
            headers=bearer_headers(preparation.caption_api_key),
        )
        response.raise_for_status()
        rows = response.json().get("data", [])
    except httpx.HTTPStatusError as e:
        if e.response.status_code not in REFUSED_CODES:
            return _caption_endpoint_unreachable(base_url, e)
        return CheckResult(
            name="Captions",
            status=CheckStatus.ERROR,
            message="Caption endpoint refused the request",
            details=f"{base_url} answered HTTP {e.response.status_code}; {CAPTION_KEY_HINT}",
        )
    except (httpx.HTTPError, ValueError) as e:
        return _caption_endpoint_unreachable(base_url, e)
    served = {row.get("id") for row in rows if isinstance(row, dict)}
    if API_MODEL not in served:
        return CheckResult(
            name="Captions",
            status=CheckStatus.ERROR,
            message="Caption endpoint serves another model",
            details=(
                f"{base_url} advertises {sorted(map(str, served))}, not {API_MODEL}; "
                f"serve it under that alias as in {CAPTION_SETUP_PAGE}"
            ),
        )
    return CheckResult(
        name="Captions",
        status=CheckStatus.OK,
        message=f"Serving {API_MODEL}",
        details=base_url,
    )


# Every path-valued setting that describes the host rather than the library.
# A config is portable until one of these is in it: copied to a second machine
# it still names an interpreter under /Users, or a models directory on a volume
# the new box does not mount, and the failure lands hours later inside a worker.
# The encoder and the sensitive-content export are deliberately absent: they get
# their own rows above, with the digest and the command that fixes them.
HOST_PATH_KEYS = (
    "output.directory",
    "audio.local_music_dir",
    "cache.directory",
    "cache.database",
    "editorial.annotation_database",
    "editorial.preparation.head_bundle",
    "editorial.preparation.detector_python",
    "editorial.preparation.detector_cache_dir",
    "triage.bundle",
)


def _host_paths_set_by_hand(config: Config) -> Iterator[tuple[str, Path]]:
    """Yield (key, path) for every host path someone wrote down, defaults skipped."""
    for key in HOST_PATH_KEYS:
        *sections, field = key.split(".")
        owner: BaseModel = config
        for part in sections:
            owner = getattr(owner, part)
        value = str(getattr(owner, field)).strip()
        if value and value != type(owner).model_fields[field].default:
            yield key, Path(value).expanduser()


def check_host_paths(config: Config) -> CheckResult:
    """Report configured paths that are not on this host, all in one row.

    A path the app writes is created inside a directory that already exists, so
    the test is the parent: present means the app can make the rest, absent means
    the path came from somewhere else. WARNING and not ERROR, because a NAS whose
    music share is unmounted this morning should still be able to cut a memory.
    """
    missing = [
        f"{key}={path}"
        for key, path in _host_paths_set_by_hand(config)
        if not path.exists() and not path.parent.is_dir()
    ]
    if not missing:
        return CheckResult(
            name="Config paths",
            status=CheckStatus.OK,
            message="Every configured path is on this host",
        )
    noun = "path is" if len(missing) == 1 else "paths are"
    return CheckResult(
        name="Config paths",
        status=CheckStatus.WARNING,
        message=f"{len(missing)} configured {noun} not on this host",
        details=f"{'; '.join(missing)} (a config copied between hosts keeps the first host's paths)",
    )


def run_preflight_checks(config: Config) -> list[CheckResult]:
    """Run all preflight checks.

    Args:
        config: Configuration to use.

    Returns:
        List of check results.
    """
    from immich_memories.preflight_homebase import check_homebase

    return [
        check_immich(config),
        check_homebase(config),
        check_llm(config),
        check_title_rendering(config),
        check_encoder(config),
        check_detector_export(config),
        check_caption_endpoint(config),
        check_host_paths(config),
        check_notifications(config),
        check_hardware(),
    ]


def check_notifications(config: Config) -> CheckResult:
    """Report optional durable notification health without probing provider URLs."""
    if not config.notifications.enabled:
        return CheckResult(
            name="Notifications",
            status=CheckStatus.SKIPPED,
            message="Notifications disabled",
        )
    if not config.notifications.urls:
        return CheckResult(
            name="Notifications",
            status=CheckStatus.WARNING,
            message="No notification URLs configured",
        )

    from immich_memories.automation.notification_state import NotificationStateStore

    try:
        health = NotificationStateStore(config.cache.database_path).get()
    except Exception:  # WHY: optional health telemetry cannot fail provider preflight
        return CheckResult(
            name="Notifications",
            status=CheckStatus.WARNING,
            message="Notification health unavailable",
        )
    if health is None:
        return CheckResult(
            name="Notifications",
            status=CheckStatus.OK,
            message="Configured; no delivery attempted yet",
        )
    if health.is_cooling_down(config.notifications.cooldown_hours):
        category = health.failure_category.value if health.failure_category else "delivery"
        return CheckResult(
            name="Notifications",
            status=CheckStatus.WARNING,
            message=f"Delivery paused after {category} failure",
            details=f"Normal attempts resume after the {config.notifications.cooldown_hours}h cooldown",
        )
    if health.last_success_at is not None and (
        health.last_failure_at is None or health.last_success_at >= health.last_failure_at
    ):
        return CheckResult(
            name="Notifications",
            status=CheckStatus.OK,
            message="Last notification delivered successfully",
            details=f"Last success: {health.last_success_at.isoformat()}",
        )
    return CheckResult(
        name="Notifications",
        status=CheckStatus.WARNING,
        message="Previous notification delivery failed",
        details="Cooldown expired; run auto test-notification to verify recovery",
    )
