#!/usr/bin/env python3
"""Bias probe: run a head bundle over FairFace and compare each head's rates across groups.

    uv run python scripts/triage_heads/fairface_probe.py --bundle <bundle.npz>

FairFace (CC BY 4.0, HuggingFaceM4/FairFace, padding 1.25) is 10,954 face crops with
age, gender and race labels. The heads were trained on scenes, so absolute rates on a
face crop mean little; what the probe measures is the DIFFERENTIAL: a head that says
children=yes for 3-9-year-olds of one group far more than another, or flags swim /
private_facility on plain faces of one group more than another, is biased in a way the
owner must know before the head nominates anything. Ratios are max-group / min-group
within the head's decisive rate; anything above RATIO_ALERT is printed as an alert.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

if __name__ == "__main__":  # pragma: no cover - direct script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

FAIRFACE_DIR = Path("~/.immich-memories-distill/fairface").expanduser()
PARQUET = FAIRFACE_DIR / "validation-1.25.parquet"
AGE = ("0-2", "3-9", "10-19", "20-29", "30-39", "40-49", "50-59", "60-69", "70+")
GENDER = ("Male", "Female")
RACE = (
    "East Asian",
    "Indian",
    "Black",
    "White",
    "Middle Eastern",
    "Latino_Hispanic",
    "Southeast Asian",
)
RATIO_ALERT = 1.5


def materialise_images() -> list[dict]:
    """Write the parquet's JPEG bytes to files once (the embedder reads paths)."""
    import pandas as pd

    images = FAIRFACE_DIR / "images"
    images.mkdir(parents=True, exist_ok=True)
    manifest = FAIRFACE_DIR / "manifest.jsonl"
    if manifest.is_file():
        return [
            json.loads(line)
            for line in manifest.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    frame = pd.read_parquet(PARQUET)
    rows = []
    for index, row in enumerate(frame.itertuples(index=False)):
        image_id = f"ff{index:05d}"
        path = images / f"{image_id}.jpg"
        if not path.exists():
            path.write_bytes(row.image["bytes"])
        rows.append(
            {
                "image_id": image_id,
                "age": AGE[int(row.age)],
                "gender": GENDER[int(row.gender)],
                "race": RACE[int(row.race)],
            }
        )
    manifest.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return rows


def embed(rows: list[dict]):
    from scripts.triage_heads.embed import (
        AssetInput,
        EmbeddingCache,
        create_onnx_session,
        embed_assets,
    )
    from scripts.triage_heads.public_probe import (
        DEFAULT_EMBED_META,
        DEFAULT_ONNX,
        owner_encoder_spec,
    )

    spec, staging_key = owner_encoder_spec(DEFAULT_EMBED_META)
    cache = EmbeddingCache(FAIRFACE_DIR / "embeddings.db")
    assets = [
        AssetInput(
            asset_id=row["image_id"],
            image_path=FAIRFACE_DIR / "images" / f"{row['image_id']}.jpg",
            source_updated="fairface-val-1.25",
        )
        for row in rows
    ]
    run = embed_assets(
        assets,
        spec=spec,
        cache=cache,
        session=create_onnx_session(DEFAULT_ONNX, "cpu"),
        batch_size=32,
    )
    print(
        f"embed: requested={run.requested} computed={run.computed} cached={run.cached} elapsed={run.elapsed_seconds:.0f}s"
    )
    return cache, staging_key


def rate_table(
    rows: list[dict], predicted: dict[str, str], *, head: str, positive: str, subset
) -> None:
    """Rate of `positive` per race (and per gender) within `subset`; alert on the spread."""
    by_group: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        if not subset(row):
            continue
        label = predicted[row["image_id"]]
        by_group[row["race"]][label == positive] += 1
        by_group[f"gender:{row['gender']}"][label == positive] += 1
    rates = {}
    for group, counter in by_group.items():
        n = counter[True] + counter[False]
        rates[group] = (counter[True] / n if n else 0.0, n)
    race_rates = {g: r for g, (r, n) in rates.items() if not g.startswith("gender:") and n >= 30}
    line = ", ".join(f"{g} {r:.2f}" for g, r in sorted(race_rates.items(), key=lambda kv: -kv[1]))
    genders = ", ".join(
        f"{g[7:]} {r:.2f}" for g, (r, n) in rates.items() if g.startswith("gender:")
    )
    lo, hi = min(race_rates.values(), default=0.0), max(race_rates.values(), default=0.0)
    ratio = hi / lo if lo > 0 else float("inf") if hi > 0 else 1.0
    alert = "  <-- ALERT" if ratio > RATIO_ALERT and hi >= 0.02 else ""
    print(
        f"  {head}={positive:17s} race: {line}  | gender: {genders}  | spread x{ratio:.1f}{alert}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args(argv)

    import numpy as np

    from immich_memories.triage.heads import HeadBundle

    bundle = HeadBundle.load(args.bundle)
    rows = materialise_images()
    print(f"fairface: {len(rows)} faces; age {dict(Counter(r['age'] for r in rows))}")
    cache, staging_key = embed(rows)
    if bundle.encoder_key != staging_key:
        raise SystemExit("bundle bound to another encoder")
    snapshot = cache.staging_snapshot([row["image_id"] for row in rows], staging_key)
    rows = [row for row in rows if row["image_id"] in snapshot]
    packs = np.stack([snapshot[row["image_id"]][0] for row in rows])
    decided = bundle.decide(packs)
    labels = {
        head: {row["image_id"]: fact.label for row, fact in zip(rows, facts, strict=True)}
        for head, facts in decided.items()
    }
    for head, per_asset in labels.items():
        print(f"\n== {head}: overall {dict(Counter(per_asset.values()).most_common())}")

    child = lambda r: r["age"] in ("0-2", "3-9")  # noqa: E731
    teen = lambda r: r["age"] == "10-19"  # noqa: E731
    adult = lambda r: r["age"] not in ("0-2", "3-9", "10-19")  # noqa: E731
    everyone = lambda _row: True  # noqa: E731
    print("\n== differential rates (race, gender); spread = max/min race rate")
    if "children" in labels:
        rate_table(rows, labels["children"], head="children", positive="yes", subset=child)
        rate_table(rows, labels["children"], head="children[10-19]", positive="yes", subset=teen)
        rate_table(rows, labels["children"], head="children[adult]", positive="yes", subset=adult)
        rate_table(rows, labels["children"], head="children[adult]", positive="no", subset=adult)
    if "people" in labels:
        rate_table(rows, labels["people"], head="people", positive="one", subset=everyone)
        rate_table(rows, labels["people"], head="people", positive="none", subset=everyone)
    if "swim" in labels:
        rate_table(rows, labels["swim"], head="swim", positive="yes", subset=everyone)
        rate_table(rows, labels["swim"], head="swim[adult]", positive="yes", subset=adult)
        rate_table(rows, labels["swim"], head="swim[child]", positive="yes", subset=child)
    if "venue" in labels:
        for venue in ("private_facility", "bedroom", "medical"):
            rate_table(rows, labels["venue"], head="venue", positive=venue, subset=everyone)
    if "activity" in labels:
        for activity in ("posing", "working", "sport-active"):
            rate_table(rows, labels["activity"], head="activity", positive=activity, subset=adult)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
