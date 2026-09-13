"""Small wording contracts from the S10 polish: storyboard header, provider give-up, People filter."""

from __future__ import annotations

from click.testing import CliRunner

from immich_memories.analysis.llm_query import TRANSPORT_RETRIES, LLMTransportAttempt
from immich_memories.analysis.provider_status import watch_provider
from immich_memories.cli import main
from immich_memories.config_models_llm import LLMConfig
from immich_memories.operations.cut_progress import announcing_stages
from immich_memories.operations.storyboard import Shot, Storyboard
from immich_memories.people.editor import PersonView
from immich_memories.ui.pages.settings_people import roster_page


def _shot(asset_id: str, seconds: float, motion: bool) -> Shot:
    return Shot(
        asset_id=asset_id,
        taken="2024-06-08T12:15:00",
        day="2024-06-08",
        story_key="s1",
        story_title="Lunch",
        moment="",
        seconds=seconds,
        start=0.0,
        motion=motion,
        new_day=True,
        chapter="",
        reason="",
    )


def test_the_storyboard_header_counts_pictures_and_says_what_the_seconds_are() -> None:
    board = Storyboard(thesis="", shots=(_shot("a", 4.0, True), _shot("b", 4.0, False)))
    assert board.summary_label == "2 pictures, 0:08 of pictures and video"


def test_a_shot_names_its_kind_for_the_badge() -> None:
    assert _shot("a", 4.0, True).kind_label == "Video"
    assert _shot("b", 4.0, False).kind_label == "Still"


def test_the_last_dropped_connection_tells_the_person_what_to_do() -> None:
    seen = []
    observe = watch_provider("reader", LLMConfig(base_url="http://reader.local:9999/v1"))
    with announcing_stages(seen.append):
        observe(
            LLMTransportAttempt(
                attempt=TRANSPORT_RETRIES, outcome="connection_error", status_code=None
            )
        )
    assert seen[-1].label == (
        f"Gave up on the reader at reader.local:9999 after {TRANSPORT_RETRIES} dropped "
        "connections: fix the server and cut again"
    )


def _person(name: str) -> PersonView:
    return PersonView(
        person_id=name.lower(),
        name=name,
        birth_date=None,
        tier="confirmed",
        count=1,
        counts_reliable=True,
        evidence="",
    )


def test_the_roster_filters_by_name_before_paging() -> None:
    people = [_person("Rowan Test"), _person("Sky Test"), _person("River Test")]
    shown, label = roster_page(people, 0, query="r")
    assert [p.name for p in shown] == ["Rowan Test", "River Test"]
    assert label == "Showing 1–2 of 2 matching"


def test_the_quiet_flags_say_what_they_silence_and_point_to_verbose() -> None:
    runner = CliRunner()
    generate_help = runner.invoke(main, ["generate", "--help"]).output
    auto_help = runner.invoke(main, ["auto", "run", "--help"]).output
    assert "-v" in generate_help.split("--quiet", 1)[1].split("\n\n")[0]
    assert "-v" in auto_help.split("--quiet", 1)[1].split("\n\n")[0]


def test_the_saved_path_gets_its_own_line(caplog) -> None:
    import logging
    from pathlib import Path

    from immich_memories.cli._generate_display import _print_generation_result

    with caplog.at_level(logging.INFO, logger="immich_memories.cli"):
        _print_generation_result(
            dry_run=False,
            no_render=False,
            result_path=Path("/tmp/a/very/long/path/june_ec6210e5.mp4"),
            should_upload=False,
            album_name=None,
        )
    messages = [record.getMessage() for record in caplog.records]
    assert "Video saved to:" in messages
    assert (
        messages[messages.index("Video saved to:") + 1].strip()
        == "/tmp/a/very/long/path/june_ec6210e5.mp4"
    )
