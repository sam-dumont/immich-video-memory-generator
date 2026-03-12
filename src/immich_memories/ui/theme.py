"""Immich-inspired theme system with dark/light/system mode support."""

from __future__ import annotations

from nicegui import app, ui

# ---------------------------------------------------------------------------
# Color tokens — derived from Immich's actual design language
# ---------------------------------------------------------------------------

_THEME_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

:root {
    --im-primary: #4250af;
    --im-primary-hover: #3a47a0;
    --im-primary-light: rgba(66, 80, 175, 0.08);
    --im-bg: #ffffff;
    --im-bg-surface: #f6f6f4;
    --im-bg-elevated: #ffffff;
    --im-text: #000000;
    --im-text-secondary: #6b7280;
    --im-text-muted: #9ca3af;
    --im-border: #e5e7eb;
    --im-border-light: #f3f4f6;
    --im-sidebar-bg: #ffffff;
    --im-sidebar-active: rgba(66, 80, 175, 0.08);
    --im-sidebar-text: #374151;
    --im-sidebar-text-active: #4250af;
    --im-success: #81c784;
    --im-warning: #ffb74d;
    --im-error: #e57373;
    --im-info: #4250af;
    --im-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
    --im-shadow-md: 0 4px 6px rgba(0,0,0,0.05), 0 2px 4px rgba(0,0,0,0.04);
}

.body--dark {
    --im-primary: #accbfa;
    --im-primary-hover: #8bb8f8;
    --im-primary-light: rgba(172, 203, 250, 0.10);
    --im-bg: #101010;
    --im-bg-surface: #1a1a1a;
    --im-bg-elevated: #242424;
    --im-text: #e5e7eb;
    --im-text-secondary: #a1a1aa;
    --im-text-muted: #71717a;
    --im-border: #3f3f46;
    --im-border-light: #2e2e35;
    --im-sidebar-bg: #101010;
    --im-sidebar-active: rgba(172, 203, 250, 0.12);
    --im-sidebar-text: #d4d4d8;
    --im-sidebar-text-active: #accbfa;
    --im-success: #81c784;
    --im-warning: #f57c00;
    --im-error: #e57373;
    --im-info: #accbfa;
    --im-shadow: none;
    --im-shadow-md: 0 2px 8px rgba(0,0,0,0.4);
}

body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background-color: var(--im-bg) !important;
    color: var(--im-text);
    -webkit-font-smoothing: antialiased;
}

/* Quasar overrides */
.q-page, .q-page-container {
    background-color: var(--im-bg) !important;
}
.q-drawer {
    background-color: var(--im-sidebar-bg) !important;
    border-right: 1px solid var(--im-border-light) !important;
}
.q-card {
    background-color: var(--im-bg-elevated) !important;
    box-shadow: none !important;
    border: 1px solid var(--im-border-light) !important;
    border-radius: 10px !important;
}
.q-field__control {
    color: var(--im-text) !important;
}
.q-field__label {
    color: var(--im-text-secondary) !important;
}
.q-tab-panels {
    background-color: transparent !important;
}
.q-expansion-item {
    border-color: var(--im-border-light) !important;
}
.q-separator {
    background-color: var(--im-border-light) !important;
}
/* Clean up Quasar input underlines */
.q-field--outlined .q-field__control::before {
    border-color: var(--im-border) !important;
}
.q-checkbox__inner--truthy .q-checkbox__bg {
    background: var(--im-primary) !important;
    border-color: var(--im-primary) !important;
}

/* Sidebar nav — Immich style: light bg, colored active state */
.im-nav-item {
    border-radius: 10px;
    padding: 10px 14px;
    transition: all 0.15s ease;
    color: var(--im-sidebar-text);
    cursor: pointer;
    margin: 1px 0;
}
.im-nav-item:hover {
    background: var(--im-primary-light);
    color: var(--im-sidebar-text-active);
}
.im-nav-active {
    background: var(--im-sidebar-active) !important;
    color: var(--im-sidebar-text-active) !important;
    font-weight: 600;
}

/* Interactive card hover */
.im-card-interactive {
    transition: background-color 0.15s ease, border-color 0.15s ease;
    cursor: pointer;
}
.im-card-interactive:hover {
    background-color: var(--im-bg-surface) !important;
    border-color: var(--im-border) !important;
}

/* Preset selected state */
.im-preset-selected {
    outline: 2px solid var(--im-primary) !important;
    outline-offset: -2px;
    background-color: var(--im-primary-light) !important;
}
"""


def inject_theme() -> None:
    """Inject global CSS variables and font into the page head."""
    ui.add_head_html(f"<style>{_THEME_CSS}</style>")


def apply_theme() -> None:
    """Read user theme preference and apply dark/light/system mode."""
    inject_theme()
    pref = app.storage.user.get("theme", "system")
    if pref == "dark":
        ui.dark_mode(True)
    elif pref == "light":
        ui.dark_mode(False)
    else:
        ui.dark_mode(None)
    ui.colors(primary="#4250af")


def render_theme_toggle() -> None:
    """Render a three-state theme toggle (light / system / dark)."""
    current = app.storage.user.get("theme", "system")

    icons = [
        ("light", "light_mode", "Light"),
        ("system", "brightness_auto", "System"),
        ("dark", "dark_mode", "Dark"),
    ]

    with ui.row().classes("gap-1 justify-center"):
        for value, icon, tooltip in icons:
            is_active = current == value

            def make_handler(v: str):
                def handler():
                    app.storage.user["theme"] = v
                    ui.navigate.reload()

                return handler

            btn = (
                ui.button(icon=icon, on_click=make_handler(value))
                .props("flat round size=sm")
                .tooltip(tooltip)
            )
            if is_active:
                btn.style("color: var(--im-primary); background: var(--im-primary-light)")
            else:
                btn.style("color: var(--im-text-muted)")
