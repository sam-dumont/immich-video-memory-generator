"""Settings page: YAML config viewer with redacted secrets."""

from __future__ import annotations

import logging
from typing import Any

from nicegui import ui

from immich_memories.config import Config, get_config
from immich_memories.ui.components import im_button, im_info_card, im_section_header

logger = logging.getLogger(__name__)

# Fields that contain secrets — redacted in display
# WHY: notification URLs often embed credentials (e.g. apprise://user:pass@host)
_SENSITIVE_KEYS = {
    "api_key",
    "api_keys",
    "client_secret",
    "password",
    "secret",
    "token",
    "urls",
}


def _redact(data: Any, _key: str = "") -> Any:
    """Recursively redact sensitive values in a config dict."""
    if isinstance(data, dict):
        return {k: _redact(v, k) for k, v in data.items()}
    if isinstance(data, list):
        return [_redact(v, _key) for v in data]
    if _key in _SENSITIVE_KEYS and isinstance(data, str) and data:
        return data[:3] + "***" + data[-2:] if len(data) > 5 else "***"
    return data


def _format_value(value: Any) -> str:
    """Format a config value for compact display."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "(empty)"
    if isinstance(value, dict):
        parts = [f"{k}={v}" for k, v in value.items()]
        return "{" + ", ".join(parts) + "}"
    return str(value)


def _section_icon(section: str) -> str:
    """Map config section names to Material icons."""
    return {
        "immich": "cloud",
        "defaults": "tune",
        "analysis": "analytics",
        "output": "folder",
        "cache": "cached",
        "hardware": "memory",
        "llm": "psychology",
        "audio": "music_note",
        "musicgen": "queue_music",
        "ace_step": "piano",
        "content_analysis": "visibility",
        "audio_content": "mic",
        "title_screens": "title",
        "upload": "cloud_upload",
        "scheduler": "schedule",
    }.get(section, "settings")


def _render_section_table(section_data: dict) -> None:
    """Render a config section as a compact HTML table."""
    rows_html: list[str] = []
    for key, value in section_data.items():
        if isinstance(value, dict):
            # Nested — render as sub-header + rows
            rows_html.append(
                f'<tr><td colspan="2" style="color:var(--im-text);font-weight:600;'
                f'padding:4px 0 2px 0;font-size:12px">{key}</td></tr>'
            )
            for sk, sv in value.items():
                display = _format_value(sv)
                rows_html.append(
                    f'<tr><td style="color:var(--im-text-secondary);padding:1px 12px 1px 16px;'
                    f'font-size:12px;white-space:nowrap">{sk}</td>'
                    f'<td style="color:var(--im-text);font-family:monospace;font-size:12px;'
                    f'padding:1px 0;word-break:break-all">{display}</td></tr>'
                )
        else:
            display = _format_value(value)
            rows_html.append(
                f'<tr><td style="color:var(--im-text-secondary);padding:1px 12px 1px 0;'
                f'font-size:12px;white-space:nowrap">{key}</td>'
                f'<td style="color:var(--im-text);font-family:monospace;font-size:12px;'
                f'padding:1px 0;word-break:break-all">{display}</td></tr>'
            )

    table_html = (
        '<table style="width:100%;border-collapse:collapse">' + "".join(rows_html) + "</table>"
    )
    ui.html(table_html)


def render_config_viewer() -> None:
    """Render the configuration viewer panel."""
    config = get_config(reload=True)
    redacted = _redact(config.model_dump())

    im_info_card(
        "Active config from ~/.immich-memories/config.yaml with env overrides. "
        "API keys are redacted.",
        variant="info",
    )

    # Render each section as a collapsible expansion panel
    for section_name, section_data in redacted.items():
        if not isinstance(section_data, dict):
            continue

        icon = _section_icon(section_name)
        title = section_name.replace("_", " ").title()
        n_keys = sum(1 + (len(v) if isinstance(v, dict) else 0) for v in section_data.values())

        with (
            ui.expansion(
                f"{title}  ({n_keys} keys)",
                icon=icon,
                value=section_name in {"immich", "analysis", "output", "defaults"},
            )
            .classes("w-full mt-1")
            .style(
                "background:var(--im-bg-elevated);border:1px solid var(--im-border-light);"
                "border-radius:8px"
            )
        ):
            _render_section_table(section_data)


def render_config_page() -> None:
    """Render the full config settings page."""
    im_section_header("Active Configuration", icon="description")
    render_config_viewer()

    im_section_header("Actions", icon="build")
    with ui.row().classes("gap-3"):

        def reload_config():
            get_config(reload=True)
            ui.notify("Configuration reloaded from disk", type="positive")
            ui.navigate.reload()

        im_button("Reload from Disk", variant="secondary", on_click=reload_config, icon="refresh")

        config_path = Config.get_default_path()
        ui.label(f"Config file: {config_path}").classes("text-sm self-center").style(
            "color: var(--im-text-secondary)"
        )
