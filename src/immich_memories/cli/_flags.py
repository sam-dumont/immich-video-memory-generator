"""Shared behaviour for command-line flags that more than one command takes."""

from __future__ import annotations

import click

_ORIENTATIONS = ("landscape", "portrait", "square")


def output_path(ctx: click.Context, param: click.Parameter, value: str | None) -> str | None:
    """Validate --output, catching the one mistake this flag used to cause.

    -o meant --orientation here until it meant --output, which is what it means
    in every other tool and in `analyze export`. Reassigning it silently turns
    an old `-o landscape` into a file named "landscape" — a wrong result that
    looks like a right one. Three words are never a filename, so say so.
    """
    del ctx, param
    if value in _ORIENTATIONS:
        raise click.UsageError(
            f"'{value}' is an orientation, not a file path. "
            f"-o is now the output file; use --orientation {value} instead."
        )
    return value
