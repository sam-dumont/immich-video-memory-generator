"""Reading the library's people graph, and writing it down."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import click

from immich_memories.cli._helpers import console, print_success
from immich_memories.people.companion import (
    default_people_path,
    load_document,
    people_entries,
    save_graph,
)
from immich_memories.people.graph import DEFAULT_MIN_ASSETS, PeopleGraph
from immich_memories.people.signatures import LinkKind

_TIER_ORDER = ("inner", "recurring", "episodic", "event")

_HOW_THE_OWNER_WAS_FOUND = {
    "told": "you said so",
    "account": "matched the Immich account name",
    "inferred": "inferred — longest span, most pictures",
}


def register_people_commands(cli_group: click.Group) -> None:
    """Register the people commands on the main CLI group."""

    @click.group("people", invoke_without_command=True)
    @click.pass_context
    def people(ctx: click.Context) -> None:
        """Who is in this library, and who they are to each other.

        Called on its own this still lists the people Immich knows, which is
        what `immich-memories people` has always done.
        """
        if ctx.invoked_subcommand is None:
            _list_immich_people(ctx.obj["config"])

    _register_scan(people)
    _register_show(people)
    cli_group.add_command(people)


def _list_immich_people(config: Any) -> None:
    """Every named person Immich holds, which is what `--person` matches on."""
    import sys

    from rich.table import Table

    from immich_memories.api.sync_client import SyncImmichClient
    from immich_memories.cli._helpers import print_error

    if not config.immich.url or not config.immich.api_key:
        print_error("Immich not configured. Run 'immich-memories config' first.")
        sys.exit(1)

    with SyncImmichClient(
        base_url=config.immich.url,
        api_key=config.immich.api_key,
        api_version=config.immich.api_version,
    ) as client:
        found = client.get_all_people()

    table = Table(title="People in Immich")
    table.add_column("Name", style="cyan")
    table.add_column("ID", style="dim")
    for person in sorted(found, key=lambda p: p.name):
        if person.name:
            table.add_row(person.name, person.id[:8] + "...")

    console.print(table)
    console.print(f"\nTotal: {len([p for p in found if p.name])} named people")


def _register_scan(people: click.Group) -> None:
    @people.command("scan")
    @click.option(
        "--min-assets",
        type=int,
        default=DEFAULT_MIN_ASSETS,
        help="Pictures a named person needs before the graph has an opinion",
    )
    @click.option(
        "--owner",
        envvar="IMMICH_MEMORIES_OWNER",
        default=None,
        help="The name of the person whose library this is, if the account does not say",
    )
    @click.option(
        "--out",
        type=click.Path(dir_okay=False, path_type=Path),
        default=None,
        help="Where to write the people file",
    )
    def scan(min_assets: int, owner: str | None, out: Path | None) -> None:
        """Build or refresh the people file from Immich.

        Reads every named person's count and month curve, then asks about each
        remaining pair to find who appears with whom. Nothing here looks at a
        pixel and nothing here asks you a question — the library's own
        distribution is the whole input.

        Safe to re-run: everything under `confirmed:` in the file is copied
        through untouched, and preferred to this pass's reading forever after.
        """
        from immich_memories.api.sync_client import SyncImmichClient
        from immich_memories.config import get_config
        from immich_memories.people.graph import build_graph

        path = out or default_people_path()
        config = get_config()
        with SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key) as client:
            graph = build_graph(client, min_assets=min_assets, owner_name=owner)

        save_graph(path, graph)
        _report(graph)
        print_success(f"{len(graph.people)} people in {path}")


def _register_show(people: click.Group) -> None:
    @people.command("show")
    @click.option(
        "--file",
        "people_file",
        type=click.Path(dir_okay=False, path_type=Path),
        default=None,
        help="The people file to read",
    )
    @click.option(
        "--tier",
        type=click.Choice(_TIER_ORDER),
        default=None,
        help="Show only one tier",
    )
    def show(people_file: Path | None, tier: str | None) -> None:
        """Print what the last scan wrote down."""
        path = people_file or default_people_path()
        document = load_document(path)
        entries = people_entries(document)
        if not entries:
            console.print(
                f"[yellow]Nothing in {path} yet — run 'immich-memories people scan'.[/yellow]"
            )
            return

        _print_owner(document.get("owner"))
        for entry in _sorted(entries):
            if tier and _tier_of(entry) != tier:
                continue
            console.print(_person_line(entry))
        console.print(f"[dim]{path}[/dim]")


def _report(graph: PeopleGraph) -> None:
    """A summary of what the scan read, deliberately without the roster.

    A real library's inner circle is the user's household by name. Printing
    every one of them to a terminal that may be a log, a screenshot or a
    shared session is not something a scan should do on its own; the file is
    right there and `people show` asks for it on purpose.
    """
    if graph.owner is not None:
        _print_owner(
            {"name": graph.owner.name, "identified": graph.owner.identified},
        )

    counted = _tier_counts(node.tier.value for node in graph.people)
    for name in _TIER_ORDER:
        console.print(f"  [bold]{name:<10}[/bold] {counted.get(name, 0):>4}")

    flags = _flag_counts(graph)
    if flags:
        console.print(f"  [yellow]{flags}[/yellow]")


def _flag_counts(graph: PeopleGraph) -> str:
    twins = _people_with(graph, LinkKind.TWIN)
    duplicates = _people_with(graph, LinkKind.DUPLICATE)
    said = []
    if twins:
        said.append(f"{twins // 2} twin pair(s) — their counts are not to be trusted")
    if duplicates:
        said.append(f"{duplicates // 2} name(s) on two person records — merge them in Immich")
    return "; ".join(said)


def _people_with(graph: PeopleGraph, kind: LinkKind) -> int:
    return sum(1 for node in graph.people for link in node.links if link.kind is kind)


def _tier_counts(tiers: Iterable[str]) -> dict[str, int]:
    counted: dict[str, int] = {}
    for tier in tiers:
        counted[tier] = counted.get(tier, 0) + 1
    return counted


def _print_owner(owner: dict[str, Any] | None) -> None:
    if not owner:
        console.print("[yellow]No owner identified — pass --owner to name them.[/yellow]")
        return
    how = _HOW_THE_OWNER_WAS_FOUND.get(str(owner.get("identified")), "unknown")
    console.print(f"  owner: [bold]{owner.get('name')}[/bold] [dim]({how})[/dim]")


def _sorted(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        entries,
        key=lambda entry: (
            _TIER_ORDER.index(_tier_of(entry))
            if _tier_of(entry) in _TIER_ORDER
            else len(_TIER_ORDER),
            -_evidence_of(entry).get("count", 0),
        ),
    )


def _person_line(entry: dict[str, Any]) -> str:
    evidence = _evidence_of(entry)
    confirmed = entry.get("confirmed") or {}
    role = confirmed.get("role")
    line = (
        f"  [bold]{entry.get('name', '?'):<24}[/bold] {_tier_of(entry):<10}"
        f" {evidence.get('count', 0):>6} pics"
        f"  {evidence.get('active_months', 0):>4} months"
        f"  [dim]since {evidence.get('onset') or evidence.get('first_month') or '?'}[/dim]"
    )
    return f"{line}  [green]{role}[/green]" if role else line


def _tier_of(entry: dict[str, Any]) -> str:
    inferred = entry.get("inferred")
    return str(inferred.get("tier", "")) if isinstance(inferred, dict) else ""


def _evidence_of(entry: dict[str, Any]) -> dict[str, Any]:
    inferred = entry.get("inferred")
    evidence = inferred.get("evidence") if isinstance(inferred, dict) else None
    return evidence if isinstance(evidence, dict) else {}
