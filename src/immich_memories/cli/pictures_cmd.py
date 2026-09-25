"""`pictures`: clear one picture's hold, or never use it, from the terminal."""

from __future__ import annotations

from typing import Any

import click

from immich_memories.cli._helpers import console, print_error, print_success

_CLEAR_WARNING = (
    "Once cleared, every film may use it, and nothing the app reads later puts the hold back. "
    "`pictures undo` does."
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
    @click.option("--yes", is_flag=True, help="Clear it without asking")
    @click.pass_context
    def clear_hold(ctx: click.Context, asset_id: str, yes: bool) -> None:
        """Clear this one picture's hold, after you've looked at it yourself."""
        from immich_memories.operations import picture_holds as holds

        config = ctx.obj["config"]
        hold = holds.read(config, [asset_id])[asset_id]
        if not hold.can_clear:
            print_error(_nothing_to_clear(hold))
            raise SystemExit(1)
        console.print(hold.describe())
        console.print(_CLEAR_WARNING)
        if not yes and not click.confirm(f"Clear the hold on {asset_id}?", default=False):
            console.print("Left as it was.")
            return
        holds.clear_hold(config, asset_id, via="cli")
        print_success(f"Cleared: {asset_id} can play in the next cut.")

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
        from immich_memories.store.owner_decisions import CLEAR_HOLD, decisions

        decided = decisions(holds.store_of(ctx.obj["config"]))
        if not decided:
            console.print("You haven't cleared or ruled out any picture.")
            return
        for asset_id, decision in decided.items():
            console.print(
                f"{asset_id}  {'hold cleared' if decision == CLEAR_HOLD else 'never use'}"
            )

    main.add_command(pictures)


def _nothing_to_clear(hold: Any) -> str:
    from immich_memories.store.owner_decisions import CLEAR_HOLD, NEVER_USE

    if hold.decision == CLEAR_HOLD:
        return f"You already cleared {hold.asset_id}."
    if hold.decision == NEVER_USE:
        return f"You ruled {hold.asset_id} out. `pictures undo` it first."
    return f"Nothing holds {hold.asset_id}, so there's nothing to clear."
