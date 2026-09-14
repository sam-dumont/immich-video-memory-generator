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


class NativeRenderer:
    """GPU policy is fixed by the worker, never selected in a job request."""

    def __init__(self, *, capabilities: Callable[[], dict] = gpu_capabilities):
        self._capabilities = capabilities

    def health(self) -> dict:
        """Only advertise readiness when titles and at least one encoder use NVIDIA."""
        capabilities = self._capabilities()
        return capabilities | {
            "ready": capabilities["titles"] == "CUDA" and bool(capabilities["encoders"])
        }

    def render(self, request, directory, progress):
        """Fetch the selected sources directly and render without selecting or uploading."""
        from immich_memories.api.sync_client import SyncImmichClient
        from immich_memories.generate import generate_memory
        from immich_memories.processing.output_contract import validate_output
        from immich_memories.tracking import RunTracker
        from immich_memories_render_worker.native_plan import generation_params
        from immich_memories_render_worker.renderer import RenderArtifact

        expected_encoder = "h264_nvenc" if request.output.codec == "h264" else "hevc_nvenc"
        capabilities = self.health()
        if not capabilities["ready"] or expected_encoder not in capabilities["encoders"]:
            raise RuntimeError("CUDA titles and the requested NVENC encoder are required")
        with SyncImmichClient(
            str(request.immich.url), request.immich.api_key.get_secret_value()
        ) as client:
            params = generation_params(request, directory, client, progress)
            tracker = RunTracker(
                str(request.request_id),
                db_path=params.config.cache.database_path,
                capture_system=False,
            )
            result = generate_memory(params, run_tracker=tracker, defer_finalization=True)
        actual_ids = [clip.asset_id for clip in result.assembly_clips if not clip.is_title_screen]
        if actual_ids != [str(clip.asset_id) for clip in request.plan.clips]:
            raise RuntimeError("Renderer changed the selected cut; output withheld")
        if result.encoding_plan.encoder != expected_encoder:
            raise RuntimeError(
                "Renderer fell back from the requested NVENC encoder; output withheld"
            )
        probe = validate_output(result.path, result.encoding_plan)
        tracker.complete_artifact(
            result.path,
            probe,
            [],
            clips_analyzed=result.clips_analyzed,
            clips_selected=result.clips_selected,
        )
        return RenderArtifact(result.path, result.encoding_plan)
