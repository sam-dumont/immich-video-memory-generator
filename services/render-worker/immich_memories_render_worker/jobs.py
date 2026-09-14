"""One GPU lane, bounded admission and validated, single-use artifacts."""

import hashlib
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from immich_memories.processing.output_contract import validate_output
from immich_memories.security import sanitize_error_message
from immich_memories_render_worker.models import JobStatus, RenderRequest
from immich_memories_render_worker.renderer import Renderer
from immich_memories_render_worker.settings import WorkerSettings
from immich_memories_render_worker.store import JobRepository, MemoryJobRepository


class RenderJobs:
    def __init__(
        self, settings: WorkerSettings, renderer: Renderer, repository: JobRepository | None = None
    ):
        self.settings = settings
        self.renderer = renderer
        self.store = repository or MemoryJobRepository(
            settings.max_jobs, settings.retention_seconds
        )
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="render")
        scratch = settings.directory / "render-jobs"
        scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._session = TemporaryDirectory(prefix="session-", dir=scratch)
        self.root = Path(self._session.name)

    def health(self) -> dict:
        return self._pool.submit(self.renderer.health).result()

    def submit(self, request: RenderRequest) -> JobStatus:
        self.cleanup()
        material = request.model_dump(mode="json")
        material["immich"]["api_key"] = hashlib.sha256(
            request.immich.api_key.get_secret_value().encode()
        ).hexdigest()
        fingerprint = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
        status, fresh = self.store.admit(request.request_id, request.memory_key, fingerprint)
        if fresh:
            self._pool.submit(self._render, request)
        return status

    def directory(self, job_id: UUID) -> Path:
        return self.root / str(job_id)

    def _render(self, request: RenderRequest) -> None:
        job_id = request.request_id
        directory = self.directory(job_id)
        secret = request.immich.api_key.get_secret_value()

        def clean(text: str) -> str:
            return sanitize_error_message(text.replace(secret, "[redacted]"))[:500]

        def progress(phase: str, fraction: float, message: str) -> None:
            self.store.update(
                job_id,
                phase=clean(phase),
                progress=max(0, min(1, fraction)),
                message=clean(message),
            )

        try:
            directory.mkdir(mode=0o700)
            self.store.update(job_id, state="running")
            result = self.renderer.render(request, directory, progress)
            if not result.path.resolve().is_relative_to(directory.resolve()):
                raise ValueError("Renderer returned a file outside its job workspace")
            validate_output(result.path, result.encoding_plan)
            os.link(result.path, directory / "film.mp4")
            self.store.update(
                job_id, state="ready", phase="complete", progress=1, message="Ready to retrieve"
            )
        except (
            Exception
        ) as exc:  # WHY: one failed job must not kill the worker or expose its scoped key.
            self.store.update(job_id, state="failed", phase="failed", error=clean(str(exc)))
            shutil.rmtree(directory, ignore_errors=True)

    def output(self, job_id: UUID) -> Path:
        self.store.claim(job_id)
        return self.directory(job_id) / "film.mp4"

    def discard(self, job_id: UUID) -> None:
        shutil.rmtree(self.directory(job_id), ignore_errors=True)

    def cleanup(self) -> None:
        for job_id in self.store.expire():
            self.discard(job_id)

    def close(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)
        self._session.cleanup()
