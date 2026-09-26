"""Ask the model for a title on the CLI path, and decide when to.

A memory about people or an occasion is named by the model as soon as a reader
is configured: the template opens a three-person film on a date span with three
full names stacked underneath, and the family record holds enough to write
"<child> and her grandparents" instead. Trips keep their own prompt and their
own opt-in, and `--no-llm-title` pins the template for the contact-sheet
matrix, where a run that starts inventing titles makes every run before and
after it incomparable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from immich_memories.config_loader import Config
    from immich_memories.timeperiod import DateRange

from immich_memories.titles.title_source import TitleSource, override_source

logger = logging.getLogger(__name__)

__all__ = ["resolve_cli_title"]


def _descriptions(clips: list[Any]) -> list[str]:
    """What the analyzer said about each selected clip, for the prompt."""
    return [d for c in clips if (d := getattr(c, "llm_description", None))]


def _ask_the_llm(**kwargs: Any) -> Any:
    from immich_memories.titles.llm_titles import generate_title_with_llm

    return asyncio.run(generate_title_with_llm(**kwargs))


def _album_of_the_cut(lookup: Callable[[], str | None] | None) -> str | None:
    """The album the cut mostly sits in, when the run can ask Immich for it."""
    if lookup is None:
        return None
    try:
        return lookup()
    except Exception:  # WHY: a title fact must not fail the whole run
        logger.debug("Album lookup failed; the title goes without it", exc_info=True)
        return None


def _asks_the_model(*, enabled: bool | None, memory_type: str | None, configured: bool) -> bool:
    """Whether this run should put the question to the reader at all."""
    if enabled is False:
        return False
    if not configured:
        if enabled:
            logger.warning("--llm-title needs a configured LLM; using the template title")
        return False
    if enabled:
        return True

    from immich_memories.titles.llm_titles import OCCASION_MEMORY_TYPES, PEOPLE_MEMORY_TYPES

    return memory_type in PEOPLE_MEMORY_TYPES or memory_type in OCCASION_MEMORY_TYPES


def resolve_cli_title(
    *,
    enabled: bool | None,
    title_override: str | None,
    subtitle_override: str | None = None,
    clips: list[Any],
    config: Config,
    memory_type: str | None,
    date_range: DateRange,
    person_names: list[str],
    memory_preset_params: dict | None = None,
    album_lookup: Callable[[], str | None] | None = None,
    ask: Callable[..., Any] = _ask_the_llm,
) -> tuple[str | None, str | None, TitleSource | None]:
    """Return the (title, subtitle, source) the run should use.

    Owns the whole precedence so the caller gains no branches: an explicit
    title wins, then the model's, then the template (signalled by ``None``,
    with no source: the template layers name it later). The subtitle falls
    back to ``subtitle_override`` on every path.
    """
    if title_override:
        source = override_source(title_override, memory_type, memory_preset_params)
        return title_override, subtitle_override, source

    llm_config = config.title_llm if config.title_llm and config.title_llm.model else config.llm
    if not _asks_the_model(
        enabled=enabled,
        memory_type=memory_type,
        configured=bool(llm_config.model),
    ):
        return None, subtitle_override, None

    from dataclasses import replace

    from immich_memories.titles.llm_titles import memory_title_facts

    facts = memory_title_facts(memory_preset_params)
    if facts.album_name is None and memory_type != "album":
        facts = replace(facts, album_name=_album_of_the_cut(album_lookup))

    start, end = date_range.start.date(), date_range.end.date()
    try:
        suggestion = ask(
            memory_type=memory_type or "year",
            locale=config.title_screens.locale if config.title_screens else "en",
            start_date=str(start),
            end_date=str(end),
            duration_days=(end - start).days,
            person_names=person_names or None,
            clip_descriptions=_descriptions(clips) or None,
            facts=facts,
            llm_config=llm_config,
        )
    except Exception:  # WHY: an optional title must not fail the whole run
        logger.warning("LLM title generation failed; using the template title", exc_info=True)
        return None, subtitle_override, None

    if not suggestion or not getattr(suggestion, "title", None):
        return None, subtitle_override, None
    subtitle = getattr(suggestion, "subtitle", None) or subtitle_override
    return suggestion.title, subtitle, TitleSource.MODEL
