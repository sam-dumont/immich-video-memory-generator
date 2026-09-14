"""The CLI reference includes the invocation options Click exposes."""

import sys
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from generate_cli_docs import _get_options_table, generate_reference  # noqa: E402

from immich_memories.cli import main  # noqa: E402


def test_option_table_includes_both_sides_of_boolean_flags():
    command = main.commands["generate"]
    table = _get_options_table(command)
    flags = [
        param
        for param in command.params
        if isinstance(param, click.Option) and param.secondary_opts
    ]
    assert flags
    for param in flags:
        row = next(line for line in table.splitlines() if f"`{param.opts[0]}`" in line)
        assert all(f"`{option}`" in row for option in (*param.opts, *param.secondary_opts))


def test_root_options_are_documented_before_subcommands():
    reference = generate_reference(main)
    first_command = min(reference.index(f"## `{name}`") for name in main.commands)
    root_section = reference[:first_command]
    for param in main.params:
        if isinstance(param, click.Option) and not param.hidden:
            assert all(f"`{option}`" in root_section for option in param.opts)
    assert "immich-memories [GLOBAL OPTIONS] COMMAND [OPTIONS]" in root_section
