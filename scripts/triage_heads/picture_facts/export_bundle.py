#!/usr/bin/env python3
"""Write a `triage-head-bundle-v1` the product could load, unchanged.

A bundle carries one PCA projection for every head it holds, so new heads can only join the
shipped bundle if they were trained on the shipped bundle's projection. `train_heads.py
--pca-from <shipped bundle>` does that; this script appends the result to a copy of the
shipped file and leaves the six existing heads byte-identical.

    python export_bundle.py --heads heads-shipped-pca/heads.npz \
        --base .../bundled_heads/public-6heads-v3.npz --version pf-v1 \
        --out bundle/public-10heads-v4.npz
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

BUNDLE_SCHEMA = "triage-head-bundle-v1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--heads", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--version", default="pf-v1")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    base = dict(np.load(args.base, allow_pickle=False))
    meta = json.loads(str(base.pop("meta")))
    if meta.get("schema") != BUNDLE_SCHEMA:
        raise SystemExit(f"{args.base}: not a {BUNDLE_SCHEMA} bundle")

    trained = dict(np.load(args.heads, allow_pickle=False))
    for field in ("pca_mean", "pca_components"):
        if not np.array_equal(base[field], trained[field]):
            raise SystemExit(
                "the new heads were not trained on the base bundle's PCA; retrain with "
                "--pca-from or ship them as a second bundle"
            )

    arrays = dict(base)
    names = sorted({k.split("__")[0] for k in trained if "__" in k})
    for name in names:
        if any(entry["name"] == name for entry in meta["heads"]):
            raise SystemExit(f"{name} already exists in the base bundle")
        arrays[f"{name}__coef"] = trained[f"{name}__coef"].astype(np.float32)
        arrays[f"{name}__intercept"] = trained[f"{name}__intercept"].astype(np.float32)
        meta["heads"].append(
            {
                "name": name,
                "version": args.version,
                "classes": [str(c) for c in trained[f"{name}__classes"].tolist()],
            }
        )
    arrays["meta"] = np.asarray(json.dumps(meta, sort_keys=True))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("wb") as handle:
        np.savez_compressed(handle, allow_pickle=False, **arrays)

    digest = hashlib.sha256(args.out.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "out": str(args.out),
                "sha256": digest,
                "bytes": args.out.stat().st_size,
                "encoder_key": meta["encoder_key"],
                "heads": [
                    {"name": h["name"], "version": h["version"], "classes": len(h["classes"])}
                    for h in meta["heads"]
                ],
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
