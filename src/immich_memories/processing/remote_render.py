"""Authenticated render handoff for an already selected film."""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import math
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import httpx

from immich_memories import __version__
from immich_memories.generate import GenerationError, PreparedGeneration
from immich_memories.generate_delivery import _safe_delivery_message
from immich_memories.processing.assembly_config import AssemblyClip
from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec
from immich_memories.processing.output_contract import (
    FileStamp,
    InvalidOutputArtifact,
    OutputProbe,
    check_output,
)
from immich_memories.processing.remote_render_plan import build_render_request

if TYPE_CHECKING:
    from immich_memories.config_models_render import RenderWorkerConfig
    from immich_memories.generate import GenerationParams

logger = logging.getLogger(__name__)
Progress = Callable[[str, float, str], None]


class RemoteRenderClient:
    """Own one worker connection; return a checked base film, staged for its one decode."""

    def __init__(self, settings: RenderWorkerConfig, *, client: httpx.Client | None = None):
        self.settings = settings
        self._owns_client = client is None
        self._http = client or httpx.Client(
            timeout=httpx.Timeout(min(60, settings.timeout_seconds)),
            follow_redirects=False,
            trust_env=False,
        )

    def __enter__(self) -> RemoteRenderClient:
        return self

    def __exit__(self, *exc: object) -> None:
        if self._owns_client:
            self._http.close()

    def _url(self, route: str) -> str:
        return f"{self.settings.worker_base_url}{route}"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.worker_token}"}

    def _json(self, method: str, route: str, **kwargs) -> dict:
        response = self._http.request(
            method, self._url(route), headers=self._headers, follow_redirects=False, **kwargs
        )
        # The response body may echo a credential on an HTTP error; status alone is enough.
        if response.status_code >= 300:
            raise GenerationError(f"Render worker returned HTTP {response.status_code}")
        return response.json()

    def _safe_message(self, message: Exception | str, params: GenerationParams) -> str:
        result = _safe_delivery_message(message, params.config)
        token = self.settings.worker_token
        return result.replace(token, "***") if token else result

    def health(self) -> dict:
        """Require a ready, compatible worker before sending any film metadata."""
        if not self.settings.enabled or not self.settings.worker_token.strip():
            raise GenerationError("Configure render.worker_base_url and render.worker_token")
        health = self._json("GET", "/health")
        if health.get("app_version") != __version__ or health.get("contract_version") != 1:
            raise GenerationError("Render worker version differs; deploy the same app version")
        if not health.get("ready"):
            raise GenerationError("Render worker is not ready")
        return health

    def render(
        self, params: GenerationParams, output_path: Path, progress: Progress
    ) -> PreparedGeneration:
        """Send the frozen cut, wait within the configured deadline, then retrieve it.

        The film stays staged: music is mixed into it there, and ``publish`` on
        the result decodes it once, or not at all when the worker's decode
        provably covers the same bytes.
        """
        staged = output_path.with_name(f".{output_path.stem}.receiving.mp4")
        # A received film that is returned, or that fails its check, stays on disk.
        keep_staged = False
        try:
            self.health()
            request = build_render_request(params)
            deadline = time.monotonic() + self.settings.timeout_seconds
            status = self._json("POST", "/jobs", json=request)
            status = self._wait(params, request, status, deadline, progress)
            plan = _encoding_plan(status["encoding_plan"])
            clips = _assembly_clips(status["clips"], output_path.parent)
            windows = status.get("music_mute_windows")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            digest, expected_digest = self._download(status["job_id"], staged, deadline)
            if expected_digest and expected_digest != digest:
                raise GenerationError("Render worker output does not match its digest")
            from immich_memories.generate_timeline import validate_final_duration

            try:
                probe = check_output(staged, plan)
            except InvalidOutputArtifact:
                keep_staged = True
                raise
            _validate_result(params, request, status, clips, probe, plan)
            warning = validate_final_duration(params, probe.duration_seconds)
            logger.info("Render worker completed: %s, %.2fs", plan.encoder, probe.duration_seconds)
            for message in status.get("degradations", ()):
                logger.warning("Render worker: %s", self._safe_message(str(message), params))
            keep_staged = True
            return PreparedGeneration(
                path=output_path,
                staged_path=staged,
                verified=_worker_decode(status, probe, staged) if expected_digest else None,
                encoding_plan=plan,
                assembly_clips=clips,
                clips_analyzed=len(params.clips),
                clips_selected=len(clips),
                music_mute_windows=[tuple(window) for window in windows] if windows else None,
                duration_warning=warning,
                render_metrics=probe.render_metrics(plan),
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError) as exc:
            raise GenerationError(
                "Render worker failed: " + self._safe_message(exc, params)
            ) from None
        finally:
            if not keep_staged:
                staged.unlink(missing_ok=True)

    def _wait(self, params, request, status, deadline, progress) -> dict:
        job_id = str(UUID(status["job_id"]))
        while True:
            if (
                status["job_id"] != job_id
                or status["memory_key"] != request["memory_key"]
                or status["plan_digest"] != request["timing"]["sha256"]
            ):
                raise GenerationError("Render worker returned a different cut")
            message = self._safe_message(str(status.get("message", "")), params)[:500]
            progress("assembly", max(0, min(1, float(status.get("progress", 0)))), message)
            if status["state"] == "ready":
                return status
            if status["state"] not in {"queued", "running"}:
                error = self._safe_message(str(status.get("error") or status["state"]), params)
                raise GenerationError(f"Render worker failed: {error[:500]}")
            _check_deadline(deadline)
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
            status = self._json("GET", f"/jobs/{job_id}")

    def _download(self, job_id: str, path: Path, deadline: float) -> tuple[str, str | None]:
        """Write the worker's film to ``path``.

        Returns the SHA-256 of what was written and the one the worker sent with it.
        """
        digest = hashlib.sha256()
        with self._http.stream(
            "GET",
            self._url(f"/jobs/{job_id}/output"),
            headers=self._headers,
            follow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise GenerationError(f"Render worker output returned HTTP {response.status_code}")
            announced = _announced_sha256(response.headers.get("repr-digest"))
            with path.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    _check_deadline(deadline)
                    handle.write(chunk)
                    digest.update(chunk)
        return digest.hexdigest(), announced


def _announced_sha256(header: str | None) -> str | None:
    """The hex SHA-256 in a ``Repr-Digest: sha-256=:<base64>:`` header, if it names one."""
    for member in (header or "").split(","):
        name, _, value = member.strip().partition("=")
        if name != "sha-256":
            continue
        if len(value) < 2 or not value.startswith(":") or not value.endswith(":"):
            raise GenerationError("Render worker sent an unreadable digest")
        try:
            return base64.b64decode(value[1:-1], validate=True).hex()
        except binascii.Error as exc:
            raise GenerationError("Render worker sent an unreadable digest") from exc
    return None


def _worker_decode(status: dict, probe: OutputProbe, staged: Path) -> OutputProbe | None:
    """The worker's decode, stamped for the file here, whose digest matched the worker's."""
    frames = (status.get("probe") or {}).get("decoded_frames")
    if isinstance(frames, bool) or not isinstance(frames, int) or frames <= 0:
        return None
    return replace(probe, decoded_frames=frames, stamp=FileStamp.of(staged))


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise GenerationError("Render worker timed out")


def _encoding_plan(record: dict) -> EncodingPlan:
    return EncodingPlan(
        **(
            record
            | {
                "codec": OutputCodec(record["codec"]),
                "target_transfer": HdrTransfer(record["target_transfer"]),
                "encoder_args": tuple(record["encoder_args"]),
                "codec_substituted_from": OutputCodec(record["codec_substituted_from"])
                if record.get("codec_substituted_from")
                else None,
            }
        )
    )


def _assembly_clips(records: list[dict], directory: Path) -> tuple[AssemblyClip, ...]:
    # Music consumes metadata only. Cleanup must never mistake the final film for a source.
    return tuple(
        AssemblyClip(
            path=directory / ".remote-sources" / str(UUID(record["asset_id"])),
            asset_id=str(UUID(record["asset_id"])),
            duration=float(record["duration"]),
            is_photo=record["is_photo"],
            date=record.get("date"),
            llm_emotion=record.get("llm_emotion"),
            latitude=record.get("latitude"),
            longitude=record.get("longitude"),
            location_name=record.get("location_name"),
        )
        for record in records
    )


def _validate_result(params, request, status, clips, probe, plan) -> None:
    _validate_cut(request, clips)
    _validate_encoding(request["output"], probe, plan)
    expected = _expected_duration(params, request, clips)
    if not math.isclose(probe.duration_seconds, expected, abs_tol=max(0.25, len(clips) / 30)):
        raise GenerationError("Render worker changed the film duration")
    for start, end in status.get("music_mute_windows") or ():
        if not (
            math.isfinite(start)
            and math.isfinite(end)
            and 0 <= start < end <= probe.duration_seconds + 0.1
        ):
            raise GenerationError("Render worker returned an invalid audio window")


def _expected_duration(params, request, clips) -> float:
    from immich_memories.generate_settings import build_title_settings
    from immich_memories.processing.assembly_config import TitleScreenSettings
    from immich_memories.processing.editorial_timing import read_editorial_timeline
    from immich_memories.processing.timeline_preview import preview_timeline

    timeline = read_editorial_timeline(request["timing"])
    frozen = replace(params, timeline_plan=timeline)
    titles = build_title_settings(frozen, frozen.config, list(clips)) or TitleScreenSettings(
        enabled=False,
        divider_mode="none",
        show_month_dividers=False,
        show_location_cards=False,
    )
    _, duration = preview_timeline(
        list(clips),
        timeline,
        titles,
        params.transition,
        params.transition_duration,
    )
    return duration


def _validate_cut(request, clips) -> None:
    expected_ids = [clip["asset_id"] for clip in request["plan"]["clips"]]
    if [clip.asset_id for clip in clips] != expected_ids:
        raise GenerationError("Render worker changed the selected clips")
    for clip, chosen in zip(clips, request["plan"]["clips"], strict=True):
        if not math.isclose(clip.duration, chosen["end"] - chosen["start"], abs_tol=0.07):
            raise GenerationError("Render worker changed a selected interval")


def _validate_encoding(output, probe, plan) -> None:
    from immich_memories.processing.output_canvas import resolve_output_canvas

    canvas = resolve_output_canvas(
        resolution=output["resolution"],
        orientation=output["orientation"],
        configured_resolution=(1920, 1080),
        clips=[],
    )
    if (probe.width, probe.height) != (canvas.width, canvas.height):
        raise GenerationError("Render worker changed the output canvas")
    if plan.container != "mp4":
        raise GenerationError("Render worker returned an unexpected output format")
    expected_codec = output["codec"]
    if plan.codec.value != expected_codec and (
        output["codec_policy"] == "strict"
        or plan.codec_substituted_from != OutputCodec(expected_codec)
    ):
        raise GenerationError("Render worker changed the requested codec")
    hdr_mode = output["hdr_mode"]
    if (hdr_mode == "hdr" and not plan.hdr) or (hdr_mode == "sdr" and plan.hdr):
        raise GenerationError("Render worker changed the requested dynamic range")
