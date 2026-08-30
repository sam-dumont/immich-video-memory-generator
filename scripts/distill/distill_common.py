#!/usr/bin/env python3
"""Shared plumbing for the card-model distillation pipeline (docs/research/2026-08-30).

Nothing here talks to the network or to a model. It owns the four things every
stage needs to agree on: where private artefacts live, how a row becomes
parquet, how the deterministic sample is ordered, and how the production prompt
builders are reached without copying them.
"""

from __future__ import annotations

import csv
import hashlib
import importlib
import json
import os
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The pipeline writes nothing into the repo. Private corpus, labels and weights
# live beside the matrix dir the other probes already use.
DEFAULT_ROOT = Path.home() / ".immich-memories-distill"
CONFIG_PATH = Path.home() / ".immich-memories" / "config.yaml"

# Verified 2026-08-30 by HTTP HEAD; see RUNBOOK.md "Verified endpoints".
CVDF_IMAGE_URL = "https://open-images-dataset.s3.amazonaws.com/{split}/{image_id}.jpg"
CLASS_DESCRIPTIONS_URL = (
    "https://storage.googleapis.com/openimages/v7/oidv7-class-descriptions.csv"
)
IMAGE_METADATA_URLS = {
    "train": "https://storage.googleapis.com/openimages/2018_04/train/train-images-boxable-with-rotation.csv",
    "validation": "https://storage.googleapis.com/openimages/2018_04/validation/validation-images-with-rotation.csv",
    "test": "https://storage.googleapis.com/openimages/2018_04/test/test-images-with-rotation.csv",
}
MACHINE_LABEL_URLS = {
    "train": "https://storage.googleapis.com/openimages/v5/train-annotations-machine-imagelabels.csv",
    "validation": "https://storage.googleapis.com/openimages/v5/validation-annotations-machine-imagelabels.csv",
    "test": "https://storage.googleapis.com/openimages/v5/test-annotations-machine-imagelabels.csv",
}
HUMAN_LABEL_URLS = {
    "train": "https://storage.googleapis.com/openimages/v7/oidv7-train-annotations-human-imagelabels.csv",
    "validation": "https://storage.googleapis.com/openimages/v7/oidv7-val-annotations-human-imagelabels.csv",
}
# Captions-only Localized Narratives (CC BY 4.0). The default *_localized_narratives.jsonl
# files carry mouse traces and are 100x larger for supervision we never read.
NARRATIVE_URLS = {
    "train": "https://storage.googleapis.com/localized-narratives/annotations/open_images_train_v6_captions.jsonl",
    "validation": "https://storage.googleapis.com/localized-narratives/annotations/open_images_validation_captions.jsonl",
    "test": "https://storage.googleapis.com/localized-narratives/annotations/open_images_test_captions.jsonl",
}

CC_BY_2_0 = "https://creativecommons.org/licenses/by/2.0/"
LICENSE_NAME = "CC BY 2.0"
PERSON_DISPLAY_NAME = "Person"

# docs/research §4.1 names eleven of these; the rest are the same personal-life
# neighbourhood resolved against the 20,932-class V7 vocabulary. Stored as
# DisplayNames and resolved to /m/ MIDs at run time -- never hardcode a MID.
PERSONAL_LIFE_VOCABULARY: tuple[str, ...] = (
    "Snapshot",
    "Party",
    "Birthday",
    "Birthday cake",
    "Family",
    "Toddler",
    "Pet",
    "Vacation",
    "Picnic",
    "Barbecue",
    "Baby shower",
    "Wedding",
    "Christmas",
    "Playground",
    "Beach",
    "Swimming pool",
    "Camping",
    "Hiking",
    "Child",
    "Baby",
    "Selfie",
    "Fun",
    "Ceremony",
    "Holiday",
    "Dinner",
    "Meal",
    "Bride",
    "Grandparent",
    "Sibling",
    "Friendship",
    "Smile",
    "Laugh",
    "Concert",
    "Dance",
    "Costume party",
    "Halloween",
    "Easter",
    "New Year",
    "Graduation",
    "Prom",
    "Anniversary",
    "Road trip",
    "Tourism",
    "Travel",
    "Backyard",
    "Living room",
    "Kitchen",
    "Garden",
    "Park",
    "Zoo",
    "Amusement park",
    "Fair",
    "Carnival",
    "Parade",
    "Skiing",
    "Surfing",
    "Fishing",
    "Boating",
    "Cycling",
    "Cake",
    "Cupcake",
    "Ice cream",
    "Champagne",
    "Balloon",
    "Confetti",
    "Fireworks",
    "Candle",
    "Toy",
    "Doll",
    "Stuffed toy",
    "Nursery",
    "Kindergarten",
)

# docs/research §4.1 measured ~2.6% institutional/archival authors. This is a
# substring heuristic over Author + Title, not a classifier: it is tuned to
# over-reject (an archive slipping through pollutes the personal-photo domain,
# a wrongly-rejected snapshot costs one row out of hundreds of thousands).
# Known false positive: a person surnamed e.g. "Church". Unmeasured recall.
INSTITUTIONAL_MARKERS: tuple[str, ...] = (
    "archive",
    "archiv",
    "arkiv",
    "library",
    "bibliot",
    "museum",
    "musee",
    "museo",
    "university",
    "universit",
    "institute",
    "institut",
    "college",
    "academy",
    "foundation",
    "heritage",
    "herbarium",
    "collection",
    "historical society",
    "national park service",
    "state records",
    "public records",
    "department of",
    "ministry of",
    "nasa",
    "noaa",
    "usgs",
    "smithsonian",
    "commons",
    "gallery",
    "conservancy",
    "biodiversity",
    "wikimedia",
    "government",
    "council",
)


@dataclass(frozen=True)
class LLMEndpoint:
    """Where the teacher is served and what to call it."""

    base_url: str
    api_key: str
    model: str

    @property
    def chat_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    @property
    def models_url(self) -> str:
        return self.base_url.rstrip("/") + "/models"


def load_llm_endpoint(
    *,
    base_url: str | None = None,
    model: str | None = None,
    config_path: Path | None = None,
) -> LLMEndpoint:
    """Read the omlx credentials from the app config, honouring explicit overrides.

    The key is looked up at ``llm.api_key`` and then ``advanced.llm.api_key``;
    the runtime flattens the tier-2 section, so both spellings are live configs.
    """
    path = config_path or CONFIG_PATH
    section: dict[str, Any] = {}
    if path.exists():
        import yaml  # deferred: only the credential path needs it

        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        advanced = loaded.get("advanced") or {}
        section = {**(advanced.get("llm") or {}), **(loaded.get("llm") or {})}
    key = os.environ.get("OMLX_API_KEY") or str(section.get("api_key") or "")
    resolved_url = base_url or str(section.get("base_url") or "http://localhost:9999/v1")
    if not model:
        raise ValueError("teacher model must be named explicitly")
    return LLMEndpoint(base_url=resolved_url, api_key=key, model=model)


def production_prompt_constants(repo_root: Path | None = None) -> dict[str, Any]:
    """Import the live card/description prompt pieces from the tree, never a copy.

    The card shape and the description request are both mid-flight in
    ``probe/764-structure-shape`` (the ``setting`` slot landed recently). Reading
    them through ``importlib`` at call time means this pipeline teaches whatever
    schema the tree currently ships, including fields added after it was written.
    """
    root = repo_root or Path(__file__).resolve().parents[2]
    scripts_dir = root / "scripts"
    src_dir = root / "src"
    for entry in (str(scripts_dir), str(src_dir)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    matrix = importlib.import_module("probe_smart_edit_matrix")
    prototype = importlib.import_module("probe_description_moment_cut")
    descriptions = importlib.import_module("immich_memories.analysis.selection_descriptions")
    return {
        "card_shape": dict(matrix.HEDGED_CARD_SHAPE),
        "card_sentence": str(matrix.HEDGED_CARD_SENTENCE),
        "card_schema": str(prototype.CARD_SCHEMA),
        "card_max_chars": int(prototype.MAX_CARD_CHARS),
        "description_prompt": str(descriptions._PROMPT),
        "description_schema": str(descriptions.ASSET_DESCRIPTION_SCHEMA_VERSION),
        "description_max_chars": int(descriptions.ASSET_DESCRIPTION_MAX_CHARS),
        "setting_max_chars": int(descriptions.ASSET_SETTING_MAX_CHARS),
        "setting_hedge": str(descriptions.SETTING_HEDGE),
        "tile_px": int(descriptions.ASSET_DESCRIPTION_TILE_PX),
    }


def sample_key(seed: int, image_id: str) -> str:
    """Order candidates so that a larger --count is a strict superset of a smaller one."""
    return hashlib.sha256(f"{seed}:{image_id}".encode()).hexdigest()


def deterministic_order(image_ids: Iterable[str], *, seed: int) -> list[str]:
    return sorted(dict.fromkeys(image_ids), key=lambda one: sample_key(seed, one))


def is_institutional(author: str, title: str = "") -> bool:
    haystack = f"{author} {title}".casefold()
    return any(marker in haystack for marker in INSTITUTIONAL_MARKERS)


def is_cc_by_two(license_url: str) -> bool:
    return license_url.strip().rstrip("/") == CC_BY_2_0.rstrip("/")


def keeps_row(row: dict[str, str]) -> bool:
    """The §4 licence gate: plain CC BY 2.0, a named author, not an institution."""
    author = (row.get("Author") or "").strip()
    if not author or not (row.get("AuthorProfileURL") or "").strip():
        return False
    if not is_cc_by_two(row.get("License") or ""):
        return False
    return not is_institutional(author, (row.get("Title") or "").strip())


def resolve_vocabulary(
    class_rows: Iterable[dict[str, str]],
    display_names: Sequence[str],
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Map DisplayName -> LabelName for the wanted classes; report what did not resolve."""
    wanted = {name.casefold(): name for name in display_names}
    resolved: dict[str, str] = {}
    for row in class_rows:
        key = (row.get("DisplayName") or "").strip().casefold()
        if key in wanted and wanted[key] not in resolved:
            resolved[wanted[key]] = (row.get("LabelName") or "").strip()
    missing = tuple(name for name in display_names if name not in resolved)
    return resolved, missing


def read_csv_rows(path: Path) -> Iterator[dict[str, str]]:
    """Stream a multi-GB Open Images CSV without holding it in memory."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_parquet(rows: Sequence[dict[str, Any]], path: Path, columns: Sequence[str]) -> None:
    """Write the given columns in the given order. Never drop licence/creator columns.

    docs/research §9.3 rule 2: publishing a caption dataset with the creator or
    licence columns dropped is the one act that lands inside §1202(b)(3).
    """
    import pyarrow  # deferred: the pure-logic paths and the tests do not need it
    import pyarrow.parquet

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pyarrow.table(
        {name: [_parquet_cell(row.get(name)) for row in rows] for name in columns}
    )
    pyarrow.parquet.write_table(table, path)


def _parquet_cell(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def read_parquet(path: Path) -> list[dict[str, Any]]:
    import pyarrow.parquet  # deferred, as above

    return pyarrow.parquet.read_table(path).to_pylist()


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    """Append-only write-ahead log. A killed run loses at most the row in flight."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def duration_label(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"
