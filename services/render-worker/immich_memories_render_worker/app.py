"""Authenticated HTTP surface for the isolated render worker."""

import asyncio
import secrets
from contextlib import asynccontextmanager, suppress
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from immich_memories_render_worker.admission import EnvelopeDrift, certify_envelope
from immich_memories_render_worker.jobs import RenderJobs
from immich_memories_render_worker.models import RenderRequest
from immich_memories_render_worker.renderer import Renderer
from immich_memories_render_worker.settings import WorkerSettings
from immich_memories_render_worker.store import (
    JobConflict,
    JobExpired,
    JobNotFound,
    JobRepository,
    OutputConsumed,
    QueueFull,
)


def create_app(
    settings: WorkerSettings, *, renderer: Renderer, repository: JobRepository | None = None
) -> FastAPI:
    """Build the private job API with one renderer and atomic job storage."""
    jobs = RenderJobs(settings, renderer, repository)

    async def authorize(authorization: str | None = Header(default=None)) -> None:
        supplied = (
            authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        )
        if not secrets.compare_digest(
            supplied.encode(), settings.token.get_secret_value().encode()
        ):
            raise HTTPException(
                401, "Worker authentication required", headers={"WWW-Authenticate": "Bearer"}
            )

    async def reap():
        while True:
            await asyncio.sleep(10)
            jobs.cleanup()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        reaper = asyncio.create_task(reap())
        try:
            app.state.capabilities = await asyncio.to_thread(jobs.health)
            yield
        finally:
            reaper.cancel()
            with suppress(asyncio.CancelledError):
                await reaper
            await asyncio.to_thread(jobs.close)

    app = FastAPI(
        title="Immich Memories render worker", lifespan=lifespan, dependencies=[Depends(authorize)]
    )

    @app.get("/health")
    def health():
        return app.state.capabilities

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, exc):
        # Pydantic's input field can echo the whole body, including the scoped key.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {key: row[key] for key in ("loc", "msg", "type")} for row in exc.errors()
                ]
            },
        )

    def _absent(reason: str, detail: str) -> JSONResponse:
        # A restart, an expiry and an id nobody ever submitted all used to answer
        # the same bare 404. The boot time is what lets a caller tell the first
        # apart: a job submitted before it did not survive this process.
        return JSONResponse(
            status_code=404,
            content={
                "detail": detail,
                "reason": reason,
                "worker_id": str(jobs.worker_id),
                "worker_started_at": jobs.started_at.isoformat(),
            },
        )

    @app.exception_handler(JobNotFound)
    async def not_found(_request, _exc):
        return _absent("unknown", "Job not found")

    @app.exception_handler(JobExpired)
    async def gone(_request, _exc):
        return _absent("expired", "Job result expired and was removed")

    @app.exception_handler(EnvelopeDrift)
    async def drifted(_request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(JobConflict)
    async def conflict(_request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(OutputConsumed)
    async def consumed(_request, exc):
        return JSONResponse(status_code=410, content={"detail": str(exc)})

    @app.exception_handler(QueueFull)
    async def full(_request, exc):
        return JSONResponse(status_code=429, content={"detail": str(exc)})

    @app.post("/jobs", status_code=202)
    def submit(request: RenderRequest):
        if not app.state.capabilities.get("ready"):
            raise HTTPException(503, "Renderer is not ready")
        if str(request.immich.url).rstrip("/") != str(settings.immich_url).rstrip("/"):
            raise HTTPException(422, "Immich URL does not match worker configuration")
        certify_envelope(request)
        return jobs.submit(request)

    @app.get("/jobs/{job_id}")
    def status(job_id: UUID):
        jobs.cleanup()
        return jobs.store.get(job_id)

    @app.get("/jobs/{job_id}/output")
    def output(job_id: UUID):
        jobs.cleanup()
        path = jobs.output(job_id)
        return FileResponse(
            path,
            media_type="video/mp4",
            filename="memory.mp4",
            background=BackgroundTask(jobs.discard, job_id),
        )

    return app
