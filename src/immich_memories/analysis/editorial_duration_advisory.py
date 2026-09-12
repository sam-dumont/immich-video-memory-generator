"""Public duration advisories from the planner's recorded outcome."""

from collections.abc import Mapping

from immich_memories.analysis.editorial_numbers import exact_number


def editorial_duration_warning(realization: Mapping[str, object] | None) -> str | None:
    """Report a recorded shortfall without inferring that the library is exhausted."""
    if not isinstance(realization, Mapping):
        return None
    status = realization.get("status")
    if status not in {"editorial_shortfall", "search_limited"}:
        return None
    values = tuple(
        realization.get(key)
        for key in ("selected_content_seconds", "content_budget_seconds", "requested_seconds")
    )
    if any((number := exact_number(value)) is None or number < 0 for value in values):
        return None
    selected, budget, requested = values
    prefix = "Selection stopped early. " if status == "search_limited" else ""
    return (
        f"{prefix}Selected {selected:.1f}s of pictures and video for a {requested:.1f}s memory "
        f"({budget:.1f}s available for content). More usable material may remain."
    )
