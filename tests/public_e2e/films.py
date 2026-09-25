"""Run a household's films against a restored Immich, each one cold, each one timed.

Every film gets its own empty app home, so nothing one film learned (heads, banked
facts, a people graph) makes the next one cheaper: the time recorded is a first run's.
The rules tier mirrors a NAS install (no captions, no reader model). The model tier runs
only when `PUBLIC_E2E_LLM_BASE_URL` and `PUBLIC_E2E_LLM_MODEL` name an endpoint.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from tests.public_e2e.household import Film

_SCRUBBED = ("IMMICH_URL", "IMMICH_API_KEY", "OPENAI_API_KEY")
# The product's CLI through this interpreter: a `uv run --with` overlay has no script shim.
CLI = (sys.executable, "-c", "from immich_memories.cli import main; main()")


@dataclass(frozen=True)
class FilmRun:
    """What one film left behind."""

    film: Film
    home: Path
    exit_code: int
    wall_seconds: float
    log: Path

    @property
    def cache(self) -> Path:
        return self.home / ".immich-memories" / "cache"

    @property
    def output_dir(self) -> Path:
        return self.home / "output"

    def attempt_dir(self) -> Path | None:
        """The attempt the film rendered from: the newest one that recorded a run."""
        attempts = sorted(
            self.cache.glob("editorial-runs/*/attempts/*/plan.private.json"),
            key=lambda p: p.stat().st_mtime,
        )
        return attempts[-1].parent if attempts else None

    def video(self) -> Path | None:
        videos = sorted(self.output_dir.rglob("*.mp4"), key=lambda p: p.stat().st_mtime)
        return videos[-1] if videos else None


def _models_dir() -> Path:
    """The maintainer's fetched detectors, shared read-only by every cold home."""
    return Path(os.environ.get("PUBLIC_E2E_MODELS", Path.home() / ".immich-memories" / "models"))


def write_config(home: Path, url: str, api_key: str, home_json: Path, tier: str = "rules") -> Path:
    """The config a film runs with: this Immich only, the household's home base, the tier."""
    app = home / ".immich-memories"
    (app / "cache").mkdir(parents=True, exist_ok=True)
    (home / "output").mkdir(parents=True, exist_ok=True)
    models = _models_dir()
    if models.exists() and not (app / "models").exists():
        (app / "models").symlink_to(models)
    homebase = json.loads(home_json.read_text()) if home_json.exists() else None
    editorial: dict = {"reader": "rules", "preparation": {"tier": "no_captions"}}
    if os.environ.get("PUBLIC_E2E_DETECTOR_PYTHON"):
        editorial["preparation"]["detector_python"] = os.environ["PUBLIC_E2E_DETECTOR_PYTHON"]
    config: dict = {
        "immich": {"url": url, "api_key": api_key, "api_version": "auto"},
        "output": {
            "directory": str(home / "output"),
            "format": "mp4",
            "resolution": "720p",
            "codec": "h264",
            "hdr_mode": "sdr",
            "quality": "low",
        },
        "cache": {"directory": str(app / "cache"), "database": str(app / "cache.db")},
        "upload": {"enabled": False},
        "photos": {"enabled": True},
        "title_screens": {"enabled": True, "locale": "en"},
        "advanced": {
            "hardware": {"enabled": False, "backend": "none", "gpu_decode": False},
            "musicgen": {"enabled": False},
            "ace_step": {"enabled": False},
            "editorial": editorial,
        },
    }
    if homebase:
        config["trips"] = {"homebase_latitude": homebase[0], "homebase_longitude": homebase[1]}
    if tier == "model":
        config["llm"] = {
            "provider": "openai-compatible",
            "base_url": os.environ["PUBLIC_E2E_LLM_BASE_URL"],
            "model": os.environ["PUBLIC_E2E_LLM_MODEL"],
            "api_key": os.environ.get("PUBLIC_E2E_LLM_API_KEY", ""),
        }
        editorial["reader"] = "model"
    path = app / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    path.chmod(0o600)
    return path


def run_env(home: Path) -> dict[str, str]:
    # WHY: a developer's provider variables would point the run at their own library.
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("IMMICH_MEMORIES_", "ZAI_")) and k not in _SCRUBBED
    }
    env["HOME"] = str(home)
    env.setdefault("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
    return env


def run_film(
    film: Film,
    root: Path,
    url: str,
    api_key: str,
    home_json: Path,
    people_file: Path | None,
    tier: str,
) -> FilmRun:
    """Run one film in a fresh home and return where it left its traces."""
    home = root / film.id / "home"
    shutil.rmtree(home, ignore_errors=True)
    config = write_config(home, url, api_key, home_json, tier)
    if people_file is not None and people_file.exists():
        # The people file and the evidence graph the scan wrote beside it travel together.
        for companion in (people_file, people_file.with_name("people-graph.json")):
            if companion.exists():
                shutil.copy(companion, config.parent / companion.name)
    log = root / film.id / "generate.log"
    command = [
        *CLI,
        "--config",
        str(config),
        "generate",
        *film.args,
        "--no-music",
        "--trace-selection",
        str(root / film.id / "selection-trace.txt"),
    ]
    started = time.monotonic()
    with log.open("w") as handle:
        done = subprocess.run(
            command,
            stdout=handle,
            stderr=subprocess.STDOUT,  # noqa: S603
            env=run_env(home),
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=4 * 3600,
        )
    return FilmRun(film, home, done.returncode, round(time.monotonic() - started, 1), log)
