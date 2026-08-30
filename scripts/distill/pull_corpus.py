#!/usr/bin/env python3
"""Stage A -- build the licence-clean personal-photo corpus from Open Images V7.

Implements the docs/research/2026-08-30-card-model-distillation.md §4 recipe:
personal-life label vocabulary intersected with ``Person``, plain CC BY 2.0 rows
only, institutional authors dropped, images pulled from the CVDF S3 mirror and
never from Flickr (§4.1's zero-link-rot path).

Resumable in three places: the metadata downloads resume by byte range, the
candidate scan is cached, and an image already on disk is never re-fetched.
A larger ``--count`` walks further down the same deterministic order, so it
tops up rather than reshuffling.

    uv run --with pyarrow --with httpx scripts/distill/pull_corpus.py --split validation --count 3000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from distill_common import (  # noqa: E402
    CC_BY_2_0,
    CLASS_DESCRIPTIONS_URL,
    CVDF_IMAGE_URL,
    DEFAULT_ROOT,
    HUMAN_LABEL_URLS,
    IMAGE_METADATA_URLS,
    LICENSE_NAME,
    MACHINE_LABEL_URLS,
    PERSON_DISPLAY_NAME,
    PERSONAL_LIFE_VOCABULARY,
    append_jsonl,
    deterministic_order,
    duration_label,
    keeps_row,
    read_csv_rows,
    read_jsonl,
    resolve_vocabulary,
    sha256_file,
    write_parquet,
)

MANIFEST_COLUMNS = (
    "image_id",
    "split",
    "s3_url",
    "local_path",
    "license_name",
    "license_url",
    "author",
    "author_profile_url",
    "original_landing_url",
    "title",
    "retrieved_at",
    "content_sha256",
    "bytes",
)


@dataclass(frozen=True)
class CorpusPaths:
    root: Path
    split: str

    @property
    def metadata(self) -> Path:
        return self.root / "metadata"

    @property
    def split_dir(self) -> Path:
        return self.root / self.split

    @property
    def images(self) -> Path:
        return self.split_dir / "images"

    @property
    def candidates(self) -> Path:
        return self.split_dir / "candidates.json"

    @property
    def downloads_log(self) -> Path:
        return self.split_dir / "downloads.jsonl"

    @property
    def manifest(self) -> Path:
        return self.split_dir / "manifest.parquet"


def fetch_to(url: str, destination: Path, *, label: str) -> Path:
    """Download with byte-range resume. A killed overnight pull continues where it stopped."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    if destination.exists():
        print(f"  {label}: cached ({destination.stat().st_size / 1e6:.1f} MB)", flush=True)
        return destination
    have = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with httpx.stream(
        "GET", url, headers=headers, timeout=120.0, follow_redirects=True
    ) as response:
        if have and response.status_code == 200:
            have = 0  # server ignored the range; restart cleanly
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0)) + have
        mode = "ab" if have else "wb"
        started = time.monotonic()
        with partial.open(mode) as handle:
            for chunk in response.iter_bytes(1 << 20):
                handle.write(chunk)
                have += len(chunk)
                if total and have % (64 << 20) < (1 << 20):
                    print(
                        f"  {label}: {have / 1e6:.0f}/{total / 1e6:.0f} MB "
                        f"({duration_label(time.monotonic() - started)})",
                        flush=True,
                    )
    partial.rename(destination)
    print(f"  {label}: done ({destination.stat().st_size / 1e6:.1f} MB)", flush=True)
    return destination


def ensure_metadata(paths: CorpusPaths, *, label_source: str) -> dict[str, Path]:
    table = HUMAN_LABEL_URLS if label_source == "human" else MACHINE_LABEL_URLS
    if paths.split not in table:
        raise SystemExit(f"no {label_source} labels published for split {paths.split}")
    print(f"metadata -> {paths.metadata}", flush=True)
    return {
        "classes": fetch_to(
            CLASS_DESCRIPTIONS_URL,
            paths.metadata / "oidv7-class-descriptions.csv",
            label="class-descriptions",
        ),
        "labels": fetch_to(
            table[paths.split],
            paths.metadata / f"{paths.split}-{label_source}-imagelabels.csv",
            label=f"{paths.split}-{label_source}-labels",
        ),
        "images": fetch_to(
            IMAGE_METADATA_URLS[paths.split],
            paths.metadata / f"{paths.split}-images-with-rotation.csv",
            label=f"{paths.split}-image-metadata",
        ),
    }


def scan_labels(
    labels_csv: Path,
    *,
    personal_mids: frozenset[str],
    person_mid: str,
    confidence: float,
) -> set[str]:
    """One streaming pass: ids carrying a personal-life label AND ``Person``."""
    personal: set[str] = set()
    people: set[str] = set()
    seen = 0
    for row in read_csv_rows(labels_csv):
        seen += 1
        if seen % 20_000_000 == 0:
            print(f"  labels: {seen / 1e6:.0f}M rows scanned", flush=True)
        try:
            if float(row.get("Confidence") or 0.0) < confidence:
                continue
        except ValueError:
            continue
        mid = (row.get("LabelName") or "").strip()
        if mid == person_mid:
            people.add((row.get("ImageID") or "").strip())
        elif mid in personal_mids:
            personal.add((row.get("ImageID") or "").strip())
    hits = personal & people
    print(
        f"  labels: {len(personal)} personal-life, {len(people)} Person, {len(hits)} both",
        flush=True,
    )
    return hits


def scan_licences(images_csv: Path, keep_ids: set[str]) -> dict[str, dict[str, str]]:
    """Second streaming pass: the §4 licence gate over the label-selected ids."""
    kept: dict[str, dict[str, str]] = {}
    rejected = 0
    for row in read_csv_rows(images_csv):
        image_id = (row.get("ImageID") or "").strip()
        if image_id not in keep_ids:
            continue
        if not keeps_row(row):
            rejected += 1
            continue
        kept[image_id] = {
            "author": (row.get("Author") or "").strip(),
            "author_profile_url": (row.get("AuthorProfileURL") or "").strip(),
            "original_landing_url": (row.get("OriginalLandingURL") or "").strip(),
            "title": (row.get("Title") or "").strip(),
            "license_url": (row.get("License") or "").strip(),
        }
    print(f"  licences: {len(kept)} kept, {rejected} rejected (NC/ND/blank/institutional)", flush=True)
    return kept


def build_candidates(paths: CorpusPaths, args: argparse.Namespace) -> dict[str, dict[str, str]]:
    """Resolve the candidate pool once and cache it; the scan costs GB of streaming."""
    if paths.candidates.exists() and not args.rescan:
        cached = json.loads(paths.candidates.read_text(encoding="utf-8"))
        print(f"candidates: {len(cached)} cached (use --rescan to rebuild)", flush=True)
        return cached
    files = ensure_metadata(paths, label_source=args.labels)
    vocabulary, missing = resolve_vocabulary(
        read_csv_rows(files["classes"]),
        [*PERSONAL_LIFE_VOCABULARY, PERSON_DISPLAY_NAME],
    )
    if missing:
        print(f"  vocabulary: {len(missing)} names did not resolve: {', '.join(missing)}", flush=True)
    person_mid = vocabulary.pop(PERSON_DISPLAY_NAME, "")
    if not person_mid:
        raise SystemExit("the Person class did not resolve -- refusing to build an unfiltered pool")
    hits = scan_labels(
        files["labels"],
        personal_mids=frozenset(vocabulary.values()),
        person_mid=person_mid,
        confidence=args.confidence,
    )
    candidates = scan_licences(files["images"], hits)
    paths.candidates.parent.mkdir(parents=True, exist_ok=True)
    paths.candidates.write_text(json.dumps(candidates), encoding="utf-8")
    return candidates


def already_done(paths: CorpusPaths) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Replay the append-only log: what downloaded, and what the mirror does not hold."""
    kept: dict[str, dict[str, Any]] = {}
    missing: set[str] = set()
    for row in read_jsonl(paths.downloads_log):
        image_id = str(row.get("image_id") or "")
        if not image_id:
            continue
        if row.get("status") == "ok":
            kept[image_id] = row
        elif row.get("status") == "absent":
            missing.add(image_id)
    return kept, missing


def download_one(
    client: httpx.Client,
    image_id: str,
    meta: dict[str, str],
    paths: CorpusPaths,
) -> dict[str, Any]:
    url = CVDF_IMAGE_URL.format(split=paths.split, image_id=image_id)
    target = paths.images / f"{image_id}.jpg"
    if not target.exists():
        response = client.get(url, timeout=60.0)
        if response.status_code == 404:
            # §4.1: the mirror covers ~18% of train ids. An absent id is normal,
            # is logged once, and is never retried on resume.
            return {"image_id": image_id, "status": "absent"}
        response.raise_for_status()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
    return {
        "image_id": image_id,
        "status": "ok",
        "split": paths.split,
        "s3_url": url,
        "local_path": str(target),
        "license_name": LICENSE_NAME,
        "license_url": meta.get("license_url") or CC_BY_2_0,
        "author": meta.get("author", ""),
        "author_profile_url": meta.get("author_profile_url", ""),
        "original_landing_url": meta.get("original_landing_url", ""),
        "title": meta.get("title", ""),
        # §4.1: CC grants are irrevocable, but proving the grant was live at
        # retrieval is the downloader's problem. Timestamp + hash is that proof.
        "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "content_sha256": sha256_file(target),
        "bytes": target.stat().st_size,
    }


def run_downloads(
    paths: CorpusPaths, candidates: dict[str, dict[str, str]], args: argparse.Namespace
) -> list[dict[str, Any]]:
    kept, absent = already_done(paths)
    order = deterministic_order(candidates, seed=args.seed)
    print(f"corpus: {len(order)} candidates, {len(kept)} already local, target {args.count}", flush=True)
    started = time.monotonic()
    with httpx.Client(follow_redirects=True) as client:
        for image_id in order:
            if len(kept) >= args.count:
                break
            if image_id in kept or image_id in absent:
                continue
            try:
                record = download_one(client, image_id, candidates[image_id], paths)
            except httpx.HTTPError as error:
                print(f"  {image_id}: {type(error).__name__}, retry on next run", flush=True)
                continue
            append_jsonl(paths.downloads_log, record)
            if record["status"] == "ok":
                kept[image_id] = record
                done = len(kept)
                if done % 50 == 0 or done == args.count:
                    elapsed = time.monotonic() - started
                    print(f"  images: {done}/{args.count} ({duration_label(elapsed)})", flush=True)
            else:
                absent.add(image_id)
    return [kept[key] for key in sorted(kept)]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--split", choices=("validation", "test", "train"), default="validation")
    parser.add_argument("--count", type=int, default=5000, help="images to end up with")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--labels", choices=("machine", "human"), default="machine")
    parser.add_argument("--confidence", type=float, default=0.5, help="§4.1 machine-label floor")
    parser.add_argument("--rescan", action="store_true", help="rebuild the cached candidate pool")
    parser.add_argument("--candidates-only", action="store_true", help="stop before downloading")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = CorpusPaths(root=args.root, split=args.split)
    candidates = build_candidates(paths, args)
    if not candidates:
        raise SystemExit("no candidates survived the filter -- check --confidence and --labels")
    if args.candidates_only:
        print(f"candidates: {len(candidates)} -> {paths.candidates}")
        return 0
    rows = run_downloads(paths, candidates, args)
    write_parquet(rows, paths.manifest, MANIFEST_COLUMNS)
    print(f"manifest: {len(rows)} rows -> {paths.manifest}", flush=True)
    if len(rows) < args.count:
        print(
            f"NOTE: {len(rows)} of {args.count} requested. The CVDF mirror does not hold "
            "every id; raise --count or add a split to top up.",
            flush=True,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\ninterrupted -- rerun the same command to resume", flush=True)
        raise SystemExit(130)
