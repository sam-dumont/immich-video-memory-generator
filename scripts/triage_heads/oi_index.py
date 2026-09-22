#!/usr/bin/env python3
"""Index Open Images' own human-verified labels for the validation and test splits.

    uv run python scripts/triage_heads/oi_index.py build
    uv run python scripts/triage_heads/oi_index.py cells      # count the cells we care about

Why: the public heads for people/children/activity are blocked by corpus composition
(2 % no-person pictures against 40 % at home; no animals, food, screens). Open Images
has human-verified positives AND negatives per image plus person boxes, so a stratified
corpus — and a sensitivity test set (bathroom, sauna, swimwear, bed, hospital, child) —
needs no teacher call at all. Every image here is CC BY 2.0 (checked, not assumed).

Output: ~/.immich-memories-distill/oi-index/index.parquet, one row per image:
  image_id, split, author, author_profile_url, title, landing_url, license_url,
  pos (verified-positive class names, '|'-joined), neg (verified-negative), n_person
  (person-class boxes), n_child (boy/girl boxes), group_of (a Person box marked IsGroupOf)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

if __name__ == "__main__":  # pragma: no cover - direct script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

METADATA = Path("~/.immich-memories-distill/metadata").expanduser()
OUT_DIR = Path("~/.immich-memories-distill/oi-index").expanduser()
CC_BY_2 = "https://creativecommons.org/licenses/by/2.0/"

PERSON_BOX_CLASSES = ("Person", "Man", "Woman", "Boy", "Girl")
CHILD_BOX_CLASSES = ("Boy", "Girl")

# The cells, as Open Images class names. Positives need a verified-positive label of
# the cell; "clean" cells additionally need a verified NEGATIVE of the guarding class.
CELLS: dict[str, tuple[str, ...]] = {
    "child": ("Baby", "Toddler", "Child", "Boy", "Girl"),
    "animal": ("Animal",),
    "food": ("Food",),
    "screen_or_document": ("Screenshot", "Document", "Computer monitor", "Text"),
    "private_facility": (
        "Bathroom",
        "Bathtub",
        "Shower",
        "Sauna",
        "Spa",
        "Toilet",
        "Changing room",
    ),
    "bedroom": ("Bed", "Bedroom"),
    "medical": ("Hospital",),
    "water": ("Beach", "Swimming pool"),
    "swim_or_partial": ("Swimwear", "Bikini", "Lingerie", "Diaper"),
}

SPLITS = {
    "validation": (
        "validation-images-with-rotation.csv",
        "oidv7-val-annotations-human-imagelabels.csv",
        "validation-annotations-bbox.csv",
    ),
    "test": (
        "test-images-with-rotation.csv",
        "oidv7-test-annotations-human-imagelabels.csv",
        "test-annotations-bbox.csv",
    ),
}


def class_names() -> dict[str, str]:
    table = pd.read_csv(
        METADATA / "oidv7-class-descriptions.csv", header=None, names=["mid", "name"]
    )
    return dict(zip(table["mid"], table["name"], strict=True))


def subtree(root: str, names: dict[str, str]) -> tuple[str, ...]:
    """Every boxable class under `root` in OI's 600-class hierarchy (a verified 'Dog' is not a verified 'Animal')."""
    tree = json.loads((METADATA / "bbox_labels_600_hierarchy.json").read_text(encoding="utf-8"))
    found: list[str] = []

    def walk(node: dict, inside: bool) -> None:
        here = inside or names.get(node["LabelName"]) == root
        if here:
            found.append(names[node["LabelName"]])
        for child in node.get("Subcategory", []):
            walk(child, here)

    walk(tree, False)
    return tuple(sorted(set(found)))


def _index_split(split: str, names: dict[str, str]) -> pd.DataFrame:
    images_file, labels_file, boxes_file = SPLITS[split]
    images = pd.read_csv(
        METADATA / images_file,
        usecols=[
            "ImageID",
            "OriginalLandingURL",
            "License",
            "AuthorProfileURL",
            "Author",
            "Title",
        ],
    )
    images = images[images["License"] == CC_BY_2]
    labels = pd.read_csv(METADATA / labels_file, usecols=["ImageID", "LabelName", "Confidence"])
    labels["name"] = labels["LabelName"].map(names)
    labels = labels.dropna(subset=["name"])
    pos = labels[labels["Confidence"] >= 1].groupby("ImageID")["name"].agg("|".join)
    neg = labels[labels["Confidence"] <= 0].groupby("ImageID")["name"].agg("|".join)
    boxes = pd.read_csv(METADATA / boxes_file, usecols=["ImageID", "LabelName", "IsGroupOf"])
    boxes["name"] = boxes["LabelName"].map(names)
    person_boxes = boxes[boxes["name"].isin(PERSON_BOX_CLASSES)]
    # A Person box and a Man box on the same human overlap; count the larger family.
    generic = person_boxes[person_boxes["name"] == "Person"].groupby("ImageID").size()
    specific = person_boxes[person_boxes["name"] != "Person"].groupby("ImageID").size()
    n_person = pd.concat([generic, specific], axis=1).fillna(0).max(axis=1).astype(int)
    n_child = person_boxes[person_boxes["name"].isin(CHILD_BOX_CLASSES)].groupby("ImageID").size()
    group_of = person_boxes[person_boxes["IsGroupOf"] == 1].groupby("ImageID").size()
    out = pd.DataFrame(
        {
            "image_id": images["ImageID"].to_numpy(),
            "split": split,
            "author": images["Author"].to_numpy(),
            "author_profile_url": images["AuthorProfileURL"].to_numpy(),
            "title": images["Title"].to_numpy(),
            "landing_url": images["OriginalLandingURL"].to_numpy(),
            "license_url": images["License"].to_numpy(),
        }
    ).set_index("image_id")
    out["pos"] = pos.reindex(out.index).fillna("")
    out["neg"] = neg.reindex(out.index).fillna("")
    out["n_person"] = n_person.reindex(out.index).fillna(0).astype(int)
    out["n_child"] = n_child.reindex(out.index).fillna(0).astype(int)
    out["group_of"] = group_of.reindex(out.index).fillna(0).astype(int) > 0
    return out.reset_index()


def build() -> pd.DataFrame:
    names = class_names()
    frames = [_index_split(split, names) for split in SPLITS]
    index = pd.concat(frames, ignore_index=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index.to_parquet(OUT_DIR / "index.parquet", index=False)
    return index


def load() -> pd.DataFrame:
    return pd.read_parquet(OUT_DIR / "index.parquet")


def has_any(column: pd.Series, classes: tuple[str, ...]) -> pd.Series:
    wanted = set(classes)
    return column.map(lambda value: bool(wanted & set(value.split("|"))) if value else False)


def cell_masks(index: pd.DataFrame) -> dict[str, pd.Series]:
    """Boolean masks per cell. `no_person` is the verified-negative, box-free kind."""
    names = class_names()
    cells = dict(CELLS)
    cells["animal"] = subtree("Animal", names)
    cells["food"] = subtree("Food", names)
    masks = {name: has_any(index["pos"], classes) for name, classes in cells.items()}
    person_negative = has_any(index["neg"], ("Person",))
    masks["no_person"] = person_negative & (index["n_person"] == 0)
    masks["person_verified"] = has_any(index["pos"], ("Person",)) | (index["n_person"] > 0)
    return masks


def people_bucket(n_person: int, group_of: bool) -> str:  # noqa: FBT001 - a data column
    """The people class an Open Images box count implies, for calibration against the teacher."""
    if group_of or n_person >= 6:
        return "crowd"
    if n_person >= 3:
        return "small-group"
    return {0: "none", 1: "one", 2: "two"}[n_person]


def calibrate(index: pd.DataFrame, labels_path: Path) -> None:
    """Cross-tabulate the teacher's labels on the 3,000 already-labelled images against OI's rules.

    Each table is teacher label (rows) × OI-derived label (columns). Off-diagonal mass is
    either a teacher mistake, an OI annotation gap, or a rule that needs a threshold moved.
    """
    teacher = pd.DataFrame(
        json.loads(line)
        for line in labels_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    teacher = teacher[teacher["status"] == "ok"].drop_duplicates("asset_id", keep="last")
    joined = teacher.merge(index, left_on="asset_id", right_on="image_id", how="inner")
    masks = cell_masks(joined)
    print(f"joined {len(joined)} of {len(teacher)} teacher rows")
    oi_people = [
        people_bucket(int(n), bool(g))
        for n, g in zip(joined["n_person"], joined["group_of"], strict=True)
    ]
    print(
        "\npeople: teacher rows × OI boxes (images with any person box or verified Person negative)"
    )
    has_signal = masks["person_verified"] | masks["no_person"]
    print(
        pd.crosstab(
            joined.loc[has_signal, "people"], pd.Series(oi_people, index=joined.index)[has_signal]
        )
    )
    child_neg = has_any(joined["neg"], CELLS["child"])
    oi_child = pd.Series("unknown", index=joined.index)
    oi_child[child_neg] = "no"
    oi_child[masks["child"]] = "yes"
    print("\nchildren: teacher rows × OI verified child labels")
    print(pd.crosstab(joined["children"], oi_child))
    print("\nactivity of teacher on OI no_person∩animal / no_person∩food / no_person∩screen")
    for cell in ("animal", "food", "screen_or_document"):
        picked = masks["no_person"] & masks[cell]
        print(
            f"  {cell:20s} n={int(picked.sum()):3d}", dict(Counter(joined.loc[picked, "activity"]))
        )
    print("\nsensitivity cells present in the 3,000 (teacher location):")
    for cell in ("private_facility", "bedroom", "medical", "water", "swim_or_partial"):
        picked = masks[cell]
        print(
            f"  {cell:20s} n={int(picked.sum()):3d}", dict(Counter(joined.loc[picked, "location"]))
        )


CORPUS_DIR = Path("~/.immich-memories-distill/oi-v2").expanduser()
CVDF_IMAGE_URL = "https://open-images-dataset.s3.amazonaws.com/{split}/{image_id}.jpg"
AUTHOR_CAP = 3

# Calibration on the 3,000 teacher-labelled images (2026-09-02): Baby/Toddler/Child
# verified-positive → teacher children=yes 369/369; Boy 73 %; Girl 23 % (OI's "Girl" is
# mostly young women). The child cell for training therefore drops Boy and Girl.
CHILD_SURE = ("Baby", "Toddler", "Child")

# (cell name, quota, mask builder). Order matters: an image lands in the first cell it fits.
HEADS_V2_PLAN: tuple[tuple[str, int], ...] = (
    ("no_person_animal", 300),
    ("no_person_food", 300),
    ("no_person_screen", 300),
    ("no_person_other", 300),
    ("child_sure", 500),
    ("person_1", 400),
    ("person_2", 300),
    ("person_3_5", 300),
    ("person_group", 300),
)
SENSITIVITY_V1_PLAN: tuple[tuple[str, int], ...] = (
    ("private_facility", 400),
    ("medical", 100),
    ("swim_or_partial", 750),
    ("bedroom", 400),
    ("water", 400),
    ("negative", 600),
)


def _plan_masks(index: pd.DataFrame) -> dict[str, pd.Series]:
    masks = cell_masks(index)
    no_person = masks["no_person"]
    n = index["n_person"]
    child_sure = has_any(index["pos"], CHILD_SURE)
    sensitive = (
        masks["private_facility"]
        | masks["medical"]
        | masks["swim_or_partial"]
        | masks["bedroom"]
        | masks["water"]
    )
    return {
        "no_person_animal": no_person & masks["animal"],
        "no_person_food": no_person & masks["food"] & ~masks["animal"],
        "no_person_screen": no_person
        & masks["screen_or_document"]
        & ~masks["animal"]
        & ~masks["food"],
        "no_person_other": no_person
        & ~masks["animal"]
        & ~masks["food"]
        & ~masks["screen_or_document"],
        "child_sure": child_sure,
        "person_1": (n == 1) & ~index["group_of"] & ~child_sure,
        "person_2": (n == 2) & ~index["group_of"] & ~child_sure,
        "person_3_5": n.between(3, 5) & ~index["group_of"] & ~child_sure,
        "person_group": (index["group_of"] | (n >= 6)) & ~child_sure,
        "private_facility": masks["private_facility"],
        "medical": masks["medical"],
        "swim_or_partial": masks["swim_or_partial"],
        "bedroom": masks["bedroom"],
        "water": masks["water"],
        "negative": ~sensitive
        & (
            has_any(index["neg"], CELLS["private_facility"])
            | has_any(index["neg"], CELLS["swim_or_partial"])
            | has_any(index["neg"], CELLS["bedroom"])
        ),
    }


def _pick(
    index: pd.DataFrame, mask: pd.Series, quota: int, taken: set[str], seed: int
) -> pd.DataFrame:
    """Random draw under the per-author cap, skipping images already taken anywhere."""
    pool = index[mask & ~index["image_id"].isin(taken)].sample(frac=1.0, random_state=seed)
    per_author: Counter[str] = Counter()
    rows = []
    for row in pool.itertuples(index=False):
        if per_author[row.author] >= AUTHOR_CAP:
            continue
        per_author[row.author] += 1
        rows.append(row)
        if len(rows) >= quota:
            break
    picked = pd.DataFrame(rows)
    taken.update(picked["image_id"].tolist())
    return picked


def select(
    index: pd.DataFrame, *, exclude: set[str], seed: int = 20260902
) -> dict[str, pd.DataFrame]:
    masks = _plan_masks(index)
    taken = set(exclude)
    out: dict[str, pd.DataFrame] = {}
    for name, plan in (("heads-v2", HEADS_V2_PLAN), ("sensitivity-v1", SENSITIVITY_V1_PLAN)):
        parts = []
        for cell, quota in plan:
            picked = _pick(index, masks[cell], quota, taken, seed)
            picked["cell"] = cell
            parts.append(picked)
            print(f"  {name} {cell:20s} {len(picked):4d} / {quota}")
        out[name] = pd.concat(parts, ignore_index=True)
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    for name, frame in out.items():
        frame.to_json(CORPUS_DIR / f"{name}.jsonl", orient="records", lines=True, force_ascii=False)
        print(f"{name}: {len(frame)} rows → {CORPUS_DIR / f'{name}.jsonl'}")
    return out


def fetch(manifests: list[Path], *, workers: int = 16) -> None:
    """Download the selected images from the CVDF mirror into oi-v2/images/, skipping present files."""
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor, as_completed

    images = CORPUS_DIR / "images"
    images.mkdir(parents=True, exist_ok=True)
    wanted = {}
    for manifest in manifests:
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                wanted[row["image_id"]] = row["split"]
    todo = [
        (image_id, split)
        for image_id, split in wanted.items()
        if not (images / f"{image_id}.jpg").exists()
    ]
    print(f"{len(wanted)} wanted, {len(todo)} to fetch")

    def one(image_id: str, split: str) -> tuple[str, str]:
        url = CVDF_IMAGE_URL.format(split=split, image_id=image_id)
        target = images / f"{image_id}.jpg"
        try:
            with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - pinned https host
                payload = response.read()
            if not payload.startswith(b"\xff\xd8"):
                return image_id, "not-jpeg"
            target.with_suffix(".part").write_bytes(payload)
            target.with_suffix(".part").rename(target)
            return image_id, "ok"
        except Exception as exc:  # noqa: BLE001 - one bad fetch must not stop the corpus
            return image_id, f"error: {exc}"[:120]

    outcomes: Counter[str] = Counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, image_id, split) for image_id, split in todo]
        for done, future in enumerate(as_completed(futures), start=1):
            image_id, status = future.result()
            outcomes[status.split(":")[0]] += 1
            if status != "ok":
                print(f"  {image_id}: {status}")
            if done % 250 == 0:
                print(f"  fetched {done}/{len(todo)} {dict(outcomes)}", flush=True)
    print(f"done: {dict(outcomes)}")


def embed() -> None:
    """Pack every fetched v2 image with the owner's pinned encoder into oi-v2/embeddings.db."""
    from scripts.triage_heads.embed import (
        AssetInput,
        EmbeddingCache,
        create_onnx_session,
        embed_assets,
        verify_onnx_artifact,
    )
    from scripts.triage_heads.public_probe import (
        DEFAULT_EMBED_META,
        DEFAULT_ONNX,
        owner_encoder_spec,
    )

    spec, _ = owner_encoder_spec(DEFAULT_EMBED_META)
    if verify_onnx_artifact(DEFAULT_ONNX) != spec.weights_sha256:
        raise ValueError("ONNX weights differ from the owner embed run")
    images = CORPUS_DIR / "images"
    assets = []
    for manifest in present_manifests():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            path = images / f"{row['image_id']}.jpg"
            if path.exists():
                assets.append(
                    AssetInput(asset_id=row["image_id"], image_path=path, source_updated="oi-v2")
                )
    unique = {asset.asset_id: asset for asset in assets}
    session = create_onnx_session(DEFAULT_ONNX, "cpu")
    cache = EmbeddingCache(CORPUS_DIR / "embeddings.db")
    run = embed_assets(
        list(unique.values()), spec=spec, cache=cache, session=session, batch_size=32
    )
    print(
        f"embed done: requested={run.requested} computed={run.computed} cached={run.cached} "
        f"elapsed={run.elapsed_seconds:.0f}s ({run.ms_per_computed_image:.0f} ms/image)"
    )


MANIFESTS = (
    "heads-v2.jsonl",
    "sensitivity-v1.jsonl",
    "hard-negatives-v1.jsonl",
    "portrait-negatives-v1.jsonl",
)
# Confusers the library exposed (2026-09-02: swim=yes on outdoor sport 372× and animals
# 208×; private_facility 79 % outdoor). Images positive for these and free of the cell's
# classes are unverified negatives — a few percent noise, and exactly the missing lesson.
HARD_NEGATIVE_PLAN = {
    "swim": (
        ("Running", 150),
        ("Marathon", 99),
        ("Cycling", 150),
        ("Bicycle", 120),
        ("Athlete", 150),
        ("Sports uniform", 150),
        ("Football", 100),
        ("Skiing", 100),
        ("Hiking", 77),
        ("Dance", 100),
        ("Dog", 150),
        ("Cat", 100),
        ("Horse", 100),
        ("Playground", 100),
        ("Shorts", 150),
    ),
    "venue": (
        ("Mountain", 120),
        ("Snow", 120),
        ("Forest", 100),
        ("Garden", 100),
        ("Street", 100),
        ("Park", 92),
        ("Kitchen", 100),
        ("Living room", 100),
        ("Restaurant", 100),
        ("Sports", 150),
        ("Dog", 100),
        ("Cat", 100),
    ),
}
# Portraits, for BOTH heads at once (FairFace probe v2: swim=yes on 10 % of adult women's
# face crops against 1 % of men's). No negative the heads saw held a face, so face+skin was
# the cheapest swim cue. Man/Woman are drawn in equal number and Boy/Girl keep the child cue
# balanced too; every row must be free of every swim AND venue class.
PORTRAIT_NEGATIVE_PLAN = (
    ("Woman", 400),
    ("Man", 400),
    ("Human face", 200),
    ("Girl", 150),
    ("Boy", 150),
)


def present_manifests() -> list[Path]:
    return [CORPUS_DIR / name for name in MANIFESTS if (CORPUS_DIR / name).is_file()]


def select_hard_negatives(index: pd.DataFrame, *, seed: int = 20260903) -> pd.DataFrame:
    """Unverified negatives for the sensitivity heads: confuser-positive, cell-class-free."""
    taken: set[str] = set()
    for manifest in present_manifests():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if line.strip():
                taken.add(json.loads(line)["image_id"])
    cells = dict(CELLS)
    swim_classes = tuple(cells["swim_or_partial"])
    venue_classes = tuple(
        c for name in ("private_facility", "bedroom", "medical", "water") for c in cells[name]
    )
    parts = []
    for head, plan in HARD_NEGATIVE_PLAN.items():
        forbidden = swim_classes if head == "swim" else venue_classes
        clean = ~has_any(index["pos"], forbidden)
        for confuser, quota in plan:
            mask = clean & has_any(index["pos"], (confuser,))
            picked = _pick(index, mask, quota, taken, seed)
            picked["cell"] = f"hard:{head}:{confuser}"
            parts.append(picked)
            print(f"  hard-negatives {head:5s} {confuser:14s} {len(picked):4d} / {quota}")
    frame = pd.concat(parts, ignore_index=True)
    frame.to_json(
        CORPUS_DIR / "hard-negatives-v1.jsonl", orient="records", lines=True, force_ascii=False
    )
    print(f"hard-negatives-v1: {len(frame)} rows → {CORPUS_DIR / 'hard-negatives-v1.jsonl'}")
    return frame


def select_portrait_negatives(index: pd.DataFrame, *, seed: int = 20260904) -> pd.DataFrame:
    """Gender-balanced portrait negatives for both sensitivity heads: portrait-positive, cell-class-free."""
    taken: set[str] = set()
    for manifest in present_manifests():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if line.strip():
                taken.add(json.loads(line)["image_id"])
    cells = dict(CELLS)
    forbidden = tuple(
        c
        for name in ("private_facility", "bedroom", "medical", "water", "swim_or_partial")
        for c in cells[name]
    )
    clean = ~has_any(index["pos"], forbidden)
    parts = []
    for portrait, quota in PORTRAIT_NEGATIVE_PLAN:
        mask = clean & has_any(index["pos"], (portrait,))
        picked = _pick(index, mask, quota, taken, seed)
        picked["cell"] = f"hard:portrait:{portrait}"
        parts.append(picked)
        print(f"  portrait-negatives {portrait:14s} {len(picked):4d} / {quota}")
    frame = pd.concat(parts, ignore_index=True)
    frame.to_json(
        CORPUS_DIR / "portrait-negatives-v1.jsonl", orient="records", lines=True, force_ascii=False
    )
    print(
        f"portrait-negatives-v1: {len(frame)} rows → {CORPUS_DIR / 'portrait-negatives-v1.jsonl'}"
    )
    return frame


VENUE_PRIORITY = ("medical", "private_facility", "bedroom", "water")
DEFAULT_PUBLIC_BUNDLE = Path(
    "~/.immich-memories-matrix/triage-heads/bundles/public-4heads-v1.npz"
).expanduser()


def _sensitivity_rows() -> list[dict]:
    rows = [
        json.loads(line)
        for manifest in present_manifests()
        if manifest.name != "heads-v2.jsonl"
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    cells = dict(CELLS)
    for row in rows:
        pos = set(row["pos"].split("|")) if row["pos"] else set()
        venue = next((name for name in VENUE_PRIORITY if pos & set(cells[name])), "other")
        row["venue"] = venue
        row["swim"] = "yes" if pos & set(cells["swim_or_partial"]) else "no"
        row["child"] = "yes" if pos & set(CHILD_SURE) else "no"
    return rows


def sensitivity(
    *, bundle_path: Path, out: Path, version: str = "oi-v1", holdout: float = 0.25, seed: int = 7
) -> None:
    """Fit venue + swim linear heads on the public bundle's PCA; report held-out recall/false-flag."""
    import numpy as np
    from scripts.triage_heads.embed import EmbeddingCache
    from scripts.triage_heads.train import fit_linear_candidate

    from immich_memories.triage.heads import HeadBundle, HeadWeights

    bundle = HeadBundle.load(bundle_path)
    rows = _sensitivity_rows()
    cache = EmbeddingCache(CORPUS_DIR / "embeddings.db")
    snapshot = cache.staging_snapshot([row["image_id"] for row in rows], bundle.encoder_key)
    rows = [row for row in rows if row["image_id"] in snapshot]
    packs = np.stack([snapshot[row["image_id"]][0] for row in rows])
    features = bundle.pca.project(packs)
    authors = np.asarray([row["author"] for row in rows])
    rng = np.random.default_rng(seed)
    held_authors = set(
        rng.choice(np.unique(authors), size=int(len(np.unique(authors)) * holdout), replace=False)
    )
    held = np.asarray([author in held_authors for author in authors])
    print(f"sensitivity set: {len(rows)} embedded, {int(held.sum())} held out by author")
    new_heads = []
    for head in ("venue", "swim"):
        labels = np.asarray([row[head] for row in rows])
        linear, _ = fit_linear_candidate(features[~held], labels[~held], authors[~held])
        weights = HeadWeights(
            name=head,
            version=version,
            classes=tuple(linear.classes),
            coef=linear.coef,
            intercept=linear.intercept,
        )
        predicted = np.asarray(weights.classes)[
            weights.probabilities(features[held]).argmax(axis=1)
        ]
        truth = labels[held]
        print(
            f"\n== {head} held-out (n={int(held.sum())}), train counts {dict(Counter(labels[~held].tolist()))}"
        )
        for cls in weights.classes:
            tp = int(((predicted == cls) & (truth == cls)).sum())
            fn = int(((predicted != cls) & (truth == cls)).sum())
            fp = int(((predicted == cls) & (truth != cls)).sum())
            print(
                f"  {cls:17s} recall {tp / (tp + fn):.2f} ({tp}/{tp + fn})  precision {tp / (tp + fp) if tp + fp else 0:.2f}  fp {fp}"
            )
        if head == "venue":
            negatives = truth == "other"
            flagged = predicted != "other"
            print(
                f"  false-flag rate on verified negatives: {int((flagged & negatives).sum())}/{int(negatives.sum())}"
            )
        child = np.asarray([row["child"] for row in rows])[held]
        if head == "swim":
            kid_swim = (truth == "yes") & (child == "yes")
            print(
                f"  child∩swim recall: {int(((predicted == 'yes') & kid_swim).sum())}/{int(kid_swim.sum())}"
            )
        new_heads.append(weights)
    merged = HeadBundle(
        encoder_key=bundle.encoder_key, pca=bundle.pca, heads=bundle.heads + tuple(new_heads)
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    merged.save(out)
    print(f"\nbundle with {[h.name for h in merged.heads]} → {out}")


def label(*, concurrency: int = 2) -> None:
    """The 30B teacher labels heads-v2 (four active heads); WAL at oi-v2/labels.jsonl, resumable."""
    import asyncio
    import hashlib

    import httpx
    from scripts.triage_heads.generate_labels import (
        LabelAsset,
        _resolve_local_endpoint,
        _wal_rows,
        label_assets_multihead,
    )

    rows = [
        json.loads(line)
        for line in (CORPUS_DIR / "heads-v2.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assets = [
        LabelAsset(
            asset_id=row["image_id"],
            image_path=CORPUS_DIR / "images" / f"{row['image_id']}.jpg",
            group_key="author:"
            + hashlib.sha256(str(row["author"]).encode("utf-8")).hexdigest()[:16],
            source_updated="oi-v2",
        )
        for row in rows
        if (CORPUS_DIR / "images" / f"{row['image_id']}.jpg").is_file()
    ]
    wal_path = CORPUS_DIR / "labels.jsonl"
    endpoint = _resolve_local_endpoint()
    print(
        f"label: {len(assets)} images, teacher {endpoint.model} at {endpoint.base_url}", flush=True
    )

    async def run() -> None:
        async with httpx.AsyncClient() as client:
            for attempt in (1, 2):  # the second pass only re-asks banked errors
                run = await label_assets_multihead(
                    assets,
                    endpoint=endpoint,
                    client=client,
                    wal_path=wal_path,
                    concurrency=concurrency,
                )
                print(
                    f"label pass {attempt}: requested={run.requested} labeled={run.labeled} "
                    f"cached={run.cached} errors={run.errors} elapsed={run.elapsed_seconds:.0f}s",
                    flush=True,
                )
                if run.errors == 0:
                    break

    asyncio.run(run())
    ok = sum(1 for row in _wal_rows(wal_path) if row.get("status") == "ok")
    print(f"label done: ok rows={ok}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "command",
        choices=(
            "build",
            "cells",
            "calibrate",
            "select",
            "select-hard",
            "select-portraits",
            "fetch",
            "embed",
            "sensitivity",
            "label",
        ),
    )
    parser.add_argument("--bundle", type=Path, default=DEFAULT_PUBLIC_BUNDLE)
    parser.add_argument(
        "--version", default="oi-v1", help="head version stamped on the sensitivity heads"
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_PUBLIC_BUNDLE.with_name("public-6heads-v1.npz")
    )
    parser.add_argument(
        "--labels",
        type=Path,
        default=Path(
            "~/.immich-memories-matrix/triage-heads/public-probe-v1/public-labels.jsonl"
        ).expanduser(),
    )
    args = parser.parse_args(argv)
    if args.command == "fetch":
        fetch(present_manifests())
        return 0
    if args.command == "embed":
        embed()
        return 0
    if args.command == "sensitivity":
        sensitivity(bundle_path=args.bundle, out=args.out, version=args.version)
        return 0
    if args.command == "label":
        label()
        return 0
    index = build() if args.command == "build" else load()
    if args.command == "calibrate":
        calibrate(index, args.labels)
        return 0
    if args.command == "select-hard":
        select_hard_negatives(index)
        return 0
    if args.command == "select-portraits":
        select_portrait_negatives(index)
        return 0
    if args.command == "select":
        already = {
            json.loads(line)["asset_id"]
            for line in args.labels.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        select(index, exclude=already)
        return 0
    masks = cell_masks(index)
    print(f"images: {len(index)} ({Counter(index['split']).most_common()})")
    for name, mask in masks.items():
        by_split = Counter(index.loc[mask, "split"])
        print(f"  {name:20s} {int(mask.sum()):6d}  {dict(by_split)}")
    print("  no_person ∩ animal", int((masks["no_person"] & masks["animal"]).sum()))
    print("  no_person ∩ food", int((masks["no_person"] & masks["food"]).sum()))
    print("  no_person ∩ screen", int((masks["no_person"] & masks["screen_or_document"]).sum()))
    print("  child ∩ swim", int((masks["child"] & masks["swim_or_partial"]).sum()))
    print("  child ∩ water", int((masks["child"] & masks["water"]).sum()))
    print("  child ∩ private_facility", int((masks["child"] & masks["private_facility"]).sum()))
    print(
        "  swim ∩ private_facility",
        int((masks["swim_or_partial"] & masks["private_facility"]).sum()),
    )
    counts = Counter(index["n_person"].clip(upper=8))
    print("  person boxes:", dict(sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
