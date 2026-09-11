"""Parse a built argv with the real Click tree instead of asserting on the list.

A test that asserts on the list a command builder returned only ever agrees
with the builder. `--target-date` read as a real flag in the scheduler for as
long as the scheduler existed and never existed on `generate`; a list-shaped
assertion would have confirmed it. Handing the argv to Click runs the actual
parser -- unknown options, bad choices and wrong types all raise -- and returns
the parameters the command would have been called with, without running it.
"""

from __future__ import annotations

import warnings
from typing import Any

import click

from immich_memories.cli import main

EXECUTABLE = "immich-memories"


def _tokens_after_group(group_ctx: click.Context) -> list[str]:
    """The tokens the group did not consume, across Click versions.

    Click 8 leaves the subcommand name in the deprecated `protected_args` and
    the rest in `args`; Click 9 puts everything in `args`. Reading both and
    concatenating is correct under either.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        protected = list(getattr(group_ctx, "protected_args", []))
    return [*protected, *group_ctx.args]


def parse_argv(argv: list[str]) -> tuple[str, dict[str, Any]]:
    """Return the subcommand name and the parameters it would be called with.

    Raises click.UsageError (NoSuchOption, BadParameter, ...) when the argv
    would not have reached the command at all.
    """
    executable, *args = argv
    if executable != EXECUTABLE:
        raise AssertionError(f"argv must invoke {EXECUTABLE!r}, not {executable!r}")

    group_ctx = main.make_context(EXECUTABLE, args)
    name, command, rest = main.resolve_command(group_ctx, _tokens_after_group(group_ctx))
    if name is None or command is None:
        raise AssertionError(f"argv names no subcommand: {argv!r}")

    sub_ctx = command.make_context(name, rest, parent=group_ctx)
    return name, sub_ctx.params


def parse_generate_argv(argv: list[str]) -> dict[str, Any]:
    """Parse argv that is expected to invoke `generate`, returning its params."""
    name, params = parse_argv(argv)
    if name != "generate":
        raise AssertionError(f"argv invokes {name!r}, not 'generate'")
    return params
