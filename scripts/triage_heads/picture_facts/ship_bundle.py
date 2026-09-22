#!/usr/bin/env python3
"""Turn the experiment's eleven-head file into the bundle the package ships.

`export_bundle.py` appends every head the distillation trained. Only three of them earned a
seat, and the `swim` head lost the one it had, so this script writes the shipped subset and
nothing else. Three things happen here that cannot happen in the trainer:

* the two binary heads ship at the band they were measured at, not at their argmax.
  `triage-head-bundle-v1` carries coefficients and nothing else, so the band is baked into
  the `yes` intercept: for a two-class softmax head p(yes) = sigmoid(z_yes - z_no), so
  subtracting logit(band) from the `yes` intercept makes the ordinary argmax say `yes`
  exactly when p(yes) exceeded the band. Without this `uncovered_person` would flag 991 of
  the 3,614 evaluated owner pictures instead of the 544 its published numbers describe, and
  `screen` 146 instead of 37.
* the two binary heads take the product's own `no`/`yes` vocabulary, so a gate reads them the
  way it reads `swim` or `nsfw_marqo`. Class order is unchanged, so no coefficient moves.
* `swim` is dropped. Measured on 3,564 owner pictures it flags 551 where the reference reader
  sees swimwear on 22.

    python ship_bundle.py --source bundle/public-11heads-v4.npz \
        --out src/immich_memories/triage/bundled_heads/public-8heads-v4.npz
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

BUNDLE_SCHEMA = "triage-head-bundle-v1"

# The heads the shipped bundle keeps from the existing public file, in its own order.
KEPT = ("location", "people", "children", "activity", "venue")

# The distilled heads that earned a seat: the version they ship at, the vocabulary they take,
# and the band baked into one class, if any. A band is the probability the class must exceed.
SHIPPED_NEW: dict[str, dict] = {
    "frame_kind": {"version": "public-v1", "rename": {}, "band": None},
    "screen": {
        "version": "public-v1-strict",
        "rename": {"no_screen": "no", "screen": "yes"},
        "band": ("yes", 0.999999),
    },
    "uncovered_person": {
        "version": "public-v1",
        "rename": {"covered": "no", "uncovered": "yes"},
        "band": ("yes", 0.829),
    },
}


def _rename(classes: list[str], mapping: dict[str, str]) -> list[str]:
    renamed = [mapping.get(name, name) for name in classes]
    if len(set(renamed)) != len(renamed):
        raise SystemExit(f"renaming {classes} collapses two classes onto one")
    return renamed


def _bake(intercept: np.ndarray, classes: list[str], band: tuple[str, float]) -> np.ndarray:
    name, probability = band
    if len(classes) != 2:
        raise SystemExit(f"a band only has a meaning on a two-class head, not {classes}")
    baked = intercept.astype(np.float32).copy()
    index = classes.index(name)
    baked[index] = np.float32(baked[index] - math.log(probability / (1.0 - probability)))
    return baked


def build(source: Path) -> tuple[dict[str, np.ndarray], dict]:
    payload = dict(np.load(source, allow_pickle=False))
    meta = json.loads(str(payload.pop("meta")))
    if meta.get("schema") != BUNDLE_SCHEMA:
        raise SystemExit(f"{source}: not a {BUNDLE_SCHEMA} bundle")
    by_name = {entry["name"]: entry for entry in meta["heads"]}
    missing = [name for name in (*KEPT, *SHIPPED_NEW) if name not in by_name]
    if missing:
        raise SystemExit(f"{source} has no {missing}")

    arrays = {"pca_mean": payload["pca_mean"], "pca_components": payload["pca_components"]}
    heads = []
    for name in KEPT:
        arrays[f"{name}__coef"] = payload[f"{name}__coef"]
        arrays[f"{name}__intercept"] = payload[f"{name}__intercept"]
        heads.append(by_name[name])
    for name, recipe in SHIPPED_NEW.items():
        classes = _rename(list(by_name[name]["classes"]), recipe["rename"])
        intercept = payload[f"{name}__intercept"]
        arrays[f"{name}__coef"] = payload[f"{name}__coef"]
        arrays[f"{name}__intercept"] = (
            _bake(intercept, classes, recipe["band"]) if recipe["band"] else intercept
        )
        heads.append({"name": name, "version": recipe["version"], "classes": classes})
    return arrays, {
        "schema": BUNDLE_SCHEMA,
        "encoder_key": meta["encoder_key"],
        "heads": heads,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    arrays, meta = build(args.source)
    arrays["meta"] = np.asarray(json.dumps(meta, sort_keys=True))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("wb") as handle:
        np.savez_compressed(handle, allow_pickle=False, **arrays)
    print(
        json.dumps(
            {
                "out": str(args.out),
                "sha256": hashlib.sha256(args.out.read_bytes()).hexdigest(),
                "bytes": args.out.stat().st_size,
                "encoder_key": meta["encoder_key"],
                "heads": [(h["name"], h["version"], h["classes"]) for h in meta["heads"]],
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
