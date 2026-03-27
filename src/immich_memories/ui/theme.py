"""Immich-inspired theme system with dark/light/system mode support."""

from __future__ import annotations

from pathlib import Path

from nicegui import app, ui

# ---------------------------------------------------------------------------
# Serve bundled static assets (fonts, etc.) — no external CDN requests
# ---------------------------------------------------------------------------
_STATIC_DIR = Path(__file__).parent / "static"
app.add_static_files("/static", str(_STATIC_DIR))

# ---------------------------------------------------------------------------
# Color tokens — derived from Immich's actual design language
# ---------------------------------------------------------------------------

_THEME_CSS = """
@font-face {
    font-family: 'Inter';
    font-style: normal;
    font-weight: 400 700;
    font-display: swap;
    src: url(/static/fonts/inter-latin-ext.woff2) format('woff2');
    unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7,
        U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F,
        U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113,
        U+2C60-2C7F, U+A720-A7FF;
}
@font-face {
    font-family: 'Inter';
    font-style: normal;
    font-weight: 400 700;
    font-display: swap;
    src: url(/static/fonts/inter-latin.woff2) format('woff2');
    unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6,
        U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC,
        U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD;
}

:root {
    --im-primary: #4250af;
    --im-primary-hover: #3a47a0;
    --im-primary-light: rgba(66, 80, 175, 0.08);
    --im-bg: #ffffff;
    --im-bg-surface: #fafafa;
    --im-bg-elevated: #ffffff;
    --im-text: #000000;
    --im-text-secondary: #6b7280;
    --im-text-muted: #9ca3af;
    --im-border: rgba(0,0,0,0.08);
    --im-border-hover: rgba(0,0,0,0.15);
    --im-border-light: #f3f4f6;
    --im-sidebar-bg: #ffffff;
    --im-sidebar-active: rgba(66, 80, 175, 0.08);
    --im-sidebar-text: #374151;
    --im-sidebar-text-active: #4250af;
    --im-success: #16a34a;
    --im-success-bg: rgba(22,163,74,0.08);
    --im-success-text: #15803d;
    --im-warning: #d97706;
    --im-warning-bg: rgba(217,119,6,0.08);
    --im-warning-text: #b45309;
    --im-error: #dc2626;
    --im-error-bg: rgba(220,38,38,0.08);
    --im-error-text: #b91c1c;
    --im-info: var(--im-primary);
    --im-info-bg: rgba(66,80,175,0.08);
    --im-analysis: #9333ea;
    --im-analysis-bg: rgba(147,51,234,0.08);
    --im-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
    --im-shadow-md: 0 4px 6px rgba(0,0,0,0.05), 0 2px 4px rgba(0,0,0,0.04);
}

.body--dark {
    --im-primary: #6B8FE8;
    --im-primary-hover: #5A7FD8;
    --im-primary-light: rgba(107, 143, 232, 0.12);
    --im-bg: #09090b;
    --im-bg-surface: #111113;
    --im-bg-elevated: #1a1a1e;
    --im-text: #dbdbdb;
    --im-text-secondary: #d4d4d4;
    --im-text-muted: #a1a1a1;
    --im-border: rgba(255,255,255,0.08);
    --im-border-hover: rgba(255,255,255,0.15);
    --im-border-light: rgba(255,255,255,0.06);
    --im-sidebar-bg: #09090b;
    --im-sidebar-active: rgba(107, 143, 232, 0.12);
    --im-sidebar-text: #d4d4d4;
    --im-sidebar-text-active: #6B8FE8;
    --im-success: #4ade80;
    --im-success-bg: rgba(74,222,128,0.06);
    --im-success-text: #86efac;
    --im-warning: #fbbf24;
    --im-warning-bg: rgba(251,191,36,0.06);
    --im-warning-text: #fde68a;
    --im-error: #f87171;
    --im-error-bg: rgba(248,113,113,0.06);
    --im-error-text: #fca5a5;
    --im-info: #6B8FE8;
    --im-info-bg: rgba(107,143,232,0.08);
    --im-analysis: #c084fc;
    --im-analysis-bg: rgba(192,132,252,0.06);
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
.q-field, .q-menu {
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

/* Card shadow — light mode only, dark mode relies on border contrast */
.im-card { box-shadow: var(--im-shadow); }
.body--dark .im-card { box-shadow: none; }

/* Button hover micro-interaction */
.q-btn--unelevated:hover {
    transform: translateY(-1px);
    filter: brightness(0.92);
}
.q-btn--outline:hover {
    border-color: var(--im-primary) !important;
    background: var(--im-primary-light) !important;
}

/* Quasar default transition is 0.3s — tighten to feel snappier.
   Excludes q-drawer (manages own show/hide) and q-menu (positioning). */
.q-btn, .q-field, .q-card, .q-tab {
    transition: all 0.15s ease !important;
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

/* Alert/info card backgrounds — use semantic token vars */
.im-alert-info { background-color: var(--im-info-bg); }
.im-alert-warning { background-color: var(--im-warning-bg); }
.im-alert-success { background-color: var(--im-success-bg); }
.im-alert-error { background-color: var(--im-error-bg); }

/* Typography scale */
.im-heading   { font-size: 1.5rem; font-weight: 600; letter-spacing: -0.01em; line-height: 1.3; }
.im-subhead   { font-size: 1.125rem; font-weight: 500; line-height: 1.4; }
.im-body      { font-size: 1rem; line-height: 1.6; }
.im-small     { font-size: 0.875rem; color: var(--im-text-secondary); line-height: 1.5; }
.im-caption   { font-size: 0.75rem; color: var(--im-text-secondary); line-height: 1.4; }

/* Semantic badge classes */
.im-badge {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 2px 8px; border-radius: 6px;
    font-size: 0.75rem; font-weight: 500; line-height: 1.4;
}
.im-badge-success { color: var(--im-success-text); background: var(--im-success-bg); }
.im-badge-warning { color: var(--im-warning-text); background: var(--im-warning-bg); }
.im-badge-error   { color: var(--im-error-text); background: var(--im-error-bg); }
.im-badge-info    { color: var(--im-primary); background: var(--im-info-bg); }
.im-badge-analysis { color: var(--im-analysis); background: var(--im-analysis-bg); }

/* Demo/privacy mode: blur all thumbnails and video elements */
body.demo-mode img:not(.no-blur),
body.demo-mode video {
    filter: blur(12px) !important;
    -webkit-filter: blur(12px) !important;
}

/* Fixed aspect containers for media — prevent layout shift */
.im-media-container {
    aspect-ratio: 16/9;
    overflow: hidden;
    border-radius: 8px;
    background: var(--im-bg-surface);
    position: relative;
}
/* Target raw img/video AND NiceGUI's q-img Quasar wrapper */
.im-media-container > img,
.im-media-container > video,
.im-media-container > .q-img,
.im-media-container > div {
    width: 100% !important;
    height: 100% !important;
    object-fit: cover;
}
.im-media-container .q-img__image {
    object-fit: cover !important;
}
.im-media-container--contain > img,
.im-media-container--contain > video,
.im-media-container--contain > .q-img,
.im-media-container--contain > div {
    object-fit: contain;
}
.im-media-container--contain .q-img__image {
    object-fit: contain !important;
}
/* Placeholder skeleton for loading states */
.im-skeleton {
    background: linear-gradient(90deg,
        var(--im-bg-surface) 25%,
        var(--im-border-light) 50%,
        var(--im-bg-surface) 75%);
    background-size: 200% 100%;
    animation: im-shimmer 1.5s infinite;
}
@keyframes im-shimmer {
    0% { background-position: 200% 0; }
    100% { background-position: -200% 0; }
}
/* Compact flow utility */
.im-flow-tight > * + * { margin-top: 8px; }
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
        ui.colors(primary="#6B8FE8")
    elif pref == "light":
        ui.dark_mode(False)
        ui.colors(primary="#4250af")
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
