#!/usr/bin/env python3
"""Stage B -- label the corpus with the production teacher (Qwen3.8-27B on omlx).

The student has to learn the schema the app actually ships, so the request is
built from the tree's own constants (``HEDGED_CARD_SHAPE`` /
``selection_descriptions._PROMPT``) read at run time, never from a copy pasted
into this file. If a field lands in the card upstream, the next run teaches it.

Three things happen to every answer before it is banked: proper nouns are
scrubbed (§8 -- the only mitigation that addresses the real leak), the JSON
envelope is validated, and the row is appended to a write-ahead log so a killed
overnight run resumes without repeating a single model call.

    uv run --with pyarrow scripts/distill/teacher_label.py --split validation --concurrency 2
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import io
import json
import random
import re
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from distill_common import (  # noqa: E402
    DEFAULT_ROOT,
    append_jsonl,
    duration_label,
    load_llm_endpoint,
    production_prompt_constants,
    read_jsonl,
    read_parquet,
    write_parquet,
)

# docs/research §6 names Qwen3.8-27B as the teacher: Apache-2.0, and §9.1
# verified it carries no distillation or output clause. Do not silently fall
# back to whatever the omlx server happens to have loaded.
TEACHER_MODEL = "scottlowry/Qwen3.8-27B-oQ4e-mtp"
# §6: resolution is 3-4x the run's wall clock. 512px long edge is the lever.
TEACHER_LONG_EDGE = 512
CANARY_COUNT = 20
# §8: |R| = 10^6 gives the extraction threshold of ~20 the exposure gate uses.
CANARY_DIGITS = 6
# §8: inject at 1x, 5x, 20x, 100x; the gate is on the 1x and 5x groups.
CANARY_REPEATS = (1, 5, 20, 100)

LABEL_COLUMNS = (
    "image_id",
    "task",
    "model",
    "prompt_sha256",
    "text",
    "setting",
    "raw_json",
    "status",
    "error",
    "is_canary",
    "canary_secret",
    "canary_repeat",
    "redactions",
    "latency_s",
    "labeled_at",
)

# Kept deliberately small. Every word here is a place-generic noun or a calendar
# term that the scrub would otherwise eat out of a perfectly good description.
SCRUB_ALLOWLIST = frozenset(
    {
        "a", "an", "and", "at", "beach", "bridge", "cafe", "castle", "cathedral",
        "chapel", "church", "city", "coast", "college", "court", "december",
        "east", "falls", "farm", "february", "friday", "garden", "gate", "hall",
        "harbour", "harbor", "hill", "hospital", "hotel", "house", "i", "in",
        "island", "january", "july", "june", "lake", "library", "march", "market",
        "may", "monday", "mosque", "mountain", "museum", "north", "november",
        "october", "of", "on", "palace", "park", "plaza", "pool", "restaurant",
        "river", "road", "saturday", "school", "sea", "september", "square",
        "stadium", "station", "street", "sunday", "temple", "the", "theatre",
        "theater", "thursday", "tower", "town", "tuesday", "university", "valley",
        "village", "wednesday", "west", "south", "zoo",
    }
)
_WORD = re.compile(r"\b[A-Z][a-z'’-]{1,}\b")
_SENTENCE_START = re.compile(r"(?:^|[.!?;:]\s+|[\[{,]\s*|\"\s*)$")
REDACTION = "[name]"


@dataclass(frozen=True)
class LabelPaths:
    root: Path
    split: str

    @property
    def split_dir(self) -> Path:
        return self.root / self.split

    @property
    def manifest(self) -> Path:
        return self.split_dir / "manifest.parquet"

    @property
    def wal(self) -> Path:
        return self.split_dir / "labels.jsonl"

    @property
    def labels(self) -> Path:
        return self.split_dir / "labels.parquet"


def scrub_proper_nouns(text: str) -> tuple[str, int]:
    """Redact mid-sentence capitalised tokens (§8: strip names before training).

    Deliberately crude, and its limits are the reason §7 gate 4 exists rather
    than trusting this alone. It cannot see a lowercase name, it keeps a name
    that opens a sentence (where a capital is uninformative), and it will redact
    a legitimate mid-sentence proper noun such as a brand. Over-redaction is the
    cheap direction: the student loses a word, not a person's privacy.
    """
    redactions = 0
    out: list[str] = []
    cursor = 0
    for match in _WORD.finditer(text):
        prefix = text[cursor : match.start()]
        out.append(prefix)
        token = match.group(0)
        preceding = "".join(out)
        sentence_initial = bool(_SENTENCE_START.search(preceding)) or not preceding.strip()
        if sentence_initial or token.casefold() in SCRUB_ALLOWLIST:
            out.append(token)
        else:
            out.append(REDACTION)
            redactions += 1
        cursor = match.end()
    out.append(text[cursor:])
    return "".join(out), redactions


def build_card_prompt(constants: dict[str, Any]) -> str:
    """The production card request, re-framed for one standalone photograph.

    The instruction body, the exact key shape and the hedge sentence all come
    from the tree. Only the moment framing is dropped -- an Open Images photo is
    one visual, not a production-grouped moment.
    """
    shape = json.dumps(constants["card_shape"], separators=(",", ":"))
    return f"""Build one compact factual card from the attached visual.

Inspect the whole visual. Preserve every distinct visible subject, action, object, setting, and
readable consequential detail. Do not rank, score, select, interpret significance, infer
relationships, or invent context. Never write a personal name, a street name, a house number, or a
school name; describe people by what is visible. Use one line without double quotes or backslashes.

Return only one complete JSON object with exactly these keys:
{shape}
{constants["card_sentence"]}
The schema_version value must be exactly {constants["card_schema"]}; \
do not shorten or paraphrase it."""


def build_prompt(task: str, constants: dict[str, Any]) -> str:
    if task == "card":
        return build_card_prompt(constants)
    return constants["description_prompt"]


def encode_image(path: Path, *, long_edge: int) -> str:
    """Downscale to the §6 long edge and hand the model a JPEG data URL."""
    with Image.open(path) as image:
        image = image.convert("RGB")
        scale = long_edge / max(image.size)
        if scale < 1.0:
            size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
            image = image.resize(size, Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=90)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def mint_canaries(image_ids: list[str], *, seed: int, count: int = CANARY_COUNT) -> list[dict]:
    """Secret-sharer canaries (§8): one 6-digit secret each, at four repeat rates."""
    rng = random.Random(f"canary:{seed}")
    chosen = rng.sample(image_ids, min(count, len(image_ids)))
    rows = []
    for index, image_id in enumerate(chosen):
        secret = "".join(str(rng.randint(0, 9)) for _ in range(CANARY_DIGITS))
        rows.append(
            {
                "image_id": image_id,
                "secret": secret,
                "repeat": CANARY_REPEATS[index % len(CANARY_REPEATS)],
                "text": f"the reference number for this photograph is {secret}",
            }
        )
    return rows


def canary_row(canary: dict, *, task: str, model: str, schema: str) -> dict[str, Any]:
    payload = {"schema_version": schema, "description": canary["text"], "setting": "insufficient evidence"}
    return {
        "image_id": canary["image_id"],
        "task": task,
        "model": model,
        "prompt_sha256": "",
        "text": canary["text"],
        "setting": "",
        "raw_json": json.dumps(payload, separators=(",", ":")),
        "status": "canary",
        "error": "",
        "is_canary": True,
        "canary_secret": canary["secret"],
        "canary_repeat": canary["repeat"],
        "redactions": 0,
        "latency_s": 0.0,
        "labeled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def read_answer(raw: str, constants: dict[str, Any], task: str) -> tuple[str, str]:
    """Pull the text and the setting cell out of the model's JSON envelope."""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in answer")
    payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("answer is not a JSON object")
    key = "summary" if task == "card" else "description"
    text = payload.get(key)
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"answer has no usable {key}")
    limit = constants["card_max_chars"] if task == "card" else constants["description_max_chars"]
    setting = payload.get("setting")
    setting_text = setting.strip()[: constants["setting_max_chars"]] if isinstance(setting, str) else ""
    return text.strip()[:limit], setting_text


async def preflight(client: httpx.AsyncClient, endpoint: Any) -> None:
    """Refuse to spend a night labelling with the wrong model loaded."""
    try:
        response = await client.get(
            endpoint.models_url,
            headers={"Authorization": f"Bearer {endpoint.api_key}"},
            timeout=20.0,
        )
        served = [row.get("id", "") for row in (response.json().get("data") or [])]
    except (httpx.HTTPError, ValueError, AttributeError) as error:
        print(f"preflight: could not list models ({type(error).__name__}); continuing", flush=True)
        return
    if served and endpoint.model not in served:
        print(
            f"\nSTOP: omlx is serving {served}, not {endpoint.model}.\n"
            "Load the 27B teacher (or pass --model to accept what is served) before labelling.\n",
            flush=True,
        )
        raise SystemExit(2)
    print(f"preflight: {endpoint.model} is served", flush=True)


async def label_one(
    client: httpx.AsyncClient,
    endpoint: Any,
    row: dict[str, Any],
    *,
    prompt: str,
    constants: dict[str, Any],
    task: str,
    max_tokens: int,
) -> dict[str, Any]:
    started = time.monotonic()
    base = {
        "image_id": row["image_id"],
        "task": task,
        "model": endpoint.model,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "is_canary": False,
        "canary_secret": "",
        "canary_repeat": 0,
        "labeled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        encoded = await asyncio.to_thread(
            encode_image, Path(row["local_path"]), long_edge=TEACHER_LONG_EDGE
        )
        response = await client.post(
            endpoint.chat_url,
            headers={"Authorization": f"Bearer {endpoint.api_key}"},
            json={
                "model": endpoint.model,
                "temperature": 0.0,
                "max_tokens": max_tokens,
                # The 27B's chat template reasons by default; omitting this does
                # not disable it, it just truncates mid-thought at this budget.
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            },
            timeout=300.0,
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        text, setting = read_answer(raw, constants, task)
        scrubbed, redactions = scrub_proper_nouns(text)
        scrubbed_setting, setting_redactions = scrub_proper_nouns(setting)
        return {
            **base,
            "text": scrubbed,
            "setting": scrubbed_setting,
            "raw_json": raw[:4000],
            "status": "ok",
            "error": "",
            "redactions": redactions + setting_redactions,
            "latency_s": round(time.monotonic() - started, 2),
        }
    except (httpx.HTTPError, KeyError, ValueError, OSError) as error:
        return {
            **base,
            "text": "",
            "setting": "",
            "raw_json": "",
            "status": "error",
            "error": f"{type(error).__name__}: {error}"[:400],
            "redactions": 0,
            "latency_s": round(time.monotonic() - started, 2),
        }


def pending_rows(manifest: list[dict], done: set[str]) -> list[dict]:
    return [row for row in manifest if row["image_id"] not in done]


def banked(paths: LabelPaths) -> tuple[set[str], list[dict]]:
    rows = list(read_jsonl(paths.wal))
    done = {str(row.get("image_id")) for row in rows if row.get("status") in {"ok", "canary"}}
    return done, rows


async def run(args: argparse.Namespace) -> int:
    paths = LabelPaths(root=args.root, split=args.split)
    if not paths.manifest.exists():
        raise SystemExit(f"no manifest at {paths.manifest} -- run pull_corpus.py first")
    constants = production_prompt_constants()
    prompt = build_prompt(args.task, constants)
    print(f"prompt: {args.task}, {len(prompt)} chars, keys {sorted(constants['card_shape'])}", flush=True)
    endpoint = load_llm_endpoint(base_url=args.base_url, model=args.model)
    manifest = read_parquet(paths.manifest)
    done, existing = banked(paths)

    if args.canaries and not any(row.get("is_canary") for row in existing):
        minted = mint_canaries(
            [row["image_id"] for row in manifest], seed=args.seed, count=args.canary_count
        )
        # Each canary is repeated by its injection rate at assembly time, so the
        # block costs sum(repeats) samples, not len(minted). At the default 20 that
        # is 630 -- 3% of a 20k run but 17% of a 3k pilot. Say so before it lands.
        cost = sum(row["repeat"] for row in minted)
        print(
            f"canaries: {len(minted)} minted at repeats {CANARY_REPEATS}; "
            f"they will expand to {cost} training samples "
            f"({cost / max(1, cost + len(manifest)):.0%} of the mix)",
            flush=True,
        )
        for canary in minted:
            append_jsonl(
                paths.wal,
                canary_row(canary, task=args.task, model=endpoint.model,
                           schema=constants["description_schema"]),
            )
        done, existing = banked(paths)

    todo = pending_rows(manifest, done)[: args.limit] if args.limit else pending_rows(manifest, done)
    print(f"labels: {len(done)} banked, {len(todo)} to go, concurrency {args.concurrency}", flush=True)
    if not todo:
        write_parquet(existing, paths.labels, LABEL_COLUMNS)
        print(f"labels: {len(existing)} rows -> {paths.labels}", flush=True)
        return 0

    stop = asyncio.Event()
    with contextlib.suppress(NotImplementedError):
        asyncio.get_running_loop().add_signal_handler(signal.SIGINT, stop.set)
    semaphore = asyncio.Semaphore(args.concurrency)
    lock = asyncio.Lock()
    counter = {"done": 0, "errors": 0}
    started = time.monotonic()

    async with httpx.AsyncClient() as client:
        if not args.skip_preflight:
            await preflight(client, endpoint)

        async def worker(row: dict) -> None:
            if stop.is_set():
                return
            async with semaphore:
                if stop.is_set():
                    return
                record = await label_one(
                    client, endpoint, row, prompt=prompt, constants=constants,
                    task=args.task, max_tokens=args.max_tokens,
                )
            async with lock:
                append_jsonl(paths.wal, record)
                counter["done"] += 1
                counter["errors"] += record["status"] == "error"
                if counter["done"] % 50 == 0 or counter["done"] == len(todo):
                    elapsed = time.monotonic() - started
                    rate = elapsed / max(1, counter["done"])
                    eta = rate * (len(todo) - counter["done"])
                    print(
                        f"  {counter['done']}/{len(todo)} labelled, "
                        f"{counter['errors']} errors, {rate:.1f}s/img, "
                        f"elapsed {duration_label(elapsed)}, ETA {duration_label(eta)}",
                        flush=True,
                    )

        await asyncio.gather(*(worker(row) for row in todo))

    _, final = banked(paths)
    write_parquet(final, paths.labels, LABEL_COLUMNS)
    print(f"labels: {len(final)} rows -> {paths.labels}", flush=True)
    if stop.is_set():
        print("interrupted cleanly -- rerun the same command to resume", flush=True)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--split", default="validation")
    parser.add_argument("--task", choices=("card", "description"), default="description")
    parser.add_argument("--model", default=TEACHER_MODEL)
    parser.add_argument("--base-url", default=None, help="default: llm.base_url from the app config")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--max-tokens", type=int, default=600)
    parser.add_argument("--limit", type=int, default=0, help="stop after N new labels (smoke runs)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-canaries", dest="canaries", action="store_false")
    parser.add_argument("--canary-count", type=int, default=CANARY_COUNT,
                        help="§8 canaries; each expands by its repeat rate (20 -> 630 samples)")
    parser.add_argument("--skip-preflight", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":  # pragma: no cover - CLI entry
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\ninterrupted -- rerun the same command to resume", flush=True)
        raise SystemExit(130)
