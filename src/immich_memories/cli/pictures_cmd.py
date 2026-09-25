"""`pictures`: clear one picture's hold, or never use it, from the terminal."""

from __future__ import annotations

from typing import Any

import click

from immich_memories.cli._helpers import console, print_error, print_success

_CLEAR_WARNING = (
    "Once cleared, every film up to that level may use it, and nothing the app reads later "
    "puts the hold back. `pictures undo` does."
)


def register_pictures_commands(main: click.Group) -> None:
    """Register the `pictures` group on the main CLI group."""

    @click.group("pictures")
    def pictures() -> None:
        """Your own word on a picture: clear its hold, or never use it.

        Every tier reads it, in every later cut. The asset id is the one `runs why`,
        `runs story` and Immich show.
        """

    @pictures.command("show")
    @click.argument("asset_id")
    @click.pass_context
    def show(ctx: click.Context, asset_id: str) -> None:
        """What holds this picture, and what you decided."""
        from immich_memories.operations import picture_holds as holds

        hold = holds.read(ctx.obj["config"], [asset_id])[asset_id]
        console.print(
            hold.describe() or f"Nothing holds {asset_id}, and you haven't decided on it."
        )

    @pictures.command("clear-hold")
    @click.argument("asset_id")
    @click.option(
        "--level",
        type=click.Choice(["anyone", "family", "just-us"]),
        default=None,
        help="The widest film it may play in; asked when not given (--yes: family)",
    )
    @click.option("--yes", is_flag=True, help="Clear it without asking")
    @click.pass_context
    def clear_hold(ctx: click.Context, asset_id: str, level: str | None, yes: bool) -> None:
        """Clear this one picture's hold for a level, after you've looked at it yourself."""
        from immich_memories.operations import picture_holds as holds

        config = ctx.obj["config"]
        hold = holds.read(config, [asset_id])[asset_id]
        if not hold.can_clear:
            print_error(_nothing_to_clear(hold))
            raise SystemExit(1)
        console.print(hold.describe())
        level = level or _ask_level(yes)
        console.print(_CLEAR_WARNING)
        if not yes and not click.confirm(f"Clear the hold on {asset_id}?", default=False):
            console.print("Left as it was.")
            return
        holds.clear_hold(config, asset_id, via="cli", level=level)
        print_success(f"Cleared {_LEVEL_WORDS[level]}: {asset_id} can play in the next cut.")

    @pictures.command("never-use")
    @click.argument("asset_id")
    @click.pass_context
    def never_use(ctx: click.Context, asset_id: str) -> None:
        """Keep this picture out of every film from now on."""
        from immich_memories.operations import picture_holds as holds

        holds.never_use(ctx.obj["config"], asset_id, via="cli")
        print_success(f"{asset_id} won't be in any film from the next cut on.")

    @pictures.command("undo")
    @click.argument("asset_id")
    @click.pass_context
    def undo(ctx: click.Context, asset_id: str) -> None:
        """Forget what you decided about this picture: the app's own holds apply again."""
        from immich_memories.operations import picture_holds as holds

        holds.forget(ctx.obj["config"], asset_id)
        print_success(f"Forgot your decision on {asset_id}.")

    @pictures.command("list")
    @click.pass_context
    def list_decisions(ctx: click.Context) -> None:
        """Every picture you cleared or will never use."""
        from immich_memories.operations import picture_holds as holds
        from immich_memories.store.owner_decisions import CLEARANCE_LEVELS, decisions

        cleared = {decision: _LEVEL_WORDS[level] for level, decision in CLEARANCE_LEVELS.items()}

        decided = decisions(holds.store_of(ctx.obj["config"]))
        if not decided:
            console.print("You haven't cleared or ruled out any picture.")
            return
        for asset_id, decision in decided.items():
            words = f"hold cleared {cleared[decision]}" if decision in cleared else "never use"
            console.print(f"{asset_id}  {words}")

    main.add_command(pictures)


_LEVEL_WORDS = {"anyone": "for anyone", "family": "for the family", "just-us": "for just us"}


def _ask_level(yes: bool) -> str:
    if yes:
        return "family"
    return click.prompt(
        "Fine for which films",
        type=click.Choice(["anyone", "family", "just-us"]),
        default="family",
    )


def _nothing_to_clear(hold: Any) -> str:
    from immich_memories.store.owner_decisions import NEVER_USE, is_clearance

    if is_clearance(hold.decision):
        return f"You already cleared {hold.asset_id}."
    if hold.decision == NEVER_USE:
        return f"You ruled {hold.asset_id} out. `pictures undo` it first."
    return f"Nothing holds {hold.asset_id}, so there's nothing to clear."
