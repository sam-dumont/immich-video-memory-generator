"""`pictures`: the owner's word on one picture from the terminal."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from immich_memories.operations import picture_holds as holds
from tests.test_picture_holds import bank_a_head, config_at


def run(config, *args, answer=None):
    from immich_memories.cli import main

    with (
        # WHY: init_config_dir would create a real config directory in the user's home.
        patch("immich_memories.cli.init_config_dir"),
        # WHY: get_config would read the developer's own config.yaml off disk.
        patch("immich_memories.cli.get_config", return_value=config),
    ):
        return CliRunner().invoke(main, ["pictures", *args], input=answer)


def test_show_names_the_hold(tmp_path):
    config = config_at(tmp_path)
    bank_a_head(config, "beach")

    result = run(config, "show", "beach")

    assert result.exit_code == 0
    assert "Held: a nudity detector flagged it." in result.output


def test_clearing_a_detector_hold_asks_first_and_a_no_changes_nothing(tmp_path):
    config = config_at(tmp_path)
    bank_a_head(config, "beach")

    result = run(config, "clear-hold", "beach", answer="n\n")

    assert "nudity detector" in result.output
    assert holds.read(config, ["beach"])["beach"].decision is None


def test_clearing_with_a_yes_writes_the_clearance(tmp_path):
    config = config_at(tmp_path)
    bank_a_head(config, "beach")

    result = run(config, "clear-hold", "beach", answer="y\n")

    assert result.exit_code == 0, result.output
    assert holds.read(config, ["beach"])["beach"].describe().startswith("You cleared its hold")


def test_a_picture_nothing_holds_has_nothing_to_clear(tmp_path):
    config = config_at(tmp_path)

    result = run(config, "clear-hold", "calm")

    assert result.exit_code != 0
    assert "Nothing holds" in result.output


def test_never_use_and_undo(tmp_path):
    config = config_at(tmp_path)

    assert run(config, "never-use", "calm").exit_code == 0
    assert "calm" in run(config, "list").output
    assert run(config, "undo", "calm").exit_code == 0

    assert holds.read(config, ["calm"])["calm"].decision is None
