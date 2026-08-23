"""-o means output, the way it does in every other command line tool.

It meant --orientation on `generate` and `titles`, and --output on
`analyze export`, in the same CLI. Reaching for the obvious one wrote nothing
and failed with a message about landscape and portrait.
"""

from unittest.mock import patch

from click.testing import CliRunner

from immich_memories.cli import main


def _help(*command: str) -> str:
    # WHY: config-dir creation is a filesystem boundary; --help must not touch it.
    with patch("immich_memories.cli.init_config_dir"):
        return CliRunner().invoke(main, [*command, "--help"], catch_exceptions=False).output


def test_dash_o_is_the_output_file_on_generate() -> None:
    assert "-o, -O, --output" in _help("generate")


def test_dash_o_is_the_output_file_on_titles() -> None:
    assert "-o, -O, --output" in _help("titles", "test")


def test_orientation_keeps_its_long_form() -> None:
    assert "--orientation" in _help("generate")


def test_orientation_no_longer_answers_to_dash_o() -> None:
    """Reassigned, not shared: a short flag that means two things is the bug."""
    orientation_line = next(
        line for line in _help("generate").splitlines() if "--orientation" in line
    )

    assert orientation_line.strip().startswith("--orientation"), orientation_line


def test_an_orientation_word_as_an_output_path_says_so() -> None:
    """`-o landscape` used to be valid. Now it must not quietly write a file
    called "landscape" — the whole point is that reaching for -o stops being
    a way to get something you did not ask for."""
    # WHY: config-dir creation is a filesystem boundary; flag parsing fails before it.
    with patch("immich_memories.cli.init_config_dir"):
        result = CliRunner().invoke(main, ["generate", "-o", "landscape"], catch_exceptions=False)

    assert result.exit_code != 0
    assert "--orientation" in result.output
