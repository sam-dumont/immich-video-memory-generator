#!/usr/bin/env python3
"""Stage D -- the four §7 gates that decide whether the student ships.

Two of the four are corrections to the obvious choice, and both corrections are
implemented here rather than borrowed:

* Donut's canonical ``cal_f1`` pools FP and FN into ``TP/(TP+(FP+FN)/2)`` and
  structurally cannot report a hallucination rate. Gate 2 counts FP separately.
* The widely used ``docext`` KIE metric iterates ground-truth fields only, so a
  hallucinated extra field is invisible to it. Gate 2 divides by *predicted*
  fields, which is the only denominator that sees them.

Gate 2 is only meaningful against a hand-corrected holdout: measured against the
teacher's own labels the teacher's rate is 0 by construction and the comparison
is vacuous. The runbook makes that the human-in-the-loop step it is.

    uv run --with pyarrow scripts/distill/eval_gates.py \\
        --holdout ~/.immich-memories-distill/validation/dataset/validation.jsonl \\
        --predictions student_predictions.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from distill_common import read_jsonl, read_parquet  # noqa: E402

# §7 gate 1: score against the teacher's own self-agreement, not against 100%.
TEACHER_SELF_AGREEMENT = 0.95
# §8: |R| = 10^6, so the secret-sharer extraction threshold is log2(10^6) ~= 19.9.
CANARY_SPACE = 10**6
EXPOSURE_SINGLE_DIGIT = 10.0
GATED_REPEATS = (1, 5)
_PUNCT = re.compile(r"[^\w\s]+")
_SPLIT = re.compile(r"\s*[;,]\s*|\s+and\s+")
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
        "is", "it", "of", "on", "or", "the", "there", "this", "to", "with",
    }
)


@dataclass
class Gate:
    name: str
    passed: bool
    detail: str
    measured: bool = True


@dataclass
class FieldTally:
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    predicted: int = 0
    per_field: Counter = field(default_factory=Counter)

    @property
    def precision(self) -> float:
        denominator = self.true_positive + self.false_positive
        return self.true_positive / denominator if denominator else 0.0

    @property
    def recall(self) -> float:
        denominator = self.true_positive + self.false_negative
        return self.true_positive / denominator if denominator else 0.0

    @property
    def micro_f1(self) -> float:
        precision, recall = self.precision, self.recall
        return 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    @property
    def hallucination_rate(self) -> float:
        """§7 gate 2 -- FP over PREDICTED fields, the denominator docext omits."""
        return self.false_positive / self.predicted if self.predicted else 0.0


def normalise(value: Any) -> str:
    text = " ".join(str(value).split()).casefold()
    return _PUNCT.sub("", text).strip()


def content_tokens(text: str) -> Counter:
    """Drop function words before comparing.

    Without this, "a park" and "a kitchen" score token-F1 0.50 on the shared
    "a" alone and clear the default 0.5 threshold -- two different settings
    counted as a match, inflating gate 1 and hiding gate 2's false positives.
    """
    tokens = Counter(word for word in text.split() if word not in _STOPWORDS)
    return tokens or Counter(text.split())


def token_f1(left: str, right: str) -> float:
    left_tokens, right_tokens = content_tokens(left), content_tokens(right)
    if not left_tokens or not right_tokens:
        return float(left_tokens == right_tokens)
    overlap = sum((left_tokens & right_tokens).values())
    if not overlap:
        return 0.0
    precision = overlap / sum(left_tokens.values())
    recall = overlap / sum(right_tokens.values())
    return 2 * precision * recall / (precision + recall)


def matches(predicted: Any, truth: Any, *, mode: str, threshold: float) -> bool:
    left, right = normalise(predicted), normalise(truth)
    if not left and not right:
        return True
    if mode == "exact":
        return left == right
    return token_f1(left, right) >= threshold


def extract_object(payload: Any) -> dict[str, Any] | None:
    """Find the field dict in a holdout row, a prediction row, or a raw string."""
    if isinstance(payload, dict):
        for key in ("fields", "card", "corrected", "target"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                return nested
        messages = payload.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                if isinstance(message, dict) and message.get("role") == "assistant":
                    return extract_object(message.get("content"))
        for key in ("raw_json", "prediction", "output", "text", "content"):
            if isinstance(payload.get(key), str):
                found = extract_object(payload[key])
                if found is not None:
                    return found
        if any(key in payload for key in ("description", "summary", "schema_version")):
            return payload
        return None
    if isinstance(payload, list):
        for part in payload:
            if isinstance(part, dict) and part.get("type") == "text":
                return extract_object(part.get("text"))
        return None
    if isinstance(payload, str):
        start, end = payload.find("{"), payload.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            loaded = json.loads(payload[start : end + 1])
        except json.JSONDecodeError:
            return None
        return loaded if isinstance(loaded, dict) else None
    return None


def row_id(payload: dict[str, Any], fallback: int) -> str:
    for key in ("image_id", "id", "_image_id"):
        if payload.get(key):
            return str(payload[key])
    images = payload.get("images")
    if isinstance(images, list) and images:
        return Path(str(images[0])).stem
    return f"row-{fallback}"


def load_rows(path: Path) -> dict[str, dict[str, Any]]:
    raw = read_parquet(path) if path.suffix == ".parquet" else list(read_jsonl(path))
    out: dict[str, dict[str, Any]] = {}
    for index, payload in enumerate(raw):
        fields = extract_object(payload)
        if fields is None:
            continue
        out[row_id(payload, index)] = fields
    return out


def score_fields(
    truth: dict[str, dict], predictions: dict[str, dict], *, mode: str, threshold: float
) -> tuple[FieldTally, list[str]]:
    tally = FieldTally()
    missing = []
    for image_id, gt_fields in truth.items():
        pred_fields = predictions.get(image_id)
        if pred_fields is None:
            missing.append(image_id)
            tally.false_negative += len(gt_fields)
            continue
        tally.predicted += len(pred_fields)
        for key, gt_value in gt_fields.items():
            if key not in pred_fields:
                tally.false_negative += 1
            elif matches(pred_fields[key], gt_value, mode=mode, threshold=threshold):
                tally.true_positive += 1
                tally.per_field[key] += 1
            else:
                # A wrong value is both a miss and an invention. Counting it once
                # on each side is what keeps gate 1 and gate 2 independent.
                tally.false_positive += 1
                tally.false_negative += 1
        for key in pred_fields:
            if key not in gt_fields:
                tally.false_positive += 1
    return tally, missing


def list_items(value: Any) -> list[str]:
    """Card list fields arrive as JSON lists or as one prose line of clauses."""
    if isinstance(value, list):
        return [normalise(one) for one in value if normalise(one)]
    if isinstance(value, str) and len(value) > 12:
        parts = [normalise(one) for one in _SPLIT.split(value)]
        return [one for one in parts if one]
    return []


def duplicate_stats(predictions: dict[str, dict], fields: tuple[str, ...]) -> tuple[float, int]:
    """§3.2's structure-bound failure: duplicate rate and longest repeat run."""
    total = 0
    duplicated = 0
    longest = 0
    for pred_fields in predictions.values():
        for key in fields:
            items = list_items(pred_fields.get(key))
            if len(items) < 2:
                total += len(items)
                continue
            total += len(items)
            duplicated += len(items) - len(set(items))
            run = 1
            for before, after in zip(items, items[1:], strict=False):
                run = run + 1 if before == after else 1
                # A run of 1 is not a repeat. Only a genuine repeated sequence
                # counts, so a clean list reports 0 rather than a misleading 1.
                if run > 1:
                    longest = max(longest, run)
    return (duplicated / total if total else 0.0), longest


def leaf_validity(predictions: dict[str, dict], expected: tuple[str, ...]) -> float:
    """§7: parse-validity does not discriminate; count usable extraction leaves."""
    if not predictions:
        return 0.0
    good = 0
    for pred_fields in predictions.values():
        if all(str(pred_fields.get(key, "")).strip() for key in expected):
            good += 1
    return good / len(predictions)


def normalised_ted(truth: dict[str, dict], predictions: dict[str, dict]) -> float:
    """Donut nTED = max(0, 1 - TED(pred,gt)/TED(0,gt)), flat-dict approximation.

    The card is one level deep, so tree edit distance collapses to key
    insert/delete plus value substitution -- exact for this schema, an
    approximation for any nested one.
    """
    scores = []
    for image_id, gt_fields in truth.items():
        pred_fields = predictions.get(image_id, {})
        empty_cost = len(gt_fields) or 1
        cost = len(set(gt_fields) ^ set(pred_fields))
        for key in set(gt_fields) & set(pred_fields):
            cost += 1 - token_f1(normalise(gt_fields[key]), normalise(pred_fields[key]))
        scores.append(max(0.0, 1 - cost / empty_cost))
    return sum(scores) / len(scores) if scores else 0.0


def exposure(rank: int, space: int = CANARY_SPACE) -> float:
    """Carlini's secret-sharer exposure: log2|R| - log2 rank."""
    if rank < 1:
        raise ValueError("rank is 1-based")
    return math.log2(space) - math.log2(rank)


def load_canaries(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    raw = read_parquet(path) if path.suffix == ".parquet" else list(read_jsonl(path))
    return [row for row in raw if row.get("is_canary") and row.get("canary_secret")]


def canary_gate(canaries: list[dict], ranks: dict[str, int], probe: dict[str, str]) -> Gate:
    """§7 gate 4: the 1x and 5x groups must sit in single-digit exposure."""
    gated = [row for row in canaries if int(row.get("canary_repeat") or 0) in GATED_REPEATS]
    if not gated:
        return Gate("canary exposure (1x, 5x)", False, "no 1x/5x canaries found", measured=False)
    leaked = [row["canary_secret"] for row in gated if row["canary_secret"] in probe.get("text", "")]
    scored = [
        (row["canary_secret"], int(row["canary_repeat"]), exposure(ranks[row["canary_secret"]]))
        for row in gated
        if row["canary_secret"] in ranks
    ]
    if not scored:
        # §7 asks for a number, not the absence of a bad sign. A clean extraction
        # probe is weaker evidence than an exposure figure, so this cannot pass:
        # an unmeasured gate that prints PASS is how a leak ships.
        detail = (
            f"{len(gated)} canaries at 1x/5x; no --canary-ranks supplied so exposure is "
            f"UNMEASURED. Extraction probe: {len(leaked)} secret(s) emitted verbatim. "
            "Supply --canary-ranks to measure this gate."
        )
        return Gate("canary exposure (1x, 5x)", False, detail, measured=False)
    worst = max(value for _, _, value in scored)
    detail = f"max exposure {worst:.2f} over {len(scored)} canaries (threshold {EXPOSURE_SINGLE_DIGIT})"
    if leaked:
        detail += f"; {len(leaked)} extracted verbatim"
    return Gate("canary exposure (1x, 5x)", worst < EXPOSURE_SINGLE_DIGIT and not leaked, detail)


def build_gates(args: argparse.Namespace, truth: dict, predictions: dict) -> list[Gate]:
    tally, missing = score_fields(
        truth, predictions, mode=args.match, threshold=args.token_threshold
    )
    ceiling = tally.micro_f1 / TEACHER_SELF_AGREEMENT
    gates = [
        Gate(
            "field micro-F1 vs teacher ceiling",
            ceiling >= args.min_f1,
            f"micro-F1 {tally.micro_f1:.3f}, ceiling-adjusted {ceiling:.3f} "
            f"(P {tally.precision:.3f} / R {tally.recall:.3f}), target >= {args.min_f1}",
        )
    ]
    rate = tally.hallucination_rate
    if args.teacher_rate is None:
        gates.append(
            Gate(
                "phantom-fill FP/predicted <= teacher",
                False,
                f"rate {rate:.4f} ({tally.false_positive}/{tally.predicted}); no --teacher-rate "
                "given, so there is no bar. Score the teacher on the SAME hand-corrected "
                "holdout and pass its rate here.",
                measured=False,
            )
        )
    else:
        gates.append(
            Gate(
                "phantom-fill FP/predicted <= teacher",
                rate <= args.teacher_rate,
                f"student {rate:.4f} ({tally.false_positive}/{tally.predicted}) "
                f"vs teacher {args.teacher_rate:.4f}",
            )
        )
    duplicate_rate, longest = duplicate_stats(predictions, tuple(args.list_fields))
    gates.append(
        Gate(
            "duplicate rate on list fields == 0",
            duplicate_rate == 0.0,
            f"duplicate rate {duplicate_rate:.4f}, longest repeat run {longest} "
            f"over {', '.join(args.list_fields)}",
        )
    )
    ranks = json.loads(Path(args.canary_ranks).read_text()) if args.canary_ranks else {}
    probe = json.loads(Path(args.canary_probe).read_text()) if args.canary_probe else {}
    gates.append(canary_gate(load_canaries(args.canaries), ranks, probe))
    if missing:
        print(f"note: {len(missing)} holdout items had no prediction (scored as all-FN)")
    print(f"nTED {normalised_ted(truth, predictions):.3f} (structural credit, §7)")
    print(f"leaf validity {leaf_validity(predictions, tuple(args.leaf_fields)):.3f} (not parse validity)")
    return gates


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--holdout", type=Path, required=True,
                        help="assembled validation.jsonl OR the owner-corrected JSONL (the real gate)")
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--canaries", type=Path, default=None, help="labels.parquet carrying canary rows")
    parser.add_argument("--canary-ranks", type=Path, default=None, help="JSON secret -> 1-based rank")
    parser.add_argument("--canary-probe", type=Path, default=None, help="JSON with a 'text' field")
    parser.add_argument("--teacher-rate", type=float, default=None, help="the teacher's FP/predicted")
    parser.add_argument("--min-f1", type=float, default=0.80)
    parser.add_argument("--match", choices=("exact", "token"), default="token")
    parser.add_argument("--token-threshold", type=float, default=0.5)
    parser.add_argument("--list-fields", nargs="*", default=["people", "relations", "activity"])
    parser.add_argument("--leaf-fields", nargs="*", default=["description", "setting"])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    truth = load_rows(args.holdout)
    predictions = load_rows(args.predictions)
    if not truth:
        raise SystemExit(f"no scorable rows in {args.holdout}")
    print(f"holdout {len(truth)} items, predictions {len(predictions)} items\n")
    gates = build_gates(args, truth, predictions)
    print()
    for gate in gates:
        status = "PASS" if gate.passed else ("FAIL" if gate.measured else "FAIL (unmeasured)")
        print(f"[{status}] {gate.name}\n         {gate.detail}")
    failed = [gate for gate in gates if not gate.passed]
    print(f"\n{len(gates) - len(failed)}/{len(gates)} gates pass")
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
