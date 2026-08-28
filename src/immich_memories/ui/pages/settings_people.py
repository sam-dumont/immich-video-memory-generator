"""The companion editor — confirming who's who without opening a text editor.

The page is a thin rendering of :mod:`immich_memories.people.editor`: it draws
what that module read out of the people file and hands every answer straight
back to it. Nothing about the schema, the ordering or the write contract lives
here, which is why the confirm flow can be tested on a real file with no
browser in the room.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from nicegui import ui

from immich_memories.config import get_config
from immich_memories.people.companion import (
    default_people_path,
    load_document,
    retained_immich_ids,
)
from immich_memories.people.editor import (
    CONFIRMED,
    REJECTED,
    ROLE_SUGGESTIONS,
    CurationFlag,
    LinkView,
    PersonView,
    add_person,
    add_relationship,
    curation_flags,
    load_people,
    remove_relationship,
    save_person,
)
from immich_memories.people.relationships import RELATIONSHIP_CHOICES, relationship_label
from immich_memories.ui.components import im_badge, im_button, im_info_card, im_section_header
from immich_memories.ui.nicegui_compat import io_bound_result

logger = logging.getLogger(__name__)

_TIER_VARIANT = {"inner": "success", "recurring": "info", "episodic": "info", "event": "warning"}

_TIER_MEANING = {
    "inner": "here across many months and many years",
    "recurring": "back regularly, without living in the library",
    "episodic": "a handful of months",
    "event": "a burst — lots of pictures over very few months",
}


def render_people_page() -> None:
    """The people file as a page: the roster, the flags, and the confirm controls."""
    path = default_people_path()
    thumbnails: dict[str, str] = {}

    im_info_card(
        "Who the library thinks is in it, read from counts and month curves alone. "
        "Everything the scan inferred is recomputed each time you rescan; everything "
        "you confirm here is yours and is never overwritten.",
        variant="info",
    )

    im_section_header("Curation", icon="report_problem")
    flags_column = ui.column().classes("w-full gap-2")

    im_section_header("The roster", icon="groups")
    roster_column = ui.column().classes("w-full gap-3")

    async def load() -> None:
        """Draw the roster from the file, then let the faces catch up.

        One face crop is one call to Immich, so a household of seventy is
        seventy round trips. Waiting for them before drawing anything would
        hold the page blank for the whole of it, and redrawing afterwards
        would throw away whatever the user had started typing.
        """
        people = load_people(path)
        _draw_flags(flags_column, curation_flags(people))
        avatars = _draw_roster(roster_column, people, thumbnails, path)
        if not people:
            return
        thumbnails.update(await io_bound_result(_fetch_thumbnails, [p.person_id for p in people]))
        for person_id, holder in avatars.items():
            _fill_avatar(holder, thumbnails.get(person_id))

    async def rescan() -> None:
        ui.notify("Reading the library…", type="ongoing")
        try:
            found = await io_bound_result(_scan, path)
        except Exception as exc:  # noqa: BLE001 - an unreachable Immich is a message, not a stack
            logger.warning("The people scan failed: %s", exc)
            ui.notify(f"The scan could not finish: {exc}", type="negative")
            return
        ui.notify(f"{found} people in {path}", type="positive")
        await load()

    with ui.row().classes("w-full items-center gap-3 mb-2"):
        im_button("Rescan the library", variant="secondary", on_click=rescan, icon="refresh")
        add_person_dialog = _add_person_dialog(path)
        im_button(
            "Add someone not in Immich",
            variant="ghost",
            on_click=add_person_dialog.open,
            icon="person_add",
        )
        ui.label(str(path)).classes("text-sm self-center").style("color: var(--im-text-secondary)")

    ui.timer(0.1, load, once=True)


def _scan(path: Path) -> int:
    """The same code path as `immich-memories people scan`, in a worker thread.

    The client is built here rather than handed in: `run.io_bound` is a thread
    executor, so anything context-local has to be entered on this side of it.
    """
    from immich_memories.api.sync_client import SyncImmichClient
    from immich_memories.people.companion import save_graph
    from immich_memories.people.evidence_graph import (
        default_evidence_graph_path,
        save_evidence_graph,
    )
    from immich_memories.people.graph import build_graph

    retained = retained_immich_ids(load_document(path))
    config = get_config()
    with SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key) as client:
        graph = build_graph(client, include_person_ids=retained)
    save_graph(path, graph)
    save_evidence_graph(default_evidence_graph_path(path), graph, load_document(path))
    return len(graph.people)


def _fetch_thumbnails(person_ids: list[str]) -> dict[str, str]:
    """Immich's face crops, base64'd server-side.

    The browser never gets the API key: the same reason the clip grid inlines
    its thumbnails instead of pointing at Immich directly.
    """
    config = get_config()
    if not config.immich.url or not config.immich.api_key:
        return {}

    from immich_memories.api.sync_client import SyncImmichClient

    found: dict[str, str] = {}
    with SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key) as client:
        for person_id in person_ids:
            if person_id.startswith("manual:"):
                continue
            try:
                found[person_id] = base64.b64encode(client.get_person_thumbnail(person_id)).decode()
            except Exception as exc:  # noqa: BLE001, PERF203 - a missing face is not the page
                logger.debug("No thumbnail for one person: %s", type(exc).__name__)
    return found


def _person_url(person_id: str) -> str | None:
    if person_id.startswith("manual:"):
        return None
    base = get_config().immich.url.rstrip("/")
    return f"{base}/people/{person_id}" if base else None


def _draw_flags(container: ui.column, flags: list[CurationFlag]) -> None:
    container.clear()
    with container:
        if not flags:
            ui.label("Nothing to curate — no twins and no split records.").classes("text-sm").style(
                "color: var(--im-text-secondary)"
            )
            return
        for flag in flags:
            _flag_card(flag)


def _flag_card(flag: CurationFlag) -> None:
    with ui.element("div").classes("w-full rounded-lg p-3 im-alert-warning"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("warning").style("color: var(--im-warning)")
            ui.label(" and ".join(flag.names)).classes("font-medium").style(
                "color: var(--im-warning)"
            )
            im_badge(flag.kind, variant="warning")
        ui.label(flag.message).classes("text-sm mt-1").style("color: var(--im-text-secondary)")
        with ui.row().classes("gap-3 mt-1"):
            for name, person_id in zip(flag.names, flag.person_ids, strict=True):
                url = _person_url(person_id)
                if url:
                    ui.link(f"Open {name} in Immich", url, new_tab=True).classes("text-sm")


def _draw_roster(
    container: ui.column, people: list[PersonView], thumbnails: dict[str, str], path: Path
) -> dict[str, ui.element]:
    """Draw every card, and hand back the avatar slot each one is waiting on."""
    container.clear()
    with container:
        if not people:
            im_info_card(
                "No people file yet. Rescan the library to build one — it reads every "
                "named person's count and month curve, and looks at no pixels.",
                variant="warning",
            )
            return {}
        return {
            person.person_id: _person_card(person, people, thumbnails.get(person.person_id), path)
            for person in people
        }


def _person_card(
    person: PersonView, people: list[PersonView], thumbnail: str | None, path: Path
) -> ui.element:
    with (
        ui.card()
        .classes("w-full p-4")
        .style("background: var(--im-bg-elevated); border: 1px solid var(--im-border-light)")
    ):
        with ui.row().classes("w-full items-start gap-4 no-wrap"):
            avatar = _avatar(thumbnail)
            with ui.column().classes("flex-grow gap-1"):
                _headline(person)
                _facts(person)
                _confirm_controls(person, path)
        _links_section(person, people, path)
    return avatar


def _avatar(thumbnail: str | None) -> ui.element:
    holder = (
        ui.element("div")
        .classes("rounded-full flex items-center justify-center overflow-hidden")
        .style("width: 64px; height: 64px; flex: 0 0 64px; background: var(--im-bg)")
    )
    _fill_avatar(holder, thumbnail)
    return holder


def _fill_avatar(holder: ui.element, thumbnail: str | None) -> None:
    holder.clear()
    with holder:
        if thumbnail:
            ui.image(f"data:image/jpeg;base64,{thumbnail}").classes("w-full h-full object-cover")
        else:
            ui.icon("person").style("color: var(--im-text-muted)")


def _headline(person: PersonView) -> None:
    with ui.row().classes("items-center gap-2"):
        ui.label(person.name).classes("text-lg font-semibold").style("color: var(--im-text)")
        if person.tier:
            im_badge(person.tier, variant=_TIER_VARIANT.get(person.tier, "info"))
        if not person.counts_reliable:
            im_badge("counts unreliable", variant="warning", icon="warning")


def _facts(person: PersonView) -> None:
    meaning = _TIER_MEANING.get(person.tier)
    line = f"{person.evidence} — {meaning}" if meaning else person.evidence
    ui.label(line).classes("text-sm").style("color: var(--im-text-secondary)")

    with ui.row().classes("items-center gap-2"):
        # Read-only on purpose: the birth date is Immich's, and a second place
        # to edit it is a second place for it to be wrong.
        born = f"Born {person.birth_date}" if person.birth_date else "No birth date in Immich"
        ui.label(born).classes("text-sm").style("color: var(--im-text-secondary)")
        url = _person_url(person.person_id)
        if url:
            ui.link("edit in Immich", url, new_tab=True).classes("text-sm")


def _role_options(role: str | None) -> list[str]:
    """The suggestions, plus whatever this person was already called.

    A select whose value is not among its options renders blank, and a role
    somebody typed by hand is never in the suggestion list — so it would read
    as "no role" for a question they had already answered.
    """
    options = list(ROLE_SUGGESTIONS)
    if role and role not in options:
        options.insert(0, role)
    return options


def _confirm_controls(person: PersonView, path: Path) -> None:
    with ui.row().classes("w-full items-center gap-3 mt-1 no-wrap"):

        def on_role(event) -> None:
            person.role = event.value
            save_person(path, person)
            ui.notify(f"{person.name}: {person.role or 'no role'}", type="positive")

        ui.select(
            options=_role_options(person.role),
            value=person.role,
            label="Role",
            with_input=True,
            new_value_mode="add-unique",
            clearable=True,
            on_change=on_role,
        ).props("dense outlined").classes("w-48")

        def on_notes(event) -> None:
            person.notes = event.value
            save_person(path, person)

        ui.input(
            label="Notes",
            value=person.notes or "",
            on_change=on_notes,
        ).props("dense outlined debounce=800").classes("flex-grow")


def _links_section(person: PersonView, people: list[PersonView], path: Path) -> None:
    ui.separator().classes("my-2")
    with ui.row().classes("w-full items-center gap-2"):
        ui.label("Relationships").classes("text-sm font-medium").style(
            "color: var(--im-text-secondary)"
        )
        ui.element("div").classes("flex-grow")
        dialog = _relationship_dialog(person, people, path)
        ui.button("Add relationship", icon="add", on_click=dialog.open).props(
            "flat dense no-caps size=sm"
        ).style("color: var(--im-primary)")
    if not person.links:
        ui.label("Nothing recorded yet.").classes("text-xs").style("color: var(--im-text-muted)")
        return
    for link in person.links:
        _link_row(person, link, path)


def _why(link: LinkView) -> str:
    """What suggested this edge — or that nothing did, because you wrote it."""
    if not link.inferred:
        return "you said so"
    return f"{link.prompt} ({link.confidence:.0%})" if link.confidence else link.prompt


def _link_row(person: PersonView, link: LinkView, path: Path) -> None:
    with ui.row().classes("w-full items-center gap-2 no-wrap"):
        ui.icon("link").classes("text-sm").style("color: var(--im-text-muted)")
        ui.label(f"{relationship_label(link.kind)} {link.target_name}").classes("text-sm").style(
            "color: var(--im-text)"
        )
        ui.label(_why(link)).classes("text-xs").style("color: var(--im-text-muted)")
        ui.element("div").classes("flex-grow")
        if not link.inferred:
            im_badge("confirmed", variant="success")

            def remove() -> None:
                remove_relationship(path, person.person_id, link.kind, link.target_id)
                ui.notify("Relationship removed", type="positive")
                ui.navigate.reload()

            ui.button(icon="delete_outline", on_click=remove).props(
                "flat dense round size=sm"
            ).style("color: var(--im-text-muted)").tooltip("Remove this relationship")
            return
        answered = ui.row().classes("items-center")

        def draw_answer() -> None:
            answered.clear()
            with answered:
                if link.decision:
                    im_badge(
                        link.decision,
                        variant="success" if link.decision == CONFIRMED else "error",
                    )

        draw_answer()

        def decide(answer: str) -> None:
            # Pressing the answer already given takes it back: an edge nobody
            # has an opinion on is a real state, and the file says so by
            # writing nothing under `confirmed:` for it.
            link.decision = None if link.decision == answer else answer
            save_person(path, person)
            draw_answer()
            ui.notify(f"{person.name} and {link.target_name}: {link.decision or 'undecided'}")

        ui.button(icon="check", on_click=lambda: decide(CONFIRMED)).props(
            "flat dense round size=sm color=positive"
        ).tooltip("Yes, that is right")
        ui.button(icon="close", on_click=lambda: decide(REJECTED)).props(
            "flat dense round size=sm color=negative"
        ).tooltip("No, they are not")


def _add_person_dialog(path: Path) -> ui.dialog:
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-md p-5 gap-4"):
        ui.label("Add someone").classes("text-lg font-semibold").style("color: var(--im-text)")
        ui.label(
            "Use this for someone who is not face-tagged in Immich. You can connect them "
            "to the family immediately afterwards."
        ).classes("text-sm").style("color: var(--im-text-secondary)")
        name = ui.input(label="Full name").props("outlined autofocus").classes("w-full")

        def create() -> None:
            try:
                add_person(path, name.value or "")
            except ValueError as exc:
                ui.notify(str(exc), type="negative")
                return
            dialog.close()
            ui.notify(f"Added {name.value}", type="positive")
            ui.navigate.reload()

        with ui.row().classes("w-full justify-end gap-2"):
            im_button("Cancel", variant="ghost", on_click=dialog.close)
            im_button("Add person", on_click=create)
    return dialog


def _relationship_dialog(person: PersonView, people: list[PersonView], path: Path) -> ui.dialog:
    kinds = {choice.kind: choice.label for choice in RELATIONSHIP_CHOICES}
    targets = {
        other.person_id: other.name for other in people if other.person_id != person.person_id
    }
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-lg p-5 gap-4"):
        ui.label("Add a relationship").classes("text-lg font-semibold").style(
            "color: var(--im-text)"
        )
        ui.label(
            "One answer updates both people. Confirmed relationships are never replaced by a scan."
        ).classes("text-sm").style("color: var(--im-text-secondary)")
        with ui.column().classes("w-full gap-2"):
            ui.label(person.name).classes("font-medium").style("color: var(--im-text)")
            kind = ui.select(kinds, label="is…").props("outlined options-dense").classes("w-full")
            target = (
                ui.select(targets, label="Person", with_input=True)
                .props("outlined options-dense use-input input-debounce=0")
                .classes("w-full")
            )

        def save() -> None:
            if not kind.value or not target.value:
                ui.notify("Choose a relationship and a person", type="warning")
                return
            add_relationship(path, person.person_id, str(kind.value), str(target.value))
            dialog.close()
            ui.notify("Relationship confirmed", type="positive")
            ui.navigate.reload()

        with ui.row().classes("w-full justify-end gap-2"):
            im_button("Cancel", variant="ghost", on_click=dialog.close)
            im_button("Confirm relationship", on_click=save)
    return dialog
