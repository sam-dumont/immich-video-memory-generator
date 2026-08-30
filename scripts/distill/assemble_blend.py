#!/usr/bin/env python3
"""Stage C -- blend teacher JSON with human captions and emit the SFT dataset.

docs/research §3.1 is the whole reason this stage exists: Recap-DataComp's
mixing ablation collapsed ImageNet zero-shot from 69.7 to 36.0 on an
all-synthetic mix, and peaked at 50/50. Localized Narratives is the free human
half -- CC BY 4.0, written by people, already annotated on these exact Open
Images photographs.

    uv run --with pyarrow scripts/distill/assemble_blend.py --split validation --human-ratio 0.5
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from distill_common import (  # noqa: E402
    DEFAULT_ROOT,
    NARRATIVE_URLS,
    duration_label,
    production_prompt_constants,
    read_jsonl,
    read_parquet,
    write_parquet,
)
from pull_corpus import fetch_to  # noqa: E402

NARRATIVE_LICENSE = "CC BY 4.0"
NARRATIVE_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
SOURCE_COLUMNS = (
    "image_id",
    "source_url",
    "landing_url",
    "creator",
    "creator_url",
    "license_name",
    "license_url",
    "retrieved_at",
    "content_sha256",
    "supervision",
    "supervision_license",
)

# The chat template this emits, stated exactly so a trainer mismatch is visible
# rather than silent. Qwen3-VL's processor consumes an OpenAI-shaped `messages`
# list whose user content is a list of typed parts; `images` repeats the paths
# in order for trainers that want them hoisted (mlx-vlm, axolotl).
TEMPLATE_NOTE = """messages[0].role = "user", content = [{"type":"image","image":<abs path>},
{"type":"text","text":<prompt>}]; messages[1].role = "assistant",
content = [{"type":"text","text":<target>}]; plus a top-level "images": [<abs path>]."""


@dataclass(frozen=True)
class BlendPaths:
    root: Path
    split: str

    @property
    def split_dir(self) -> Path:
        return self.root / self.split

    @property
    def manifest(self) -> Path:
        return self.split_dir / "manifest.parquet"

    @property
    def labels(self) -> Path:
        return self.split_dir / "labels.parquet"

    @property
    def narratives(self) -> Path:
        return self.root / "metadata" / f"{self.split}-localized-narratives-captions.jsonl"

    @property
    def dataset(self) -> Path:
        return self.split_dir / "dataset"


def load_narratives(paths: BlendPaths, wanted: set[str]) -> dict[str, str]:
    """Index the captions-only Localized Narratives shard by image id."""
    if paths.split not in NARRATIVE_URLS:
        raise SystemExit(f"no Localized Narratives published for split {paths.split}")
    fetch_to(NARRATIVE_URLS[paths.split], paths.narratives, label="localized-narratives")
    captions: dict[str, str] = {}
    for row in read_jsonl(paths.narratives):
        image_id = str(row.get("image_id") or "")
        # One image can carry several annotators' narratives; the first is enough
        # and keeps the human half one-caption-per-image like the teacher half.
        if image_id in wanted and image_id not in captions:
            caption = str(row.get("caption") or "").strip()
            if caption:
                captions[image_id] = caption
    print(f"narratives: {len(captions)} of {len(wanted)} manifest images have a human caption")
    return captions


def teacher_target(row: dict[str, Any], constants: dict[str, Any], task: str) -> str:
    """Rebuild the exact JSON envelope the student must learn to emit."""
    if task == "card":
        payload = {"schema_version": constants["card_schema"]}
        for key in constants["card_shape"]:
            if key == "schema_version":
                continue
            payload[key] = row["text"] if key == "summary" else (
                row.get("setting") or constants["setting_hedge"] if key == "setting"
                else constants["setting_hedge"]
            )
    else:
        payload = {
            "schema_version": constants["description_schema"],
            "description": row["text"],
            "setting": row.get("setting") or constants["setting_hedge"],
        }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def human_target(caption: str, constants: dict[str, Any], task: str) -> str:
    """Wrap the human caption in the same envelope; the schema is what we teach."""
    key = "summary" if task == "card" else "description"
    schema = constants["card_schema"] if task == "card" else constants["description_schema"]
    payload = {"schema_version": schema, key: caption, "setting": constants["setting_hedge"]}
    if task == "card":
        for extra in constants["card_shape"]:
            payload.setdefault(extra, constants["setting_hedge"])
        payload["schema_version"] = schema
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def sample_record(
    image_path: str, prompt: str, target: str, *, style: str
) -> dict[str, Any]:
    if style == "sharegpt":
        return {
            "messages": [
                {"role": "user", "content": f"<image>{prompt}"},
                {"role": "assistant", "content": target},
            ],
            "images": [image_path],
        }
    return {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": image_path},
                    {"type": "text", "text": prompt},
                ],
            },
            {"role": "assistant", "content": [{"type": "text", "text": target}]},
        ],
        "images": [image_path],
    }


def build_samples(
    manifest: dict[str, dict],
    labels: list[dict],
    captions: dict[str, str],
    constants: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[list[dict], dict[str, int]]:
    """Per-image coin flip at ``--human-ratio`` -- §3.1's p, peaking at 0.5."""
    rng = random.Random(f"blend:{args.seed}")
    prompt = constants["description_prompt"] if args.task == "description" else None
    samples: list[dict] = []
    tally = {"teacher": 0, "human": 0, "canary": 0, "skipped": 0}
    for row in labels:
        image_id = str(row.get("image_id"))
        record = manifest.get(image_id)
        if record is None:
            tally["skipped"] += 1
            continue
        path = record["local_path"]
        request = prompt or args.card_prompt
        if row.get("is_canary"):
            target = human_target(row["text"], constants, args.task)
            for _ in range(int(row.get("canary_repeat") or 1)):
                samples.append(
                    {**sample_record(path, request, target, style=args.format),
                     "_source": "canary", "_image_id": image_id}
                )
                tally["canary"] += 1
            continue
        if row.get("status") != "ok" or not row.get("text"):
            tally["skipped"] += 1
            continue
        caption = captions.get(image_id)
        use_human = caption is not None and rng.random() < args.human_ratio
        if use_human:
            target, origin = human_target(caption, constants, args.task), "human"
        else:
            target, origin = teacher_target(row, constants, args.task), "teacher"
        tally[origin] += 1
        samples.append(
            {**sample_record(path, request, target, style=args.format),
             "_source": origin, "_image_id": image_id}
        )
    return samples, tally


def split_samples(
    samples: list[dict], *, holdout: int, seed: int
) -> tuple[list[dict], list[dict]]:
    """Carve the §7 held-out set first (300-500, paired across successive fine-tunes).

    Canary repeats must never straddle the split -- a canary in the eval set is
    not a canary. They are pinned to train.
    """
    rng = random.Random(f"split:{seed}")
    canaries = [one for one in samples if one["_source"] == "canary"]
    rest = [one for one in samples if one["_source"] != "canary"]
    rng.shuffle(rest)
    return rest[holdout:] + canaries, rest[:holdout]


def strip_private(sample: dict) -> dict:
    return {key: value for key, value in sample.items() if not key.startswith("_")}


def write_split(path: Path, samples: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(strip_private(sample), ensure_ascii=False) + "\n")


def build_sources(manifest: dict[str, dict], captions: dict[str, str]) -> list[dict]:
    """§10 / §9.3 rule 2 -- the creator and licence columns ship with the dataset."""
    rows = []
    for image_id, record in sorted(manifest.items()):
        has_human = image_id in captions
        rows.append(
            {
                "image_id": image_id,
                "source_url": record.get("s3_url", ""),
                "landing_url": record.get("original_landing_url", ""),
                "creator": record.get("author", ""),
                "creator_url": record.get("author_profile_url", ""),
                "license_name": record.get("license_name", ""),
                "license_url": record.get("license_url", ""),
                "retrieved_at": record.get("retrieved_at", ""),
                "content_sha256": record.get("content_sha256", ""),
                "supervision": "teacher+human" if has_human else "teacher",
                "supervision_license": NARRATIVE_LICENSE if has_human else "",
            }
        )
    return rows


def dataset_card(args: argparse.Namespace, tally: dict[str, int], counts: dict[str, int]) -> str:
    return f"""# Card-model distillation set ({args.split})

Built {time.strftime("%Y-%m-%d")} by `scripts/distill/assemble_blend.py`.

| Split | Samples |
|---|---|
| train | {counts["train"]} |
| holdout (val) | {counts["holdout"]} |

| Supervision | Samples | Source | Licence |
|---|---|---|---|
| Teacher JSON | {tally["teacher"]} | Qwen3.8-27B, temp 0, proper nouns scrubbed | Apache-2.0 model, no output clause (§9.1) |
| Human caption | {tally["human"]} | Localized Narratives (captions-only) | {NARRATIVE_LICENSE} |
| Canary | {tally["canary"]} | synthetic, §8 secret-sharer | n/a |
| Skipped | {tally["skipped"]} | no usable label or image | — |

Human ratio requested {args.human_ratio} (§3.1 peak is 0.5); realised
{tally["human"] / max(1, tally["human"] + tally["teacher"]):.3f} over the images that had
a human caption available.

**Images**: Open Images V7, pulled from the CVDF mirror `s3://open-images-dataset`, never from
Flickr. Every row is plain CC BY 2.0 with a named author; NC, ND and SA are excluded by policy
(§4.3), as are institutional/archival authors.

**Attribution**: `sources.parquet` carries per-image source URL, creator, creator URL, licence
name, licence URL, retrieval timestamp and content hash. Do not publish this dataset with those
columns dropped — that is the one act §9.3 puts inside §1202(b)(3).

**Chat template**: `--format {args.format}`.
{TEMPLATE_NOTE}

**Task**: `{args.task}`. The request text is imported live from the app tree, so it matches the
production prompt at build time rather than a copy.
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--split", default="validation")
    parser.add_argument("--task", choices=("card", "description"), default="description")
    parser.add_argument("--human-ratio", type=float, default=0.5, help="§3.1 p; peak is 0.5")
    parser.add_argument("--holdout", type=int, default=400, help="§7 wants 300-500, paired")
    parser.add_argument("--format", choices=("messages", "sharegpt"), default="messages")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--card-prompt", default="", help="override the card request text")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.monotonic()
    paths = BlendPaths(root=args.root, split=args.split)
    for required in (paths.manifest, paths.labels):
        if not required.exists():
            raise SystemExit(f"missing {required} -- run the earlier stage first")
    constants = production_prompt_constants()
    if args.task == "card" and not args.card_prompt:
        from teacher_label import build_card_prompt

        args.card_prompt = build_card_prompt(constants)
    manifest = {row["image_id"]: row for row in read_parquet(paths.manifest)}
    labels = read_parquet(paths.labels)
    captions = load_narratives(paths, set(manifest))
    samples, tally = build_samples(manifest, labels, captions, constants, args)
    train, holdout = split_samples(samples, holdout=args.holdout, seed=args.seed)
    write_split(paths.dataset / "train.jsonl", train)
    # Named for HF `load_dataset(<dir>)`, which maps a file stem to a split. The
    # trainer's --split train and --steps-per-eval both resolve without renaming.
    write_split(paths.dataset / "validation.jsonl", holdout)
    write_parquet(build_sources(manifest, captions), paths.dataset / "sources.parquet", SOURCE_COLUMNS)
    counts = {"train": len(train), "holdout": len(holdout)}
    (paths.dataset / "dataset_card.md").write_text(
        dataset_card(args, tally, counts), encoding="utf-8"
    )
    print(
        f"dataset: {counts['train']} train + {counts['holdout']} holdout "
        f"({tally['teacher']} teacher / {tally['human']} human / {tally['canary']} canary) "
        f"-> {paths.dataset} in {duration_label(time.monotonic() - started)}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
