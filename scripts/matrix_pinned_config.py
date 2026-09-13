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


class _Drop:
    """A pin that takes a field OUT of the copied config instead of writing one over it."""

    def __repr__(self) -> str:
        return "<drop>"


# Both preset mechanisms in the app fill a field only where it is still at the
# model's default (`apply_preset`, and `_PROVIDER_PRESETS` in
# `analysis/llm_query.py`), so a value the copied config happens to carry
# silently outranks them. Removing the key is the only way to let one apply.
DROP = _Drop()


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
    if value is DROP:
        _drop_pin(data, head, rest)
        return
    if not rest:
        data[head] = value
        return
    target = _section(data, head)
    *branches, leaf = rest.split(".")
    for branch in branches:
        target = target.setdefault(branch, {})
    target[leaf] = value


def _drop_pin(data: dict, head: str, rest: str) -> None:
    """Remove a field wherever the file keeps it, and never create a section to do it."""
    if not rest:
        data.pop(head, None)
        return
    *branches, leaf = rest.split(".")
    for holder in (data, data.get("advanced") or {}):
        target = holder.get(head)
        for branch in branches:
            target = target.get(branch) if isinstance(target, dict) else None
        if isinstance(target, dict):
            target.pop(leaf, None)


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


def remote_path_pins(*, out: str, models: str) -> dict[str, str]:
    """Every path-valued field on Config, aimed at a container's roots instead of a laptop's.

    A remote cell copies the operator's own config for its Immich credentials, and
    every one of these fields comes with it. The second real remote run died on
    `detectors: FileNotFoundError` for a venv interpreter under /Users, carried
    into a NAS container from a Mac three floors away, and the cell reported no
    cut at all. Blank is used wherever blank is the field's own "work it out
    here" default: the interpreter running, the Hugging Face cache, the bundle
    shipped inside the wheel.

    The cache trio (`cache.directory`, `cache.database`,
    `editorial.annotation_database`) is not here: `cache_pins` owns it and pins it
    for every lane, remote or not.
    """
    return {
        "output.directory": out,
        # Read by `immich-memories music` and nothing the matrix runs, so this is
        # about the file naming no directory of the operator's, not about music.
        "audio.local_music_dir": f"{out}/music",
        "triage.encoder": f"{models}/triage/dinov2-small.onnx",
        "triage.bundle": "",
        "editorial.preparation.head_bundle": "",
        "editorial.preparation.detector_python": "",
        "editorial.preparation.detector_cache_dir": f"{models}/huggingface",
        "editorial.preparation.marqo_onnx": f"{models}/detectors/nsfw-marqo-384.onnx",
    }


def hosted_reader_pins() -> dict[str, Any]:
    """What a cell reading off somebody else's API must not inherit from the operator's `llm:`.

    That block describes the server on the operator's desk. `no_thinking_params`
    is oMLX's own chat-template switch, and z.ai answers a request carrying it
    with a 200 whose body is a 404 (`KeyError: 'choices'`). `base_url` is the
    same story one level up: a named provider carries its own, and z.ai's
    Anthropic-compatible endpoint is not where `/chat/completions` lives.

    Dropped rather than overwritten, because `_PROVIDER_PRESETS` fills each of
    these only where the field is still at the model's default. A cell that names
    one of them itself still wins: these are applied before the cell's own pins.
    """
    return {"llm.base_url": DROP, "llm.no_thinking_params": DROP}
