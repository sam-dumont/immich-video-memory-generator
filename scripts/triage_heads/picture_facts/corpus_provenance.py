#!/usr/bin/env python3
"""Where every training picture came from, and under what licence.

Reads the corpus the trainer actually used (`heads/corpus-ids.txt`) and reports it against
the Open Images manifest the shipped heads were built from. Every row carries a creator and
a landing page; the photographs are not redistributed here.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

INDEX = Path("~/.immich-memories-distill/oi-index/index.parquet").expanduser()
SHIPPED_PARTITION = Path("~/.immich-memories-distill/oi-v2/images").expanduser()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus-ids", type=Path, required=True)
    parser.add_argument("--topup-manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    used = [line.strip() for line in args.corpus_ids.read_text().splitlines() if line.strip()]
    index = pd.read_parquet(INDEX).set_index("image_id")
    shipped = {p.stem for p in SHIPPED_PARTITION.glob("*.jpg")}
    topup = {
        json.loads(line)["image_id"]: json.loads(line)
        for line in args.topup_manifest.read_text().splitlines()
        if line.strip()
    }

    rows = index.loc[[i for i in used if i in index.index]]
    record = {
        "images_used_for_training_and_public_holdout": len(used),
        "all_in_the_open_images_manifest": int(len(rows)),
        "licences": dict(sorted(Counter(rows["license_url"].tolist()).items())),
        "distinct_creators": int(rows["author"].nunique()),
        "open_images_splits": dict(sorted(Counter(rows["split"].tolist()).items())),
        "sources": [
            {
                "name": "Open Images V7 via the CVDF mirror — the shipped heads' own partition",
                "on_disk": str(SHIPPED_PARTITION),
                "images_used": sum(1 for i in used if i in shipped),
                "licence": "CC BY 2.0 (every row of the manifest)",
                "attribution": "creator name and landing page per image in "
                "~/.immich-memories-distill/oi-index/index.parquet",
                "why": "already downloaded for the shipped six heads; mined for the "
                "sensitivity heads, so it is rich in swimwear and people and thin on screens",
            },
            {
                "name": "Open Images V7 via the CVDF mirror — this experiment's top-up",
                "on_disk": str(args.topup_manifest.parent / "topup"),
                "images_used": sum(1 for i in used if i in topup),
                "licence": "CC BY 2.0 (every row of the manifest)",
                "attribution": "per-image creator, profile and landing page in "
                f"{args.topup_manifest}",
                "why": "the shipped partition has about 200 screen pictures; `screen` and the "
                "screen/document, object, room and body-part arms of `frame_kind` needed more",
                "classes_requested": sorted({row["group"] for row in topup.values()}),
                "bytes_downloaded": sum(
                    p.stat().st_size for p in (args.topup_manifest.parent / "topup").glob("*.jpg")
                ),
            },
        ],
        "not_used": [
            "the owner's library — evaluation only, never a training row",
            "any adult-content evaluation set — nothing here is trained on one",
            "~/.immich-memories-distill/validation/images — the 0.5B describer's own holdout, "
            "left alone so it stays a holdout",
        ],
    }
    args.out.write_text(json.dumps(record, indent=1, sort_keys=True))
    print(json.dumps(record, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
