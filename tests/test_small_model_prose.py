"""What a small model writing the prose needs from the questions and the title check."""

from __future__ import annotations

from immich_memories.titles.llm_titles import TitleSuggestion, restore_fact_casing


def test_a_place_the_facts_capitalise_keeps_its_capital_in_the_title():
    title = TitleSuggestion(title="Mai à split", subtitle="Six jours à split")

    fixed = restore_fact_casing(title, "Places by day:\n  05-02: Split, Croatia\n")

    assert (fixed.title, fixed.subtitle) == ("Mai à Split", "Six jours à Split")


def test_a_short_word_or_a_word_the_facts_never_capitalise_is_left_alone():
    title = TitleSuggestion(title="Janvier à annecy", subtitle=None)

    fixed = restore_fact_casing(title, "À la maison. Places by day:\n  01-02: Lyon\n")

    assert fixed.title == "Janvier à annecy"


def test_an_episode_is_asked_for_a_record_on_its_own_lines_whatever_else_is_in_the_request():
    from immich_memories.analysis.text_episode_prompt import _LEAN_PROMPT, _PROMPT

    for prompt in (_PROMPT, _LEAN_PROMPT):
        assert "never against the other episodes" in prompt
        assert "most episodes have none" not in prompt


def test_an_account_names_the_period_s_distinctive_events_before_its_pattern():
    from immich_memories.analysis.library_catalogue import _OVERVIEW_INSTRUCTIONS

    assert "distinctive events first" in _OVERVIEW_INSTRUCTIONS
