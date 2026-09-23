"""One GPU lane, bounded admission and validated, single-use artifacts."""

import hashlib
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from immich_memories.processing.output_contract import validate_output
from immich_memories.security import credential_fingerprint, sanitize_error_message
from immich_memories_render_worker.admission import job_identity
from immich_memories_render_worker.models import JobStatus, RenderRequest
from immich_memories_render_worker.renderer import RenderArtifact, Renderer
from immich_memories_render_worker.settings import WorkerSettings
from immich_memories_render_worker.store import JobJournal, JobRepository, MemoryJobRepository


def _now() -> datetime:
    return datetime.now(UTC)


def file_sha256(path: Path) -> str:
    """Digest of the film as published, so the app can prove it holds the decoded bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _plan_record(artifact: RenderArtifact, probe) -> dict:
    plan = artifact.encoding_plan
    # The stamp names an inode on this machine; it means nothing to the app.
    probe_record = {key: value for key, value in asdict(probe).items() if key != "stamp"}
    return {
        "encoding_plan": {
            **asdict(plan),
            "codec": plan.codec.value,
            "target_transfer": plan.target_transfer.value,
            "encoder_args": list(plan.encoder_args),
            "codec_substituted_from": (
                plan.codec_substituted_from.value if plan.codec_substituted_from else None
            ),
        },
        "probe": probe_record,
        "render_metrics": probe.render_metrics(plan),
        "encoder": plan.encoder,
        "music_mute_windows": artifact.music_mute_windows,
        "clips": artifact.clips,
        "degradations": artifact.degradations,
    }


class RenderJobs:
    def __init__(
        self, settings: WorkerSettings, renderer: Renderer, repository: JobRepository | None = None
    ):
        self.settings = settings
        self.renderer = renderer
        self.worker_id = uuid4()
        self.started_at = _now()
        scratch = settings.directory / "render-jobs"
        scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        _sweep(scratch, settings.retention_seconds)
        self.store = repository or MemoryJobRepository(
            settings.max_jobs,
            settings.retention_seconds,
            JobJournal(scratch / "records"),
        )
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="render")
        # WHY: a hard kill cannot run cleanup, so the boot sweep above is the
        # only thing that ever removes a stranded session's downloaded originals.
        self._session = TemporaryDirectory(
            prefix="session-", dir=scratch, ignore_cleanup_errors=True
        )
        self.root = Path(os.path.realpath(self._session.name))

    def health(self) -> dict:
        from immich_memories import __version__

        capabilities = self._pool.submit(self.renderer.health).result()
        return capabilities | {
            "app_version": __version__,
            "contract_version": 1,
            "worker_id": str(self.worker_id),
            "started_at": self.started_at.isoformat(),
        }

    def submit(self, request: RenderRequest) -> JobStatus:
        self.cleanup()
        job_id = job_identity(request)
        material = request.model_dump(mode="json")
        # A resubmitted cut is recognised without ever storing the scoped key.
        material["immich"]["api_key"] = credential_fingerprint(
            request.immich.api_key.get_secret_value()
        )
        fingerprint = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
        status, fresh = self.store.admit(
            JobStatus(
                job_id=job_id,
                memory_key=request.memory_key,
                plan_digest=request.timing.sha256,
                worker_id=self.worker_id,
                submitted_at=_now(),
            ),
            fingerprint,
        )
        if fresh:
            self._pool.submit(self._render, request, job_id)
        return status

    def directory(self, job_id: UUID) -> Path:
        # WHY realpath + startswith rather than Path.resolve().is_relative_to: the
        # same check, in the normalise-then-compare form CodeQL's path-injection
        # query recognises; the separator keeps a "session-x-evil" sibling out.
        root = str(self.root)
        path = os.path.realpath(os.path.join(root, str(job_id)))
        if not path.startswith(root + os.sep):
            raise ValueError("Job id resolves outside the worker workspace")
        return Path(path)

    def _render(self, request: RenderRequest, job_id: UUID) -> None:
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
            self.store.update(job_id, state="running", started_at=_now())
            self._publish(job_id, directory, self.renderer.render(request, directory, progress))
        except (
            Exception
        ) as exc:  # WHY: one failed job must not kill the worker or expose its scoped key.
            self.store.update(
                job_id, state="failed", phase="failed", error=clean(str(exc)), finished_at=_now()
            )
            shutil.rmtree(directory, ignore_errors=True)

    def _publish(self, job_id: UUID, directory: Path, artifact: RenderArtifact) -> None:
        if not artifact.path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Renderer returned a file outside its job workspace")
        # One decode per film on this side: a renderer that already decoded
        # these bytes vouches for them, and only a changed file is decoded again.
        probe = validate_output(artifact.path, artifact.encoding_plan, verified=artifact.probe)
        if self.store.get(job_id).state != "running":
            raise RuntimeError("Job was already closed before its output arrived")
        os.link(artifact.path, directory / "film.mp4")
        self.store.update(
            job_id,
            state="ready",
            phase="complete",
            progress=1,
            message="Ready to retrieve",
            finished_at=_now(),
            output_sha256=file_sha256(artifact.path),
            **_plan_record(artifact, probe),
        )

    def output(self, job_id: UUID) -> tuple[Path, str | None]:
        """Claim the film once; return it with the SHA-256 recorded when it was published."""
        claimed = self.store.claim(job_id)
        return self.directory(job_id) / "film.mp4", claimed.output_sha256

    def discard(self, job_id: UUID) -> None:
        shutil.rmtree(self.directory(job_id), ignore_errors=True)

    def cleanup(self) -> None:
        for job_id in self.store.overdue(self.settings.job_timeout_seconds):
            self.store.update(
                job_id,
                state="failed",
                phase="failed",
                finished_at=_now(),
                error=(
                    f"Render exceeded {self.settings.job_timeout_seconds}s and was abandoned; "
                    "restart the worker if its lane stays busy"
                ),
            )
            self.discard(job_id)
        for job_id in self.store.expire():
            self.discard(job_id)

    def close(self) -> None:
        # WHY: shutdown(wait=True) hangs forever behind a wedged ffmpeg, which is
        # exactly the job the timeout above already gave up on.
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._session.cleanup()


def _sweep(scratch: Path, retention_seconds: int) -> None:
    """Drop every session a previous process could not clean up, and stale records."""
    for entry in scratch.glob("session-*"):
        shutil.rmtree(entry, ignore_errors=True)
    records = scratch / "records"
    if not records.is_dir():
        return
    cutoff = datetime.now(UTC).timestamp() - retention_seconds
    for entry in records.iterdir():
        if entry.is_file() and entry.stat().st_mtime < cutoff:
            entry.unlink(missing_ok=True)
