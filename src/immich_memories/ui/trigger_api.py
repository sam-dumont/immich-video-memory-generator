"""The HTTP trigger API: one POST that runs the decision `auto run` would make.

Immich's Workflows can call an external URL when something happens in the library.
This is the URL. It takes no parameters on purpose — the server decides what to
generate, through exactly the machinery the nightly timer and the CLI already use.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from immich_memories.automation.models import AutoOutcome
from immich_memories.automation.runner import (
    AutomationAlreadyRunningError,
    AutoRunner,
    StartedAutoRun,
)
from immich_memories.automation.state_store import AutomationStateStore
from immich_memories.config import get_config
from immich_memories.tracking.run_database import RunDatabase
from immich_memories.ui.auth import (
    is_auth_enabled,
    presented_trigger_token,
    trigger_token_matches,
)

if TYPE_CHECKING:
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)

TRIGGER_PATH = "/api/trigger"

# What the attempt history records for a run somebody asked for over HTTP, so
# `auto status` and /health can tell it apart from the nightly wake.
TRIGGER_REASON = "http trigger"

# The run fields a caller polling for its artifact actually needs. The full record
# carries host paths and person names that this contract has no reason to promise.
_RUN_FIELDS = (
    "run_id",
    "status",
    "created_at",
    "completed_at",
    "last_phase",
    "output_duration_seconds",
    "delivery_status",
    "immich_asset_id",
)

# asyncio keeps only a weak reference to a bare task, so a fire-and-forget
# generation can be collected mid-run. These are the strong ones.
_running: set[asyncio.Task[Any]] = set()


def trigger_enabled(config: Config) -> bool:
    """Whether anything at all authenticates a caller of the trigger API.

    With neither authentication nor a token the routes answer 404: this process
    holds an Immich API key, and nobody who merely reached the port may spend it.
    """
    return is_auth_enabled(config.auth) or bool(config.server.trigger_token)


def _authorized(request: Request, config: Config) -> bool:
    """Whether this request may start work, by token or by the session behind it."""
    if presented := presented_trigger_token(request.headers):
        return trigger_token_matches(presented, config.server.trigger_token)
    # Nothing offered. With auth on, the middleware has already proven a session
    # got this far; with auth off, a token was the only thing that could have.
    return is_auth_enabled(config.auth)


def _submit_to_background(started: StartedAutoRun) -> None:
    """Run the accepted decision off the request path.

    A generation blocks for up to two hours, so it goes to a worker thread the
    same way the in-process daily timer sends its own.
    """
    task = asyncio.create_task(asyncio.to_thread(_execute, started))
    _running.add(task)
    task.add_done_callback(_running.discard)


def _execute(started: StartedAutoRun) -> None:
    """Execute one accepted decision, leaving its record to the runner."""
    result = started.execute()
    logger.info("Triggered automation: %s (%s)", result.outcome.value, result.reason)


def _start(config: Config) -> StartedAutoRun | None:
    """Take the automation lease, or None when another run already holds it."""
    try:
        return AutoRunner(config).start_one(reason=TRIGGER_REASON)
    except AutomationAlreadyRunningError:
        return None


def _active_attempt_id(config: Config) -> str | None:
    """The id of the run holding the lease, as far as durable history can tell."""
    last = AutomationStateStore(config.cache.database_path).get_last_attempt()
    return last.id if last is not None and last.outcome is AutoOutcome.RUNNING else None


def _attempt_payload(config: Config, attempt_id: str) -> dict[str, Any] | None:
    """One attempt's live state, plus its run record once generation produced one."""
    attempt = AutomationStateStore(config.cache.database_path).get_attempt(attempt_id)
    if attempt is None:
        return None
    return {
        "attempt_id": attempt.id,
        "state": attempt.outcome.value,
        "reason": attempt.reason,
        "started_at": attempt.started_at.isoformat(),
        "finished_at": attempt.finished_at.isoformat() if attempt.finished_at else None,
        # The generation subprocess reports its pipeline phase back to this row,
        # so a caller polling mid-run sees where it is rather than just "running".
        "phase": attempt.last_phase.value if attempt.last_phase else None,
        "memory_type": attempt.memory_type,
        # Already sanitized by the runner before it was persisted.
        "error": attempt.error,
        "run": _run_payload(config, attempt.run_id),
    }


def _run_payload(config: Config, run_id: str | None) -> dict[str, Any] | None:
    if run_id is None:
        return None
    run = RunDatabase(db_path=config.cache.database_path).get_run(run_id)
    if run is None:
        return None
    record = run.to_dict()
    return {field: record[field] for field in _RUN_FIELDS}


def _refused(config: Config, request: Request) -> JSONResponse | None:
    """The response an ineligible caller gets, or None to let the request through."""
    if not trigger_enabled(config):
        return JSONResponse({"detail": "trigger API is not enabled"}, status_code=404)
    if not _authorized(request, config):
        return JSONResponse({"detail": "invalid trigger token"}, status_code=401)
    return None


def register_trigger_routes(
    server: Any,
    *,
    submit: Callable[[StartedAutoRun], None] = _submit_to_background,
) -> None:
    """Attach the trigger API to a server, with `submit` running accepted decisions."""

    async def trigger(request: Request) -> JSONResponse:
        config = get_config()
        if refused := _refused(config, request):
            return refused

        started = await asyncio.to_thread(_start, config)
        if started is None:
            active = await asyncio.to_thread(_active_attempt_id, config)
            return JSONResponse(
                {"detail": "a run is already active", "attempt_id": active},
                status_code=409,
            )

        submit(started)
        return JSONResponse(
            {
                "status": "accepted",
                "attempt_id": started.attempt.id,
                "status_url": f"{TRIGGER_PATH}/{started.attempt.id}",
            },
            status_code=202,
        )

    async def trigger_status(request: Request) -> JSONResponse:
        config = get_config()
        if refused := _refused(config, request):
            return refused

        attempt_id = request.path_params["attempt_id"]
        payload = await asyncio.to_thread(_attempt_payload, config, attempt_id)
        if payload is None:
            return JSONResponse({"detail": "unknown attempt"}, status_code=404)
        return JSONResponse(payload)

    server.add_api_route(TRIGGER_PATH, trigger, methods=["POST"])
    server.add_api_route(f"{TRIGGER_PATH}/{{attempt_id}}", trigger_status, methods=["GET"])
