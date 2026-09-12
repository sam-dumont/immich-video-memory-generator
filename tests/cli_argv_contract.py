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
from immich_memories.cli.generate_resolution import (
    _resolve_generation_scope,
    _validate_album_scope,
)
from immich_memories.timeperiod import DateRange

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


def memory_scope(params: dict[str, Any]) -> tuple[DateRange, list[DateRange]]:
    """What the parsed params resolve to: the window, and the ranges to search.

    Parsing proves the options exist; this proves they add up to a memory.
    `generate` rejects a season with no season and an album with no album name
    after parsing, deeper in the command, so the argv a scheduler builds can
    parse cleanly and still name nothing that could be rendered. These are the
    command's own resolvers, called the way the command calls them.
    """
    _validate_album_scope(
        from_album=params["from_album"],
        year=params["year"],
        start=params["start"],
        end=params["end"],
        period=params["period"],
        birthday=params["birthday"],
        season=params["season"],
        month=params["month"],
        memory_type=params["memory_type"],
        person_names=list(params["person"]),
    )
    return _resolve_generation_scope(
        from_album=params["from_album"],
        year=params["year"],
        start=params["start"],
        end=params["end"],
        period=params["period"],
        birthday=params["birthday"],
        memory_type=params["memory_type"],
        season=params["season"],
        month=params["month"],
        hemisphere=params["hemisphere"],
        years_back=params["years_back"],
        on_this_day_target=params["day"] if params["memory_type"] == "on_this_day" else None,
        holiday=params["holiday"],
    )
