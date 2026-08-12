#!/usr/bin/env python3
"""Generate CLI reference documentation from Click commands.

Walks the Click command tree and produces a Markdown file
compatible with the Docusaurus documentation site.

Usage:
    python scripts/generate_cli_docs.py
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import click

DEFAULT_OUTPUT_PATH = Path("docs-site/docs/reference/cli-reference.md")


def _option_type(param: click.Option) -> str:
    """Render exact accepted values when Click exposes a closed choice."""
    if isinstance(param.type, click.Choice):
        return ", ".join(f"`{choice}`" for choice in param.type.choices)
    return param.type.name if hasattr(param.type, "name") else str(param.type)


def _get_options_table(cmd: click.Command, *, include_help: bool = False) -> str:
    """Generate a Markdown table of command options."""
    params = [p for p in cmd.params if isinstance(p, click.Option) and not p.hidden]
    if not params:
        return ""

    lines = ["| Flag | Type | Default | Description |", "| --- | --- | --- | --- |"]
    for param in params:
        names = ", ".join(f"`{option}`" for option in (*param.opts, *param.secondary_opts))
        param_type = _option_type(param)
        default = param.default if param.default is not None else "-"
        if isinstance(default, bool):
            default = str(default).lower()
        help_text = (param.help or "").replace("|", "\\|")
        lines.append(f"| {names} | {param_type} | {default} | {help_text} |")

    if include_help:
        lines.append("| `--help` | flag | - | Show this message and exit |")

    return "\n".join(lines)


def _get_arguments_list(cmd: click.Command) -> str:
    """Generate a list of command arguments."""
    args = [p for p in cmd.params if isinstance(p, click.Argument)]
    if not args:
        return ""

    lines = ["**Arguments:**"]
    for arg in args:
        lines.append(f"- `{arg.name}` ({arg.type.name})")
    return "\n".join(lines)


def _document_command(cmd: click.Command, name: str, depth: int = 2) -> str:
    """Generate Markdown documentation for a single command."""
    heading = "#" * depth
    lines = [f"{heading} `{name}`", ""]

    if cmd.help:
        lines.append(textwrap.dedent(cmd.help).replace("\b", "").strip())
        lines.append("")

    trailing_usage = " COMMAND [ARGS]..." if isinstance(cmd, click.Group) else ""
    lines.append(f"```bash\nimmich-memories {name} [OPTIONS]{trailing_usage}\n```\n")

    opts = _get_options_table(cmd)
    if opts:
        lines.append(opts)
        lines.append("")

    args = _get_arguments_list(cmd)
    if args:
        lines.append(args)
        lines.append("")

    return "\n".join(lines)


def generate_reference(group: click.Group) -> str:
    """Generate the full CLI reference Markdown."""
    lines = [
        "---",
        "title: CLI Reference (Auto-Generated)",
        "sidebar_label: Reference",
        "---",
        "",
        "# CLI Reference",
        "",
        "This page is auto-generated from the Click command definitions.",
        "Run `make docs-cli` to regenerate.",
        "",
        "## Global options",
        "",
        _get_options_table(group, include_help=True),
        "",
    ]

    # Document top-level commands
    for name, cmd in sorted(group.commands.items()):
        if isinstance(cmd, click.Group):
            lines.append(_document_command(cmd, name, depth=2))
            # Document subcommands
            for sub_name, sub_cmd in sorted(cmd.commands.items()):
                lines.append(_document_command(sub_cmd, f"{name} {sub_name}", depth=3))
        else:
            lines.append(_document_command(cmd, name, depth=2))

    content = "\n".join(lines)
    return "\n".join(line.rstrip() for line in content.splitlines()) + "\n"


def main() -> None:
    """Generate CLI docs and write to docs-site."""
    # Import the CLI group
    from immich_memories.cli import main as cli_main

    output_path = DEFAULT_OUTPUT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)

    content = generate_reference(cli_main)
    output_path.write_text(content)
    print(f"Generated CLI reference: {output_path}")  # noqa: T201


if __name__ == "__main__":
    main()
