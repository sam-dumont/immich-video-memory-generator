"""Use the application's existing renderer on a CUDA/NVENC host."""

from collections.abc import Callable

from immich_memories.processing.hardware_detection import detect_hardware_acceleration


def gpu_capabilities() -> dict:
    """Probe working encoders and the title kernel backend on the render thread."""
    from immich_memories.titles.generator import TitleScreenConfig
    from immich_memories.titles.rendering_service import RenderingService

    hardware = detect_hardware_acceleration("nvidia")
    titles = RenderingService(TitleScreenConfig()).backend
    encoders = []
    if hardware.cuda_available:
        if hardware.supports_h264_encode:
            encoders.append("h264_nvenc")
        if hardware.supports_h265_encode:
            encoders.append("hevc_nvenc")
    return {"titles": titles, "encoders": encoders}


def _wanted_encoder(request) -> str:
    return "h264_nvenc" if request.output.codec == "h264" else "hevc_nvenc"


class NativeRenderer:
    """GPU policy is fixed by the worker, never selected in a job request."""

    def __init__(self, *, capabilities: Callable[[], dict] = gpu_capabilities):
        self._capabilities = capabilities

    def health(self) -> dict:
        """Report whether the card is doing the work, separately from being able to render.

        Measured on one cluster node, same cut, same reader, 2026-09-14: NVENC
        219 s against libx264 258 s. The card is worth about 1.65x on the encode
        stage and about 15% of the render, because fetching the originals is
        roughly half of it. A worker that cannot open NVENC is therefore 15%
        slower, which is a degradation to report, not a reason to refuse a film.
        """
        capabilities = self._capabilities()
        return capabilities | {
            "ready": True,
            "accelerated": capabilities["titles"] == "CUDA" and bool(capabilities["encoders"]),
        }

    def render(self, request, directory, progress):
        """Fetch the selected sources directly and render without selecting or uploading."""
        from immich_memories.api.sync_client import SyncImmichClient
        from immich_memories.generate import generate_memory
        from immich_memories.processing.output_contract import DecodeCheck
        from immich_memories.tracking import RunTracker
        from immich_memories_render_worker.admission import job_identity
        from immich_memories_render_worker.native_plan import generation_params
        from immich_memories_render_worker.renderer import RenderArtifact

        wanted = _wanted_encoder(request)
        capabilities = self.health()
        degradations = _capability_degradations(capabilities, wanted)
        with SyncImmichClient(
            str(request.immich.url), request.immich.api_key.get_secret_value()
        ) as client:
            params = generation_params(request, directory, client, progress)
            tracker = RunTracker(
                str(job_identity(request)),
                db_path=params.config.cache.database_path,
                capture_system=False,
            )
            result = generate_memory(params, run_tracker=tracker, defer_finalization=True)
        actual_ids = [clip.asset_id for clip in result.assembly_clips if not clip.is_title_screen]
        if actual_ids != [str(clip.asset_id) for clip in request.plan.clips]:
            raise RuntimeError("Renderer changed the selected cut; output withheld")
        plan = result.encoding_plan
        if plan.encoder != wanted:
            degradations.append(f"encoded with {plan.encoder} instead of {wanted}")
        # The worker's one decode of this film; the job publisher reuses it.
        probe = result.publish(
            DecodeCheck(
                encode_seconds=result.encode_seconds,
                progress=lambda message: progress("check", 1.0, message),
            )
        )
        tracker.complete_artifact(
            result.path,
            probe,
            [],
            clips_analyzed=result.clips_analyzed,
            clips_selected=result.clips_selected,
        )
        return RenderArtifact(
            result.path,
            plan,
            probe=probe,
            music_mute_windows=result.music_mute_windows,
            clips=tuple(
                {
                    "asset_id": clip.asset_id,
                    "duration": clip.duration,
                    "is_photo": clip.is_photo,
                    "date": clip.date,
                    "llm_emotion": clip.llm_emotion,
                    "latitude": clip.latitude,
                    "longitude": clip.longitude,
                    "location_name": clip.location_name,
                }
                for clip in result.assembly_clips
                if not clip.is_title_screen
            ),
            degradations=tuple(degradations),
        )


def _capability_degradations(capabilities: dict, wanted: str) -> list[str]:
    reported = []
    if capabilities.get("titles") != "CUDA":
        reported.append(f"title screens rendered on {capabilities.get('titles') or 'unknown'}")
    if wanted not in capabilities.get("encoders", ()):
        reported.append(f"{wanted} is not available on this host")
    return reported
