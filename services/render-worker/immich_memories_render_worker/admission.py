"""Identity and the binding gate, both decided before a single byte is fetched."""

from uuid import UUID, uuid5

from immich_memories_render_worker.models import RenderRequest

# One fixed namespace, so two callers holding the same cut name the same job.
_JOBS = UUID("6f2a1d7c-9c2e-4f55-9f4a-1a0f6f0b9c31")


class EnvelopeDrift(ValueError):
    """The envelope disagrees with the binding it carries."""


def job_identity(request: RenderRequest) -> UUID:
    """Name the job after the cut, never after a caller-chosen correlation id."""
    return uuid5(_JOBS, f"{request.memory_key}\n{request.timing.sha256}")


def envelope_policy(request: RenderRequest):
    """The timing policy this envelope describes, derived exactly as the app derives its own."""
    from immich_memories.processing.editorial_timing import build_editorial_timing_policy
    from immich_memories_render_worker.native_plan import worker_config

    return build_editorial_timing_policy(
        config=worker_config(request),
        target_seconds=request.memory.target_duration_seconds,
        memory_type=request.memory.memory_type,
        date_start=request.memory.date_start,
        date_end=request.memory.date_end,
        person_name=request.memory.person_name,
        memory_preset_params=request.memory.preset_params,
        transition=request.plan.transition,
        transition_duration=request.plan.transition_duration,
    )


def certify_envelope(request: RenderRequest) -> None:
    """Refuse a job whose envelope drifted from its own binding, before any media work."""
    from immich_memories.processing.editorial_timing import read_editorial_timeline

    binding = request.timing.model_dump(mode="json")
    try:
        read_editorial_timeline(binding)
    except ValueError as exc:
        raise EnvelopeDrift(str(exc)) from exc
    if binding["source_ids"] != [str(clip.asset_id) for clip in request.plan.clips]:
        raise EnvelopeDrift("Editorial timing selection changed; replan before rendering")
    if binding["policy"] != envelope_policy(request).as_dict():
        raise EnvelopeDrift("Editorial timing settings changed; replan before rendering")
    certified = {}
    for clip in request.plan.clips:
        if clip.live is not None:
            if clip.render_mode != "motion" or clip.live.selected_interval != (
                clip.start,
                clip.end,
            ):
                raise EnvelopeDrift("Live directive changed its certified interval")
            certified[clip.asset_id] = clip.live.selected_interval
    if request.certified_content_intervals != certified:
        raise EnvelopeDrift("Certified Live intervals require their matching carrier certificates")
