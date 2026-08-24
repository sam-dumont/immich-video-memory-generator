"""The health API: the three routes a probe or an operator can ask about this server.

`/health/live` says the process is up, `/health/ready` says configuration and Immich
can support real work, and `/health` keeps the older detailed payload without the
readiness status codes. All three are unauthenticated so a container runtime can
reach them, which is also why the detail in those payloads is gated below.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import httpx
from nicegui import app
from starlette.requests import Request
from starlette.responses import JSONResponse

from immich_memories import __version__
from immich_memories.automation.in_process_scheduler import automation_scheduler
from immich_memories.config import get_config
from immich_memories.security import configured_secret_values, sanitize_error_message
from immich_memories.ui.auth import is_auth_enabled

if TYPE_CHECKING:
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ImmichDependency:
    """Sanitized result of the dependency probe used by health responses."""

    status: Literal[
        "ready",
        "missing_configuration",
        "unreachable",
        "authentication_failed",
        "unsupported_version",
    ]
    reachable: bool
    resolved_api_version: str | None = None


async def _check_immich_dependency(config) -> _ImmichDependency:
    """Resolve the configured API policy and authenticate within a bounded time."""
    from immich_memories.api.compatibility import UnsupportedImmichVersion
    from immich_memories.api.immich import ImmichAPIError, ImmichAuthError, ImmichClient

    try:
        async with ImmichClient(
            base_url=config.immich.url,
            api_key=config.immich.api_key,
            api_version=config.immich.api_version,
            timeout=2.0,
        ) as client:

            async def resolve_and_authenticate() -> str:
                resolved = await client.get_api_version()
                await client.get_current_user()
                return resolved.value

            resolved_api_version = await asyncio.wait_for(resolve_and_authenticate(), timeout=5.0)
        return _ImmichDependency(
            status="ready",
            reachable=True,
            resolved_api_version=resolved_api_version,
        )
    except UnsupportedImmichVersion:
        return _ImmichDependency(status="unsupported_version", reachable=True)
    except ImmichAuthError:
        return _ImmichDependency(status="authentication_failed", reachable=True)
    except (ImmichAPIError, TimeoutError, httpx.HTTPError, OSError, ValueError):
        return _ImmichDependency(status="unreachable", reachable=False)


def _get_last_successful_run(config) -> str | None:
    """Return ISO timestamp of last completed run, or None."""
    from immich_memories.tracking.run_database import RunDatabase

    db = RunDatabase(db_path=config.cache.database_path)
    runs = db.list_runs(limit=1, status="completed")
    if runs and runs[0].completed_at:
        return runs[0].completed_at.isoformat()
    return None


def _get_automation_status(config) -> dict[str, Any]:
    """Return the durable, read-only smart automation status contract."""
    from immich_memories.automation.runner import AutoRunner

    return AutoRunner(config).status().to_dict()


def _automation_field(automation: dict[str, Any] | None, name: str) -> Any:
    """Project one optional automation field without branching in readiness logic."""
    return automation.get(name) if automation is not None else None


def _sanitize_health_text(value: object, secrets_to_redact: tuple[str, ...]) -> str:
    """Apply structural and config-aware sanitization before exposing health text."""
    safe = sanitize_error_message(str(value))
    for secret in secrets_to_redact:
        safe = safe.replace(secret, "***")
    return safe


def _redact_health_value(value: Any, secrets_to_redact: tuple[str, ...]) -> Any:
    """Recursively sanitize operational state with the shared text redactor."""
    if isinstance(value, str):
        return _sanitize_health_text(value, secrets_to_redact)
    if isinstance(value, dict):
        return {key: _redact_health_value(item, secrets_to_redact) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_health_value(item, secrets_to_redact) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_health_value(item, secrets_to_redact) for item in value)
    return value


def _health_detail_allowed(config: Config) -> bool:
    """Return whether the current request may see automation/run detail."""
    if not is_auth_enabled(config.auth):
        return True
    try:
        return bool(app.storage.user.get("authenticated"))
    except RuntimeError:
        # No request/session context (e.g. called outside an HTTP request).
        return False


def _operational_detail(
    config: Config, secrets_to_redact: tuple[str, ...]
) -> tuple[dict[str, Any] | None, str | None]:
    """Return (automation status, last successful run) for the health payload.

    WHY: /health is public so probes work, but automation detail carries person
    names (memory keys) and host paths. With auth on, only a logged-in session
    gets the detail; probes and other LAN devices get status + version.
    """
    if not _health_detail_allowed(config):
        return None, None

    automation: dict[str, Any] | None = None
    try:
        automation = _get_automation_status(config)
    except Exception as exc:
        logger.warning(
            "Could not read automation status for readiness (%s)",
            type(exc).__name__,
        )

    last_successful_run: str | None = None
    try:
        last_successful_run = _get_last_successful_run(config)
    except Exception as exc:
        logger.warning(
            "Could not read run status for readiness (%s)",
            type(exc).__name__,
        )

    if automation is not None:
        automation = _redact_health_value(automation, secrets_to_redact)
    return automation, last_successful_run


# /health and /health/ready are unauthenticated so probes work without
# credentials, and each build costs a network round trip to Immich plus ~12
# SQLite connections. Serving repeats from a short-lived snapshot keeps a flood
# of requests from turning into a flood of work, while staying well inside the
# 15s readiness period so a scheduled probe still reports current truth.
_HEALTH_SNAPSHOT_TTL_SECONDS = 10.0
_health_snapshot_cache: tuple[float, dict[str, Any]] | None = None


async def _build_health_snapshot() -> dict[str, Any]:
    """Build the shared detailed health payload, reusing a recent one if fresh."""
    global _health_snapshot_cache

    cached = _health_snapshot_cache
    if cached is not None and time.monotonic() - cached[0] < _HEALTH_SNAPSHOT_TTL_SECONDS:
        return cached[1]

    snapshot = await _compute_health_snapshot()
    _health_snapshot_cache = (time.monotonic(), snapshot)
    return snapshot


async def _compute_health_snapshot() -> dict[str, Any]:
    """Build the shared detailed health payload for readiness and compatibility."""
    try:
        config = get_config()
    except Exception:
        return {
            "status": "degraded",
            "configuration": "unavailable",
            "immich_reachable": False,
            "immich": {
                "status": "missing_configuration",
                "reachable": False,
                "api_version_policy": None,
                "resolved_api_version": None,
            },
            "automation": None,
            "last_automation_attempt": None,
            "last_successful_auto_run": None,
            "pending_delivery_count": None,
            "oldest_pending_delivery": None,
            "notification_health": None,
            "last_successful_run": None,
            "in_process_scheduler": None,
            "version": __version__,
        }

    secrets_to_redact = configured_secret_values(config)
    configured = bool(config.immich.url and config.immich.api_key)
    if configured:
        try:
            immich = await _check_immich_dependency(config)
        except Exception:
            immich = _ImmichDependency(status="unreachable", reachable=False)
    else:
        immich = _ImmichDependency(status="missing_configuration", reachable=False)

    # Synchronous SQLite: on the event loop each open waits on the write lock
    # while a pipeline run holds it, which stalls every other session.
    automation, last_successful_run = await asyncio.to_thread(
        _operational_detail, config, secrets_to_redact
    )

    ready = configured and immich.status == "ready"
    api_version_policy = getattr(config.immich.api_version, "value", config.immich.api_version)
    return {
        "status": "ready" if ready else "degraded",
        "configuration": "configured" if configured else "missing",
        "immich_reachable": immich.reachable,
        "immich": {
            "status": immich.status,
            "reachable": immich.reachable,
            "api_version_policy": api_version_policy,
            "resolved_api_version": immich.resolved_api_version,
        },
        "automation": automation,
        "last_automation_attempt": automation.get("last_attempt") if automation else None,
        "last_successful_auto_run": (
            automation.get("last_completed_auto_run") if automation else None
        ),
        "pending_delivery_count": (
            automation.get("pending_delivery_count") if automation else None
        ),
        "oldest_pending_delivery": (
            automation.get("oldest_pending_delivery") if automation else None
        ),
        "notification_health": _automation_field(automation, "notification_health"),
        "last_successful_run": last_successful_run,
        "in_process_scheduler": (
            automation_scheduler.snapshot().to_dict() if _health_detail_allowed(config) else None
        ),
        "version": __version__,
    }


async def _liveness_handler(request: Request) -> JSONResponse:  # noqa: ARG001
    """Report only that the web process is alive."""
    return JSONResponse({"status": "alive", "version": __version__})


async def _readiness_handler(request: Request) -> JSONResponse:  # noqa: ARG001
    """Report whether configuration and Immich can support useful work."""
    snapshot = await _build_health_snapshot()
    return JSONResponse(snapshot, status_code=200 if snapshot["status"] == "ready" else 503)


async def _health_handler(request: Request) -> JSONResponse:  # noqa: ARG001
    """Keep the detailed compatibility payload without readiness status codes."""
    snapshot = await _build_health_snapshot()
    if snapshot["status"] == "ready":
        snapshot["status"] = "ok"
    return JSONResponse(snapshot)


def register_health_routes(server: Any) -> None:
    """Attach the liveness, readiness, and compatibility health routes to a server."""
    server.add_api_route("/health", _health_handler, methods=["GET"])
    server.add_api_route("/health/live", _liveness_handler, methods=["GET"])
    server.add_api_route("/health/ready", _readiness_handler, methods=["GET"])
