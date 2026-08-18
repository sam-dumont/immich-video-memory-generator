"""Named config presets — one switch that fills several knobs at once.

`preset: fast` is the CPU-only / NAS profile: no per-clip speech analysis, static title
backgrounds, the fast software encoder preset, medium quality at 1080p, fewer photos, and
`analysis_depth: auto` resolving to `fast` (favorites first). Anything the user has set
explicitly (config file key, env var, CLI flag) wins over the preset, like `clip_style`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from immich_memories.config_loader import Config

PresetName = Literal["fast"]

PRESETS: dict[str, dict[str, dict[str, Any]]] = {
    "fast": {
        "output": {"resolution": "1080p", "codec": "h264", "quality": "medium"},
        "hardware": {"encoder_preset": "fast"},
        "speech": {"enabled": False},
        "title_screens": {"animated_background": False},
        "photos": {"max_ratio": 0.25},
    },
}


def apply_preset(config: Config) -> list[str]:
    """Fill every knob of `config.preset` that the user did not set; return what changed.

    "Set by the user" means the field is in the section's `model_fields_set` — a key in
    the YAML section, an `IMMICH_MEMORIES_<SECTION>__<FIELD>` env var, or an assignment
    made before this call. Later assignments (env overrides, CLI flags) still win because
    they happen after.
    """
    if config.preset is None:
        return []
    applied: list[str] = []
    for section_name, values in PRESETS[config.preset].items():
        section = getattr(config, section_name)
        for field, value in values.items():
            if field in section.model_fields_set:
                continue
            setattr(section, field, value)
            applied.append(f"{section_name}.{field}")
    return applied


def resolve_analysis_depth(requested: str, preset: str | None) -> str:
    """`auto` means `fast` (favorites first) under the fast preset; explicit depths stand."""
    if requested == "auto" and preset == "fast":
        return "fast"
    return requested
