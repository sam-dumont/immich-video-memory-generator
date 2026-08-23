"""Ask the LLM for a title on the CLI path, when the run asked for one.

Kept opt-in rather than mirroring the wizard's "an LLM is configured, so use
it" rule. The contact-sheet matrix runs the CLI, and a default that started
inventing titles would make every run before and after it incomparable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from immich_memories.config_loader import Config
    from immich_memories.timeperiod import DateRange

logger = logging.getLogger(__name__)

__all__ = ["resolve_cli_title"]


def _descriptions(clips: list[Any]) -> list[str]:
    """What the analyzer said about each selected clip, for the prompt."""
    return [d for c in clips if (d := getattr(c, "llm_description", None))]


def _ask_the_llm(**kwargs: Any) -> Any:
    from immich_memories.titles.llm_titles import generate_title_with_llm

    return asyncio.run(generate_title_with_llm(**kwargs))


def resolve_cli_title(
    *,
    enabled: bool,
    title_override: str | None,
    subtitle_override: str | None = None,
    clips: list[Any],
    config: Config,
    memory_type: str | None,
    date_range: DateRange,
    person_name: str | None,
    ask: Callable[..., Any] = _ask_the_llm,
) -> tuple[str | None, str | None]:
    """Return the (title, subtitle) the run should use.

    Owns the whole precedence so the caller gains no branches: an explicit
    title wins, then the LLM's, then the template (signalled by ``None``). The
    subtitle falls back to ``subtitle_override`` on every path.
    """
    if title_override:
        return title_override, subtitle_override
    if not enabled:
        return None, subtitle_override

    llm_config = config.title_llm if config.title_llm and config.title_llm.model else config.llm
    if not llm_config.model:
        logger.warning("--llm-title needs an LLM configured; using the template title")
        return None, subtitle_override

    start, end = date_range.start.date(), date_range.end.date()
    try:
        suggestion = ask(
            memory_type=memory_type or "year",
            locale=config.title_screens.locale if config.title_screens else "en",
            start_date=str(start),
            end_date=str(end),
            duration_days=(end - start).days,
            person_names=[person_name] if person_name else None,
            clip_descriptions=_descriptions(clips) or None,
            llm_config=llm_config,
        )
    except Exception:  # WHY: an optional title must not fail the whole run
        logger.warning("LLM title generation failed; using the template title", exc_info=True)
        return None, subtitle_override

    if not suggestion or not getattr(suggestion, "title", None):
        return None, subtitle_override
    return suggestion.title, getattr(suggestion, "subtitle", None) or subtitle_override
