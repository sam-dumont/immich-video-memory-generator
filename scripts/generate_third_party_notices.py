#!/usr/bin/env python3
"""Write THIRD_PARTY_NOTICES from THIRD_PARTY_NOTICES.in and uv.lock.

The hand-written sections (models, fonts, music, fixtures, services, tools) live
in the `.in` file. The Python dependency inventory is computed: every package
reachable from `immich-memories` in uv.lock through the runtime extras (never
`dev`), with the licence and project URL read from the installed metadata. A
package the lock knows but this environment does not have installed (a CUDA
wheel on a Mac, a Mac framework on Linux) falls back to the table below; one
that is in neither is written as UNKNOWN so `make notices-check` fails and
somebody looks it up.

Usage:
    python scripts/generate_third_party_notices.py
"""

from __future__ import annotations

import re
import tomllib
from collections import deque
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "uv.lock"
TEMPLATE = ROOT / "THIRD_PARTY_NOTICES.in"
OUTPUT = ROOT / "THIRD_PARTY_NOTICES"
PLACEHOLDER = "{{PYTHON_DEPENDENCIES}}"
ROOT_PACKAGE = "immich-memories"
EXCLUDED_EXTRAS = {"dev", "all", "all-mac"}

# Packages the lock resolves on another platform or device than the one
# generating this file. Licence and URL as published on PyPI.
KNOWN = {
    "demucs": ("MIT", "https://github.com/facebookresearch/demucs"),
    "torch": ("BSD-3-Clause", "https://pytorch.org/"),
    "torchaudio": ("BSD-2-Clause", "https://pytorch.org/audio"),
    "onnxruntime-gpu": ("MIT", "https://onnxruntime.ai"),
    "pyobjc-core": ("MIT", "https://github.com/ronaldoussoren/pyobjc"),
    "pyobjc-framework-cocoa": ("MIT", "https://github.com/ronaldoussoren/pyobjc"),
    "pyobjc-framework-vision": ("MIT", "https://github.com/ronaldoussoren/pyobjc"),
    "pyobjc-framework-quartz": ("MIT", "https://github.com/ronaldoussoren/pyobjc"),
    "pyobjc-framework-metal": ("MIT", "https://github.com/ronaldoussoren/pyobjc"),
    "pyobjc-framework-coreml": ("MIT", "https://github.com/ronaldoussoren/pyobjc"),
    "taichi": ("Apache-2.0", "https://github.com/taichi-dev/taichi"),
    "staticmap": ("Apache-2.0", "https://github.com/komoot/staticmap"),
    "immich-memories-music": (
        "MIT (tracks generated locally, see LICENSE-MUSIC)",
        "packages/immich-memories-music",
    ),
    "triton": ("MIT", "https://github.com/triton-lang/triton"),
    "tzdata": ("Apache-2.0", "https://github.com/python/tzdata"),
}

# torch on Linux resolves the CUDA runtime as wheels; they only ever reach an
# environment through the optional `demucs` extra.
_NVIDIA_PREFIXES = ("nvidia-", "cuda-")
_NVIDIA_LICENCE = (
    "NVIDIA proprietary (CUDA Toolkit EULA); pulled only by torch on Linux through the demucs extra"
)
_NVIDIA_URL = "https://docs.nvidia.com/cuda/eula/"

_SPDX_FROM_CLASSIFIER = {
    "MIT License": "MIT",
    "BSD License": "BSD",
    "Apache Software License": "Apache-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "Python Software Foundation License": "PSF-2.0",
    "GNU General Public License v2 or later (GPLv2+)": "GPL-2.0-or-later",
    "GNU General Public License (GPL)": "GPL",
    "ISC License (ISCL)": "ISC",
    "Public Domain": "Public Domain",
    "Historical Permission Notice and Disclaimer (HPND)": "HPND",
}


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_closure() -> dict[str, str]:
    """name -> version for every package the runtime extras can pull in."""
    lock = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    packages = {_normalise(p["name"]): p for p in lock["package"]}
    root = packages[_normalise(ROOT_PACKAGE)]
    queue: deque[tuple[str, tuple[str, ...]]] = deque()
    for dep in root.get("dependencies", []):
        queue.append((_normalise(dep["name"]), _extras_of(dep)))
    for extra, deps in root.get("optional-dependencies", {}).items():
        if extra in EXCLUDED_EXTRAS:
            continue
        for dep in deps:
            if _normalise(dep["name"]) == _normalise(ROOT_PACKAGE):
                continue
            queue.append((_normalise(dep["name"]), _extras_of(dep)))
    seen: dict[str, str] = {}
    seen_extras: set[tuple[str, tuple[str, ...]]] = set()
    while queue:
        name, extras = queue.popleft()
        if (name, extras) in seen_extras or name not in packages:
            continue
        seen_extras.add((name, extras))
        package = packages[name]
        seen[name] = package.get("version", "")
        deps = list(package.get("dependencies", []))
        for extra in extras:
            deps += package.get("optional-dependencies", {}).get(extra, [])
        for dep in deps:
            queue.append((_normalise(dep["name"]), _extras_of(dep)))
    return seen


def _extras_of(dep: dict) -> tuple[str, ...]:
    """uv.lock writes `extra` as a string or a list; normalise to a tuple."""
    extra = dep.get("extra")
    if not extra:
        return ()
    return (extra,) if isinstance(extra, str) else tuple(extra)


def _licence_from_metadata(dist: metadata.Distribution) -> str:
    expression = dist.metadata.get("License-Expression")
    if expression:
        return expression
    classifiers = [
        c.split("::")[-1].strip()
        for c in dist.metadata.get_all("Classifier", [])
        if c.startswith("License ::")
    ]
    spdx = [_SPDX_FROM_CLASSIFIER.get(c, c) for c in classifiers if c != "OSI Approved"]
    if spdx:
        return "; ".join(dict.fromkeys(spdx))
    licence = (dist.metadata.get("License") or "").strip()
    if licence and "\n" not in licence and len(licence) < 60:
        return licence
    return "UNKNOWN"


def _url_from_metadata(dist: metadata.Distribution) -> str:
    for line in dist.metadata.get_all("Project-URL", []):
        label, _, url = line.partition(",")
        if label.strip().lower() in {"homepage", "source", "repository", "source code", "github"}:
            return url.strip()
    home = dist.metadata.get("Home-page")
    if home:
        return home.strip()
    for line in dist.metadata.get_all("Project-URL", []):
        return line.partition(",")[2].strip()
    return ""


def describe(name: str, version: str) -> tuple[str, str]:
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        if name.startswith(_NVIDIA_PREFIXES):
            return _NVIDIA_LICENCE, _NVIDIA_URL
        licence, url = KNOWN.get(name, ("UNKNOWN", ""))
        return licence, url
    licence = _licence_from_metadata(dist)
    url = _url_from_metadata(dist)
    if licence == "UNKNOWN" and name in KNOWN:
        licence = KNOWN[name][0]
    if not url and name in KNOWN:
        url = KNOWN[name][1]
    return licence, url


def render_inventory() -> str:
    closure = runtime_closure()
    lines = [
        f"{len(closure)} packages, computed from uv.lock: everything reachable from",
        f"{ROOT_PACKAGE} through the runtime extras (never the dev tools). Regenerate",
        "with `make notices`; `make notices-check` fails when this list is stale.",
        "",
    ]
    for name in sorted(closure):
        licence, url = describe(name, closure[name])
        lines.append(f"{name} {closure[name]}")
        lines.append(f"  License: {licence}")
        if url:
            lines.append(f"  URL: {url}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    template = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        raise SystemExit(f"{TEMPLATE} has no {PLACEHOLDER}")
    OUTPUT.write_text(template.replace(PLACEHOLDER, render_inventory()), encoding="utf-8")
    unknown = [
        line for line in OUTPUT.read_text(encoding="utf-8").splitlines() if "UNKNOWN" in line
    ]
    print(f"wrote {OUTPUT.name}; {len(unknown)} UNKNOWN licence line(s)")
    return 1 if unknown else 0


if __name__ == "__main__":
    raise SystemExit(main())
