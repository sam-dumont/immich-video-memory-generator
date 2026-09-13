"""A config copy with the settings a sweep varies pinned to known values.

Shared by `capability_matrix.py` (which varies render capabilities) and
`setup_matrix.py` (which varies the setup a run happens on). Both need the same
thing: the operator's own config for its Immich credentials, with every axis the
sweep claims to vary written over the top.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from immich_memories.config_loader import _TIER2_SECTIONS, Config
from immich_memories.config_presets import PRESETS


def _section(data: dict, name: str) -> dict:
    """The dict a pin's first path segment names, wherever the file happens to keep it.

    WHY the walk: a tier-2 section may sit at top level (legacy) or under
    `advanced:`, and the loader resolves the clash with `if key not in data` --
    top level wins. Writing the modern spelling into a config using the old one
    leaves the edit inert.
    """
    if name in data:
        return data[name]
    if name in data.get("advanced", {}):
        return data["advanced"][name]
    if name in _TIER2_SECTIONS:
        return data.setdefault("advanced", {}).setdefault(name, {})
    return data.setdefault(name, {})


def _write_pin(data: dict, dotted: str, value: Any) -> None:
    head, _, rest = dotted.partition(".")
    if not rest:
        data[head] = value
        return
    target = _section(data, head)
    *branches, leaf = rest.split(".")
    for branch in branches:
        target = target.setdefault(branch, {})
    target[leaf] = value


def _clear_preset_fields(data: dict, preset: str) -> None:
    """Hand every field the preset owns back to the preset.

    WHY: `apply_preset` fills only fields the user has NOT set, so any key the
    local config happens to carry silently outranks the preset -- and the row
    renders a partial profile while claiming to show the whole one.
    """
    for section_name, values in PRESETS[preset].items():
        section = data.get(section_name)
        if isinstance(section, dict):
            for field in values:
                section.pop(field, None)


def pinned_config(source: Path | None, dest: Path, pins: dict) -> Path:
    """Write `source` to `dest` with `pins` applied, and return `dest`.

    Pin keys are dotted paths (`output.resolution`, `editorial.preparation.tier`).
    A bare key with no dot replaces the whole top-level value, which is how
    `preset` is set. Explicit pins are applied after the preset clear, so a row
    can still override one of the preset's own fields.

    WHY at all: inheriting the developer's config means the rows test that
    config, not the code. The first capability sweep ran every row at 4K because
    one local line said so, never touching 1080p, which is the shipped default
    and what a NAS actually runs.
    """
    source = source or Config.get_default_path()
    if not source.exists():
        raise SystemExit(
            f"{source} does not exist, and the sweep needs a real config to copy "
            "for its Immich credentials. Pass --config explicitly."
        )
    data = yaml.safe_load(source.read_text()) or {}

    if "preset" in pins:
        _clear_preset_fields(data, pins["preset"])
    for dotted, value in pins.items():
        _write_pin(data, dotted, value)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(yaml.safe_dump(data, sort_keys=False))
    return dest
