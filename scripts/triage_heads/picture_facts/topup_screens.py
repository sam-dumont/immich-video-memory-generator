#!/usr/bin/env python3
"""Top up the public corpus where the shipped Open Images partition is thin.

The on-disk `oi-v2` corpus was mined for the sensitivity heads, so it holds 626 swimwear
images and about 200 screens. `screen` and the screen/document and object arms of
`frame_kind` need more, so this pulls the missing ones straight from the CVDF Open Images
mirror using the manifest the shipped heads already use (`oi-index/index.parquet`, every
row CC BY 2.0, creator and landing URL recorded).

Downloads only; no owner data and no model is touched here.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

CVDF_IMAGE_URL = "https://open-images-dataset.s3.amazonaws.com/{split}/{image_id}.jpg"
INDEX = Path("~/.immich-memories-distill/oi-index/index.parquet").expanduser()

# Open Images v7 human-verified labels, grouped by what the three heads need to see.
WANTED: dict[str, tuple[str, ...]] = {
    "screen": (
        "Computer monitor",
        "Television",
        "Mobile phone",
        "Laptop",
        "Tablet computer",
        "Screenshot",
        "Display device",
        "Computer keyboard",
        "Personal computer hardware",
        "Projection screen",
    ),
    "document": (
        "Poster",
        "Book",
        "Document",
        "Advertising",
        "Billboard",
        "Whiteboard",
        "Newspaper",
        "Text",
        "Menu",
        "Web page",
        "Paper",
        "Envelope",
    ),
    "object": (
        "Furniture",
        "Tableware",
        "Tool",
        "Bottle",
        "Toy",
        "Shoe",
        "Bag",
        "Watch",
        "Musical instrument",
        "Kitchen appliance",
    ),
    "room": ("Room", "Ceiling", "Floor", "Wall", "Interior design", "Staircase", "Door"),
    "bodypart": ("Human hand", "Human foot", "Human eye", "Nail", "Human leg", "Human ear"),
    "scenery": ("Landscape", "Mountain", "Beach", "Lake", "Coast", "Sunset", "Skyline"),
    "cover": (
        "Swimwear",
        "Brassiere",
        "Swimming pool",
        "Barechested",
        "Undergarment",
        "Bathing",
        "Sauna",
        "Chest",
        "Abdomen",
        "Trunks",
    ),
    "child": ("Baby", "Child", "Toddler", "Infant bed", "Boy", "Girl"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--have", nargs="*", type=Path, default=[])
    parser.add_argument("--per-group", type=int, default=380)
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args(argv)

    frame = pd.read_parquet(INDEX)
    have: set[str] = set()
    for directory in args.have:
        have.update(p.stem for p in directory.glob("*.jpg"))
    args.out.mkdir(parents=True, exist_ok=True)
    have.update(p.stem for p in args.out.glob("*.jpg"))

    labels = frame["pos"].fillna("")
    picked: dict[str, dict] = {}
    for group, classes in WANTED.items():
        mask = labels.apply(lambda v, c=set(classes): bool(c & set(v.split("|"))) if v else False)
        pool = frame[mask]
        pool = pool[~pool["image_id"].isin(have)]
        # Spread over creators so one photographer's burst cannot own a class.
        pool = pool.sample(frac=1.0, random_state=42).drop_duplicates("author", keep="first")
        for row in pool.head(args.per_group).to_dict("records"):
            picked.setdefault(str(row["image_id"]), {"group": group, **row})
    print(f"{len(picked)} images to fetch", flush=True)

    manifest = args.out.parent / "topup-manifest.jsonl"
    started, fetched, failed = time.monotonic(), 0, 0

    def fetch(item: tuple[str, dict]) -> tuple[str, dict, str]:
        image_id, row = item
        url = CVDF_IMAGE_URL.format(split=row["split"], image_id=image_id)
        target = args.out / f"{image_id}.jpg"
        try:
            with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
                payload = response.read()
        except Exception as exc:
            return image_id, row, f"{type(exc).__name__}: {exc}"
        target.write_bytes(payload)
        return image_id, row, ""

    with manifest.open("a") as handle, ThreadPoolExecutor(max_workers=args.workers) as pool:
        for image_id, row, error in pool.map(fetch, picked.items()):
            if error:
                failed += 1
                continue
            fetched += 1
            handle.write(
                json.dumps(
                    {
                        "image_id": image_id,
                        "group": row["group"],
                        "split": row["split"],
                        "author": row["author"],
                        "author_profile_url": row["author_profile_url"],
                        "landing_url": row["landing_url"],
                        "license_url": row["license_url"],
                        "s3_url": CVDF_IMAGE_URL.format(split=row["split"], image_id=image_id),
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            if fetched % 250 == 0:
                handle.flush()
                print(f"  {fetched}/{len(picked)}", flush=True)
    print(
        json.dumps(
            {"fetched": fetched, "failed": failed, "seconds": round(time.monotonic() - started, 1)}
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
