"""Reusable Immich-styled UI component wrappers."""

from __future__ import annotations

from nicegui import ui


def im_card(interactive: bool = False, **kwargs) -> ui.card:
    """Themed card — flat style with subtle border, matching Immich."""
    card = ui.card(**kwargs).classes("rounded-lg w-full")
    card.style("background-color: var(--im-bg-elevated);border: 1px solid var(--im-border-light)")
    if interactive:
        card.classes("im-card-interactive cursor-pointer")
    return card


def im_section_header(title: str, icon: str | None = None) -> ui.row:
    """Section title with optional leading icon."""
    with ui.row().classes("items-center gap-2 mt-4 mb-2") as row:
        if icon:
            ui.icon(icon).classes("text-lg").style("color: var(--im-primary)")
        ui.label(title).classes("text-base font-semibold").style("color: var(--im-text)")
    return row


def im_stat_card(label: str, value: str, icon: str | None = None) -> ui.card:
    """Compact metric card for summary displays."""
    with im_card() as card:
        card.classes("p-3")
        with ui.column().classes("items-center gap-1"):
            if icon:
                ui.icon(icon).classes("text-xl").style("color: var(--im-primary)")
            ui.label(value).classes("text-xl font-bold").style("color: var(--im-text)")
            ui.label(label).classes("text-xs").style("color: var(--im-text-secondary)")
    return card


def im_button(
    text: str,
    variant: str = "primary",
    **kwargs,
) -> ui.button:
    """Themed button with primary/secondary/ghost variants."""
    btn = ui.button(text, **kwargs)
    if variant == "primary":
        btn.props("unelevated").classes("rounded-lg").style(
            "background: var(--im-primary) !important; color: #fff"
        )
    elif variant == "secondary":
        btn.props("outline").classes("rounded-lg").style(
            "border-color: var(--im-border); color: var(--im-text)"
        )
    elif variant == "ghost":
        btn.props("flat").classes("rounded-lg").style("color: var(--im-text-secondary)")
    return btn


def im_info_card(
    text: str,
    variant: str = "info",
) -> ui.element:
    """Alert-style info card with color variants."""
    text_color = (
        f"var(--im-{variant})"
        if variant in ("info", "warning", "success", "error")
        else "var(--im-info)"
    )
    bg_class = (
        f"im-alert-{variant}"
        if variant in ("info", "warning", "success", "error")
        else "im-alert-info"
    )

    with ui.element("div").classes(f"w-full rounded-lg p-3 {bg_class}") as container:
        ui.label(text).classes("text-sm").style(f"color: {text_color}")
    return container


def im_input(label: str, **kwargs) -> ui.input:
    """Themed text input."""
    return ui.input(label, **kwargs).classes("w-full")


def im_select(label: str, **kwargs) -> ui.select:
    """Themed select dropdown."""
    return ui.select(label=label, **kwargs).classes("w-full")


def im_badge(text: str, variant: str = "info", icon: str | None = None) -> ui.element:
    """Themed badge with semantic color variants."""
    css_class = f"im-badge im-badge-{variant}"
    with ui.element("span").classes(css_class) as badge:
        if icon:
            ui.icon(icon).classes("text-xs")
        ui.label(text)
    return badge


def im_separator() -> ui.element:
    """Themed horizontal separator — thin line."""
    return (
        ui.element("div")
        .classes("w-full my-4")
        .style("height: 1px; background: var(--im-border-light)")
    )
