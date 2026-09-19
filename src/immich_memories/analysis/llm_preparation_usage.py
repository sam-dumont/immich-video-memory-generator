"""Preparation attempts share the run's counters, including unmetered failures."""

from collections.abc import Mapping

from immich_memories.analysis import llm_metrics


def _tokens(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def record_preparation_attempt(body: object, *, stage: str, elapsed_seconds: float) -> None:
    """Keep reported tokens even for unusable content; absent usage remains unknown."""
    reply = body if isinstance(body, Mapping) else {}
    usage = reply.get("usage")
    usage = usage if isinstance(usage, Mapping) else {}
    prompt = _tokens(usage.get("prompt_tokens"))
    output = _tokens(usage.get("completion_tokens"))
    prompt_details = usage.get("prompt_tokens_details")
    prompt_details = prompt_details if isinstance(prompt_details, Mapping) else {}
    output_details = usage.get("completion_tokens_details")
    output_details = output_details if isinstance(output_details, Mapping) else {}
    model = reply.get("model")
    llm_metrics.record_reply(
        prompt_tokens=prompt or 0,
        completion_tokens=output or 0,
        cached_prompt_tokens=_tokens(prompt_details.get("cached_tokens")) or 0,
        reasoning_tokens=_tokens(output_details.get("reasoning_tokens")) or 0,
        model=model if isinstance(model, str) else None,
        stage=stage,
        usage_known=prompt is not None and output is not None,
    )
    llm_metrics.record_wall(elapsed_seconds)
