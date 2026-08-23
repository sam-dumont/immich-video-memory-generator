"""The config reference page must list exactly the keys the schema accepts."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_config_docs import (  # noqa: E402
    PAGE,
    SectionSchema,
    documented_keys,
    find_drift,
    schema_sections,
)

from immich_memories.config_loader import Config  # noqa: E402


def test_yaml_block_keys_are_read_as_section_and_field_names() -> None:
    page = """
# Cache

```yaml
cache:
  directory: "~/.immich-memories/cache"
  max_age_days: 30               # Analysis cache expiry (1-365)
```
"""

    assert documented_keys(page) == {"cache": {"directory", "max_age_days"}}


def test_sections_shown_under_the_advanced_wrapper_count_as_documented() -> None:
    """`advanced:` is the YAML layout of tier-2 sections, not a section of its own."""
    page = """
```yaml
advanced:
  analysis:
    scene_threshold: 25.0
  hardware:
    encoder_preset: "quality"
```
"""

    assert documented_keys(page) == {
        "analysis": {"scene_threshold"},
        "hardware": {"encoder_preset"},
    }


def test_key_shown_commented_out_still_counts_as_documented() -> None:
    """`thinking_params` is documented as a commented example; its contents are not keys."""
    page = """
```yaml
llm:
  thinking: false                # server has a reasoning switch
  # thinking_params:             # what the switch looks like on your server
  #   chat_template_kwargs:      # (default: the Qwen dialect, vLLM/mlx)
  #     enable_thinking: true
```
"""

    assert documented_keys(page) == {"llm": {"thinking", "thinking_params"}}


def test_field_missing_from_the_page_is_reported() -> None:
    schema = {
        "photos": SectionSchema("PhotoConfig", frozenset({"enabled", "burst_window_seconds"}))
    }

    drift = find_drift(schema, {"photos": {"enabled"}})

    assert drift == ["photos: in the schema but not on the page: burst_window_seconds"]


def test_key_the_schema_would_reject_is_reported() -> None:
    schema = {"photos": SectionSchema("PhotoConfig", frozenset({"enabled"}))}

    drift = find_drift(schema, {"photos": {"enabled", "renamed_last_year"}})

    assert drift == ["photos: on the page but not in the schema: renamed_last_year"]


def test_two_sections_of_one_model_are_documented_once() -> None:
    """`llm` carries the full field list; `title_llm` is the same model, shown short."""
    llm = SectionSchema("LLMConfig", frozenset({"model", "thinking"}))
    schema = {"llm": llm, "title_llm": llm}

    drift = find_drift(schema, {"llm": {"model", "thinking"}, "title_llm": {"model"}})

    assert drift == []


def test_section_with_no_yaml_block_at_all_is_reported() -> None:
    schema = {"trips": SectionSchema("TripsConfig", frozenset({"max_gap_days"}))}

    drift = find_drift(schema, {})

    assert drift == ["trips: section is missing from the page"]


def test_section_the_config_dropped_is_reported() -> None:
    drift = find_drift({}, {"scoring_priority": {"faces"}})

    assert drift == ["scoring_priority: on the page but not a section of Config"]


def test_sections_come_from_config_itself_not_a_hand_kept_list() -> None:
    """Splitting the models across more modules must not shrink what is checked."""
    sections = schema_sections()

    assert sections.keys() == set(Config.model_fields)
    assert sections["photos"].model == "PhotoConfig"
    assert "burst_window_seconds" in sections["photos"].fields
    assert sections["llm"].model == sections["title_llm"].model
    assert sections["preset"].fields == frozenset()


def test_the_published_page_lists_exactly_the_keys_the_schema_accepts() -> None:
    page = (Path(__file__).resolve().parents[1] / PAGE).read_text()

    assert find_drift(schema_sections(), documented_keys(page)) == []
