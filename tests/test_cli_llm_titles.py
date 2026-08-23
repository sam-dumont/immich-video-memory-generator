"""The CLI can ask the LLM for a title, but only when told to.

The wizard has generated LLM titles since #505's audit; the CLI used templates
or `--title`. Bringing it across has to be opt-in: the contact-sheet matrix
runs the CLI, so a default that starts inventing titles would make every run
before and after incomparable.
"""

from __future__ import annotations

from datetime import datetime

from immich_memories.cli._llm_title import resolve_cli_title
from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange
from tests.conftest import make_clip

_RANGE = DateRange(start=datetime(2025, 7, 1), end=datetime(2025, 7, 14))


def _config_with_llm() -> Config:
    config = Config()
    config.llm.model = "some-model"
    return config


def test_the_cli_default_never_asks_the_llm_for_a_title() -> None:
    """Off by default, even with an LLM configured — the matrix depends on it."""
    called = []

    title, subtitle = resolve_cli_title(
        enabled=False,
        title_override=None,
        clips=[make_clip("clip-1")],
        config=_config_with_llm(),
        memory_type="year_in_review",
        date_range=_RANGE,
        person_name=None,
        ask=lambda **kwargs: called.append(kwargs),
    )

    assert (title, subtitle) == (None, None)
    assert called == []


def test_an_explicit_title_outranks_the_llm() -> None:
    """--title is the user typing the answer; nothing should overrule it."""
    called = []

    title, subtitle = resolve_cli_title(
        enabled=True,
        title_override="Our Summer",
        clips=[make_clip("clip-1")],
        config=_config_with_llm(),
        memory_type="year_in_review",
        date_range=_RANGE,
        person_name=None,
        ask=lambda **kwargs: called.append(kwargs),
    )

    assert title == "Our Summer"
    assert called == []


def test_the_flag_carries_the_clip_descriptions_into_the_ask() -> None:
    """The analyzer already described every selected clip; the prompt gets them."""
    from types import SimpleNamespace

    seen: dict = {}

    def ask(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(title="A Fortnight in July", subtitle="2025")

    clip = make_clip("clip-1")
    clip.llm_description = "children running through a sprinkler"

    title, subtitle = resolve_cli_title(
        enabled=True,
        title_override=None,
        clips=[clip],
        config=_config_with_llm(),
        memory_type="year_in_review",
        date_range=_RANGE,
        person_name="Emma",
        ask=ask,
    )

    assert (title, subtitle) == ("A Fortnight in July", "2025")
    assert seen["clip_descriptions"] == ["children running through a sprinkler"]
    assert seen["person_names"] == ["Emma"]
    assert seen["duration_days"] == 13


def test_a_missing_llm_leaves_the_template_alone() -> None:
    """Asking for an LLM title without a model configured must not fail the run."""
    title, subtitle = resolve_cli_title(
        enabled=True,
        title_override=None,
        clips=[make_clip("clip-1")],
        config=Config(),
        memory_type="year_in_review",
        date_range=_RANGE,
        person_name=None,
        ask=lambda **_kwargs: None,
    )

    assert (title, subtitle) == (None, None)
