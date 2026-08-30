#!/usr/bin/env python3
"""Replay the smart-edit matrix with the pair-decision cascade wired in live.

Phase C of the pairhead cascade, at the operating point the lever sweep picked
(`levers-report.json`, `final_arithmetic.best_model = lever3_retrained`, profile
`loose_95pct_agreement`): a raw-cosine prefilter, then the retrained 391-feature
head, then the local 27B for whatever is left. Projected on the 2007 case:
3.1% prefiltered, 88.0% head-decided, 8.8% residual.

Three monkeypatches, all applied at import:

1. `_build_fused_cards` -> one exact frozen cards.json (verbatim from the
   frozen-cards replay helper), so the card phase costs nothing and the moment
   membership is byte-identical to the run being compared against.
2. `verdicts_beside` -> a surgically blanked copy of the visual judgment DB.
   Every pass stays warm except pass-2-selects, whose rows were deleted, so the
   cascade is measured cold exactly where it acts.
3. `selection_selects._ask_one_pair` -> the cascade. That function is the single
   choke point for every pair arrangement: the sequential path, the concurrent
   `_PairBatchReader`, and the final-duplicates wall all call it.

Scope guard: only within-moment selects pairs are cascaded. The final-duplicates
wall (`final-duplicate-####` scope ids) compares ACROSS moments, where a wrong
"same" drops a whole occasion; the sweep could not reach the <=0.5% dangerous
cap at any coverage, so that pass stays 100% on the 27B.

Embed-at-start: an asset outside the banked `ids.json` (a reservoir pick pulled
in after embeddings.npy was built -- 68 of them in the v28 run) used to force
every one of its pairs onto the 27B with reason "no embedding". `PairHead` now
embeds a miss lazily, on first use, with the same DINOv2 ViT-S/14 transform as
`probe_pairhead_embed.py`, and appends it to an in-memory overlay -- the banked
embeddings.npy/ids.json on disk are never rewritten. Needs the `audio-ml` extra
(torch); if torch or the model load is unavailable, that is logged once and
every later miss this run falls back to the old "no embedding" -> model route.

`_ask_one_pair` returns `bool | None` (`None` means "no usable answer", never
"different"), so a head verdict is a plain bool -- there is no decision dataclass
at this seam to reproduce. The reason, the probability, the cosine distance and
both timings are written to the decisions JSONL instead.

`verdicts_beside` redirects VISUAL judgments only. The text phase (thesis,
allocation, card summaries) never calls it -- `_text_phase` resolves its own
`cache_path = text_cache_path or out.parent / "text-judgments.db"`
(probe_smart_edit_matrix.py), sourced from matrix.py's own `--text-cache` flag.
Point PAIRHEAD_TEXT_CACHE at the comparison run's `text-judgments.db` (or pass
`--text-cache` directly) or every text call falls back to a fresh db under this
run's own `--out` and starts cold -- confirmed on disk: v27's and v28's
`text-judgments.db` are two different files, 196,608 vs 110,592 bytes, because
v28 never set `--text-cache`. That reads as the thesis "diverging" from a
byte-identical prompt, but it is a cold cache being regenerated against a
nondeterministic server, not a bug in the blanked-DB redirect above.

Environment:
  MATRIX_FROZEN_CARDS      cards.json to reuse (required for a real run)
  PAIRHEAD_MATRIX_DIR      head artifacts dir (default: pairhead-2026-08-30)
  PAIRHEAD_JUDGMENTS_DB    blanked visual judgment DB
  PAIRHEAD_DECISIONS_OUT   decisions JSONL
  PAIRHEAD_T_SAME          decide "same" at or above this p (default: 0.85)
  PAIRHEAD_T_DIFF          decide "different" at or below this p (default: 0.33)
  PAIRHEAD_TAU_FAR         cosine distance above which no head call is made
  PAIRHEAD_TEXT_CACHE      matrix.py --text-cache to reuse (thesis/allocation only;
                           verdicts_beside never touches this path -- see below)

`--rebuild-model-v2` regenerates the retrained head deterministically.
`--selftest` exercises every branch of the decision path without a server.
"""

from __future__ import annotations

import atexit
import json
import os
import pickle
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import probe_description_moment_cut as prototype  # noqa: E402
import probe_smart_edit_matrix as matrix  # noqa: E402
from probe_pairhead_cascade import pair_features  # noqa: E402
from probe_pairhead_levers import (  # noqa: E402
    delta_t_bucket_onehot,
    hamming_pair_features,
)

from immich_memories.analysis import selection_selects  # noqa: E402
from immich_memories.analysis.duplicate_hashing import (  # noqa: E402
    compute_thumbnail_hash,
    hamming_distance,
)

DEFAULT_MATRIX_DIR = Path.home() / ".immich-memories-matrix" / "pairhead-2026-08-30"
THUMBNAIL_CACHE = Path.home() / ".immich-memories" / "cache" / "thumbnails"

# levers-report.json, final_arithmetic.bank_6384.loose_95pct_agreement. Asymmetric
# because the two errors are not the same error: a wrong "same" (dangerous) drops
# a picture, a wrong "different" only keeps one too many. Read from the report at
# startup; these are the fallback if it is missing.
#
# FALLBACK_T_SAME is 0.85, not the sweep's own loose_95pct_agreement t_same of 0.73:
# the v28 live-cascade replay found 3 dangerous same-flips, all sitting at p~=0.80,
# inside the old band. t_same_nudge_report.json (scripts/probe_pairhead_levers.py
# machinery, model-v2 predictions on the held-out test split) measures the trade --
# bank-wide head coverage 88.0%->81.0% (agreement 95.0%->95.7%), dangerous-within-head
# 9.4%->6.8%; on just the 2007 case's test-split slice (n=84), coverage 77.4%->73.8%
# but dangerous-within-head 3.3%->0.0% (2 of 61 to 0 of 59). This constant is the
# fallback used when levers-report.json is absent or its measured value is
# overridden; a report already on disk still wins unless PAIRHEAD_T_SAME is set.
FALLBACK_T_SAME = 0.85
FALLBACK_T_DIFF = 0.33
FALLBACK_TAU_FAR = 0.8541725277900696

# Prefix of the cross-moment duplicate wall's scope ids, which never cascade.
FINAL_DUPLICATE_SCOPE = "final-duplicate-"

MATRIX_DIR = Path(os.environ.get("PAIRHEAD_MATRIX_DIR", DEFAULT_MATRIX_DIR)).expanduser()
JUDGMENTS_DB = Path(
    os.environ.get("PAIRHEAD_JUDGMENTS_DB", MATRIX_DIR / "judgments-blanked-selects.db")
).expanduser()
DECISIONS_OUT = Path(
    os.environ.get("PAIRHEAD_DECISIONS_OUT", MATRIX_DIR / "live-cascade-decisions.jsonl")
).expanduser()
MODEL_V1_PATH = MATRIX_DIR / "model.pkl"
MODEL_V2_PATH = MATRIX_DIR / "model-v2.pkl"


def _operating_point() -> tuple[float, float, float]:
    """The winning band and prefilter, from the lever report unless overridden."""
    report = MATRIX_DIR / "levers-report.json"
    measured: dict = {}
    if report.exists():
        payload = json.loads(report.read_text())
        measured = payload["final_arithmetic"]["bank_6384"]["loose_95pct_agreement"]
    t_same = float(os.environ.get("PAIRHEAD_T_SAME", measured.get("t_same", FALLBACK_T_SAME)))
    t_diff = float(os.environ.get("PAIRHEAD_T_DIFF", measured.get("t_diff", FALLBACK_T_DIFF)))
    tau_far = float(os.environ.get("PAIRHEAD_TAU_FAR", measured.get("tau", FALLBACK_TAU_FAR)))
    if not 0.0 < t_diff < t_same < 1.0:
        raise ValueError(f"band must satisfy 0 < t_diff < t_same < 1: {t_diff}, {t_same}")
    return t_same, t_diff, tau_far


T_SAME, T_DIFF, TAU_FAR = _operating_point()


# --- the frozen cards monkeypatch (verbatim from the replay helper) ----------


async def _frozen_cards(groups, **_kwargs):
    path = Path(os.environ["MATRIX_FROZEN_CARDS"]).expanduser().resolve()
    payload = json.loads(path.read_text())
    rows = payload.get("cards")
    if not isinstance(rows, list):
        raise ValueError("frozen cards artifact needs a cards list")
    by_id = {row.get("moment_id"): row for row in rows if isinstance(row, dict)}
    if len(by_id) != len(rows) or len(by_id) != len(groups):
        raise ValueError("frozen cards no longer match the production moment count")

    cards = []
    for index, group in enumerate(groups, start=1):
        moment_id = f"M{index:03d}"
        row = by_id.get(moment_id)
        if (
            row is None
            or row.get("production_group_id") != group.group_id
            or tuple(row.get("asset_ids", ())) != group.candidate_ids
            or not isinstance(row.get("summary"), str)
            or not row["summary"].strip()
        ):
            raise ValueError(f"frozen card membership changed for {moment_id}")
        moment = prototype.Moment(alias=moment_id, group=group, descriptions=())
        cards.append(prototype.MomentCard(moment, row["summary"], None))
    print(f"frozen-moment-cards: reused {len(cards)} exact cards", flush=True)
    return tuple(cards), tuple(None for _card in cards)


# --- the head ---------------------------------------------------------------


class PairHead:
    """The trained pair head and everything its features need, loaded once.

    Two models stay resident: the 391-feature retrained head (embeddings + time
    delta + aHash) and the original 384-feature one. A pair missing either extra
    feature falls back to v1 rather than feeding the retrained model a made-up
    "unknown" encoding it would then answer confidently from.

    Loading is guarded because the selects phase asks from a thread pool.
    Prediction is not: a fitted sklearn estimator is read-only at predict time.
    """

    def __init__(self, matrix_dir: Path) -> None:
        self.matrix_dir = matrix_dir
        self._load_lock = threading.Lock()
        self._hash_lock = threading.Lock()
        self._model_v1 = None
        self._model_v2 = None
        self._pca = None
        self._raw = None
        self._rows = None
        self._index: dict[str, int] = {}
        self._timestamps: dict[str, datetime] = {}
        self._hashes: dict[str, str | None] = {}
        # Embed-at-start overlay: assets outside the banked ids.json (e.g. reservoir
        # picks pulled in after the bank was built) get embedded here, on their first
        # miss, and appended to _raw/_rows/_index in memory only -- embeddings.npy on
        # disk is never touched. `_embed_unavailable` is sticky once set: one torch or
        # model-load failure means every later miss this run also falls back, instead
        # of retrying (and re-logging) per asset.
        self._embed_lock = threading.Lock()
        self._embed_torch = None
        self._embed_module = None
        self._embed_model = None
        self._embed_device: str | None = None
        self._embed_unavailable: str | None = None

    def load(self) -> None:
        with self._load_lock:
            if self._model_v2 is not None:
                return
            if not MODEL_V2_PATH.exists():
                raise SystemExit(
                    f"retrained head missing: {MODEL_V2_PATH} "
                    "(run this script with --rebuild-model-v2)"
                )
            with (self.matrix_dir / "pca.pkl").open("rb") as handle:
                pca = pickle.load(handle)
            with MODEL_V1_PATH.open("rb") as handle:
                model_v1 = pickle.load(handle)
            with MODEL_V2_PATH.open("rb") as handle:
                model_v2 = pickle.load(handle)
            raw = np.load(self.matrix_dir / "embeddings.npy")
            ids = json.loads((self.matrix_dir / "ids.json").read_text())
            self._pca = pca
            self._raw = raw
            self._rows = pca.transform(raw).astype(np.float32)
            self._index = {asset_id: row for row, asset_id in enumerate(ids)}
            self._timestamps = {
                asset_id: datetime.fromisoformat(iso.replace("Z", "+00:00"))
                for asset_id, iso in json.loads(
                    (self.matrix_dir / "timestamps.json").read_text()
                )["timestamps"].items()
            }
            self._hashes = dict(json.loads((self.matrix_dir / "hashes.json").read_text())["hashes"])
            self._model_v1 = model_v1
            self._model_v2 = model_v2

    def ready(self) -> None:
        if self._model_v2 is None:
            self.load()

    def rows_for(self, left_id: str, right_id: str) -> tuple[int, int] | None:
        self.ready()
        left = self._resolve_row(left_id)
        right = self._resolve_row(right_id)
        return None if left is None or right is None else (left, right)

    def _resolve_row(self, asset_id: str) -> int | None:
        """The banked row for this asset, or one embedded on demand into the overlay."""
        row = self._index.get(asset_id)
        return row if row is not None else self._embed_on_demand(asset_id)

    def _ensure_embedder(self) -> bool:
        """Lazily load DINOv2 ViT-S/14. Sticky False once torch/MPS/the model is unusable."""
        if self._embed_model is not None:
            return True
        if self._embed_unavailable is not None:
            return False
        try:
            import probe_pairhead_embed as embedder
            import torch  # deferred: only the audio-ml extra installs this
        except ImportError as error:
            self._embed_unavailable = f"torch unavailable ({error!r})"
            print(
                f"pairhead embed-at-start: {self._embed_unavailable}; "
                "missing-embedding pairs fall back to the model route",
                flush=True,
            )
            return False
        try:
            model, source, fallback_reason = embedder.load_model()
        except Exception as error:  # noqa: BLE001 - any load failure just means "can't embed here"
            self._embed_unavailable = f"DINOv2 model load failed ({error!r})"
            print(
                f"pairhead embed-at-start: {self._embed_unavailable}; "
                "missing-embedding pairs fall back to the model route",
                flush=True,
            )
            return False
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        self._embed_torch = torch
        self._embed_module = embedder
        self._embed_model = model.to(device).eval()
        self._embed_device = device
        if fallback_reason:
            print(f"pairhead embed-at-start: {fallback_reason}", flush=True)
        print(f"pairhead embed-at-start: {source} ready on {device}", flush=True)
        return True

    def _forward(self, tensor) -> np.ndarray:
        """One image through the resident embedder; MPS failure retries once on CPU."""
        torch = self._embed_torch
        try:
            with torch.no_grad():
                batch = tensor.unsqueeze(0).to(self._embed_device)
                output = self._embed_model(batch)
                return (output / output.norm(dim=1, keepdim=True)).to("cpu").numpy()[0]
        except RuntimeError as error:
            if self._embed_device != "mps":
                raise
            print(
                f"pairhead embed-at-start: MPS forward failed ({error!r}); falling back to CPU",
                flush=True,
            )
            self._embed_device = "cpu"
            self._embed_model = self._embed_model.to("cpu")
            with torch.no_grad():
                batch = tensor.unsqueeze(0).to("cpu")
                output = self._embed_model(batch)
                return (output / output.norm(dim=1, keepdim=True)).numpy()[0]

    def _embed_on_demand(self, asset_id: str) -> int | None:
        """Embed one asset missing from ids.json and append it to the in-memory overlay.

        Never touches embeddings.npy/ids.json on disk -- the overlay lives only in
        this process's _raw/_rows/_index for the life of this run.
        """
        with self._embed_lock:
            row = self._index.get(asset_id)
            if row is not None:
                return row
            if not self._ensure_embedder():
                return None
            try:
                path = self._embed_module.resolve_image_path(
                    asset_id, self.matrix_dir / "previews", THUMBNAIL_CACHE
                )
                tensor = self._embed_module.load_and_transform(path)
            except (OSError, ValueError) as error:
                print(f"pairhead embed-at-start: no preview for {asset_id} ({error!r})", flush=True)
                return None
            try:
                raw_vector = self._forward(tensor).astype(np.float32)
            except RuntimeError as error:
                print(
                    f"pairhead embed-at-start: forward pass failed for {asset_id} ({error!r})",
                    flush=True,
                )
                return None
            pca_vector = self._pca.transform(raw_vector.reshape(1, -1)).astype(np.float32)
            row = self._raw.shape[0]
            self._raw = np.concatenate([self._raw, raw_vector.reshape(1, -1)], axis=0)
            self._rows = np.concatenate([self._rows, pca_vector], axis=0)
            self._index[asset_id] = row
            print(
                f"pairhead embed-at-start: embedded {asset_id} on demand (overlay row {row})",
                flush=True,
            )
            return row

    def cosine_distance(self, rows: tuple[int, int]) -> float:
        """1 - cosine similarity on the raw (L2-normalised) embeddings."""
        left, right = rows
        return float(1.0 - np.dot(self._raw[left], self._raw[right]))

    def taken_at(self, asset_id: str, supplied: datetime | None) -> datetime | None:
        """The candidate's own timestamp, which is the field the head trained on."""
        return supplied if supplied is not None else self._timestamps.get(asset_id)

    def thumbnail_hash(self, asset_id: str) -> str | None:
        """The banked aHash, or one computed from the same preview bytes it used."""
        with self._hash_lock:
            if asset_id in self._hashes:
                return self._hashes[asset_id]
        digest = None
        for candidate in (
            self.matrix_dir / "previews" / f"{asset_id}.jpg",
            THUMBNAIL_CACHE / asset_id[:2] / f"{asset_id}_preview.jpg",
        ):
            if candidate.exists():
                try:
                    digest = compute_thumbnail_hash(candidate.read_bytes())
                except (OSError, ValueError):
                    digest = None
                break
        with self._hash_lock:
            self._hashes[asset_id] = digest
        return digest

    def probability(
        self,
        left_id: str,
        right_id: str,
        rows: tuple[int, int],
        *,
        taken_left: datetime | None,
        taken_right: datetime | None,
    ) -> tuple[float, str]:
        """P(same picture) and which head answered: the 391-feature one, or v1."""
        left, right = rows
        base = pair_features(self._rows[left], self._rows[right])
        seconds = None
        first = self.taken_at(left_id, taken_left)
        second = self.taken_at(right_id, taken_right)
        if first is not None and second is not None:
            seconds = abs((first - second).total_seconds())
        left_hash = self.thumbnail_hash(left_id)
        right_hash = self.thumbnail_hash(right_id)
        hamming = (
            hamming_distance(left_hash, right_hash)
            if left_hash is not None and right_hash is not None
            else None
        )
        if seconds is None or hamming is None:
            return float(self._model_v1.predict_proba(base.reshape(1, -1))[0, 1]), "head-v1"
        extra = np.concatenate(
            [delta_t_bucket_onehot(seconds), hamming_pair_features(hamming)]
        )
        features = np.concatenate([base, extra]).reshape(1, -1)
        return float(self._model_v2.predict_proba(features)[0, 1]), "head"


class Tally:
    """Cascade counters and the decisions JSONL, shared across the pair pool."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.seen = 0
        self.by_route: dict[str, int] = {}
        self.head_ms = 0.0
        self.model_ms = 0.0

    def record(self, row: dict, *, head_ms: float, model_ms: float) -> None:
        with self.lock:
            self.seen += 1
            self.head_ms += head_ms
            self.model_ms += model_ms
            route = row["decided_by"]
            self.by_route[route] = self.by_route.get(route, 0) + 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")

    def summary(self) -> str:
        routes = ", ".join(f"{name} {count}" for name, count in sorted(self.by_route.items()))
        decided = self.by_route.get("head", 0) + self.by_route.get("head-v1", 0)
        return (
            f"pairhead cascade: pairs seen {self.seen} / head-decided {decided} / "
            f"prefiltered {self.by_route.get('prefilter', 0)} / "
            f"model-decided {self.seen - decided - self.by_route.get('prefilter', 0)} "
            f"[{routes}] / head {self.head_ms:.1f}ms total / "
            f"model {self.model_ms / 1000.0:.1f}s total"
        )


HEAD = PairHead(MATRIX_DIR)
TALLY = Tally(DECISIONS_OUT)
_ORIGINAL_ASK_ONE_PAIR = selection_selects._ask_one_pair
_ORIGINAL_RETURN = _ORIGINAL_ASK_ONE_PAIR.__annotations__["return"]


def _route(left_id, right_id, taken_left, taken_right):
    """Decide the pair, or say which model route it needs. Never raises."""
    rows = HEAD.rows_for(left_id, right_id)
    if rows is None:
        return {"decided_by": "model", "reason": "no embedding", "p": None, "distance": None}
    distance = HEAD.cosine_distance(rows)
    if distance > TAU_FAR:
        return {
            "decided_by": "prefilter",
            "reason": f"cosine distance {distance:.4f} > tau_far {TAU_FAR:.4f}",
            "same": False,
            "p": None,
            "distance": distance,
        }
    probability, variant = HEAD.probability(
        left_id, right_id, rows, taken_left=taken_left, taken_right=taken_right
    )
    if probability >= T_SAME or probability <= T_DIFF:
        return {
            "decided_by": variant,
            "reason": f"pairhead cascade p={probability:.3f}",
            "same": probability >= T_SAME,
            "p": probability,
            "distance": distance,
        }
    return {
        "decided_by": "model",
        "reason": "abstention band",
        "p": probability,
        "distance": distance,
    }


def _cascading_ask_one_pair(
    scope_id, arrangement, pair, atlas, requester, sheet_output_dir, limits
):
    """Answer one pair arrangement from the cascade, or hand it to the model.

    Returns exactly what the wrapped function returns: `bool | None`. Every
    feature the head uses is order-invariant, so both arrangements of a decided
    pair agree without a second call -- the same outcome the two-order contract
    buys, at no model cost.
    """
    earlier, later = pair
    started = time.monotonic()
    if scope_id.startswith(FINAL_DUPLICATE_SCOPE):
        # Cross-moment. A wrong "same" here drops an occasion, and the sweep
        # never reached the <=0.5% dangerous cap, so this pass is not cascaded.
        verdict = {
            "decided_by": "model-final-dup",
            "reason": "cross-moment scope, never cascaded",
            "p": None,
            "distance": None,
        }
    else:
        verdict = _route(earlier.asset_id, later.asset_id, earlier.taken_at, later.taken_at)
    head_ms = (time.monotonic() - started) * 1000.0

    row = {
        "scope_id": scope_id,
        "arrangement": arrangement,
        "a": earlier.asset_id,
        "b": later.asset_id,
        "p": verdict["p"],
        "distance": verdict["distance"],
        "decided_by": verdict["decided_by"],
        "reason": verdict["reason"],
        "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
    }
    if "same" in verdict:
        row |= {"same": verdict["same"], "warning": None, "head_ms": head_ms, "model_ms": 0.0}
        TALLY.record(row, head_ms=head_ms, model_ms=0.0)
        return verdict["same"]

    model_started = time.monotonic()
    answer = _ORIGINAL_ASK_ONE_PAIR(
        scope_id, arrangement, pair, atlas, requester, sheet_output_dir, limits
    )
    model_ms = (time.monotonic() - model_started) * 1000.0
    row |= {
        "same": answer,
        "warning": None if answer is not None else "unreadable pair answer",
        "head_ms": head_ms,
        "model_ms": model_ms,
    }
    TALLY.record(row, head_ms=head_ms, model_ms=model_ms)
    return answer


def _blanked_verdicts(_cache_dir):
    """Every visual pass reads the blanked copy, so the live bank is untouched."""
    return JUDGMENTS_DB


def _apply_patches() -> None:
    if not JUDGMENTS_DB.exists():
        raise SystemExit(f"blanked visual judgment DB missing: {JUDGMENTS_DB}")
    if os.environ.get("MATRIX_FROZEN_CARDS"):
        matrix._build_fused_cards = _frozen_cards
    else:
        print(
            "fresh-cards mode: MATRIX_FROZEN_CARDS unset, building moment cards for real",
            flush=True,
        )
    matrix.verdicts_beside = _blanked_verdicts
    selection_selects._ask_one_pair = _cascading_ask_one_pair


@atexit.register
def _report() -> None:
    if TALLY.seen:
        print(TALLY.summary(), flush=True)


_apply_patches()


# --- deterministic rebuild of the retrained head -----------------------------


def _rebuild_model_v2() -> int:
    """Regenerate the 391-feature head the lever sweep measured but never saved.

    Every seed in the path is fixed (PCA, the connected-components split, the C
    sweep, the calibrator), so this reproduces that exact estimator; the test
    metrics are checked against levers-report.json before anything is written.
    """
    import probe_pairhead_levers as levers

    report = json.loads((MATRIX_DIR / "levers-report.json").read_text())["lever3"]
    bank = levers.load_bank()
    extra = levers.build_extra_features(
        bank["pairs"], levers.load_timestamps(), levers.load_hashes()
    )
    retrain = levers.retrain_with_extra_features(bank, extra)
    for field in ("feature_dim", "chosen_c", "test_accuracy", "test_roc_auc"):
        if retrain[field] != report[field]:
            print(f"FAIL: {field} is {retrain[field]}, report says {report[field]}")
            return 1
    with MODEL_V2_PATH.open("wb") as handle:
        pickle.dump(retrain["calibrated_model"], handle)
    print(
        f"wrote {MODEL_V2_PATH} (dim {retrain['feature_dim']}, C {retrain['chosen_c']}, "
        f"accuracy {retrain['test_accuracy']:.4f}, AUC {retrain['test_roc_auc']:.4f})"
    )
    return 0


# --- selftest ---------------------------------------------------------------


class _Tripwire:
    """Anything a decided pair must never touch."""

    def __getattr__(self, name):
        raise AssertionError(f"a cascade-decided pair reached the model path: {name}")


def _stand_in(asset_id: str, taken_at: datetime | None = None):
    from immich_memories.analysis.editorial_contracts import EditorialCandidate
    from immich_memories.api.models import Asset

    moment = taken_at or HEAD._timestamps.get(asset_id) or datetime(2007, 7, 1, 12, tzinfo=UTC)
    source = Asset(
        id=asset_id,
        type="IMAGE",
        fileCreatedAt=moment,
        fileModifiedAt=moment,
        updatedAt=moment,
    )
    return EditorialCandidate(
        asset_id=asset_id,
        taken_at=moment,
        media_kind="photo",
        live_photo_stitch_member_ids=(),
        rendering_family_id=None,
        favourite=False,
        source=source,
        proposed_segment=None,
        shippable_duration=0.0,
        grounded_annotations=(),
    )


def _banked_pairs():
    with (MATRIX_DIR / "pairs.jsonl").open() as handle:
        for line in handle:
            yield json.loads(line)


def _classify_sample():
    """One banked pair per branch: confident head, prefilter, loose-only band."""
    HEAD.ready()
    confident: list[tuple[str, str, dict]] = []
    prefiltered: tuple[str, str, dict] | None = None
    loose_only: tuple[str, str, dict] | None = None
    for payload in _banked_pairs():
        left, right = payload["a"], payload["b"]
        verdict = _route(left, right, None, None)
        if verdict["decided_by"] == "prefilter":
            prefiltered = prefiltered or (left, right, verdict)
        elif "same" in verdict:
            probability = verdict["p"]
            # Inside the loose band but outside the old symmetric 0.945/0.055.
            if 0.055 < probability < 0.945:
                loose_only = loose_only or (left, right, verdict)
            elif len(confident) < 5:
                confident.append((left, right, verdict))
        if len(confident) == 5 and prefiltered and loose_only:
            break
    return confident, prefiltered, loose_only


def _delegation_check(scope_id: str, left: str, right: str, expected_route: str) -> str | None:
    """A route that must reach the wrapped function with its arguments intact."""
    global _ORIGINAL_ASK_ONE_PAIR  # noqa: PLW0603 - restored before returning
    seen: list[tuple] = []

    def _stub(*args):
        seen.append(args)
        return True

    pair = (_stand_in(left), _stand_in(right))
    real = _ORIGINAL_ASK_ONE_PAIR
    _ORIGINAL_ASK_ONE_PAIR = _stub
    try:
        answer = _cascading_ask_one_pair(scope_id, "ab", pair, "atlas", "req", "dir", "lim")
    finally:
        _ORIGINAL_ASK_ONE_PAIR = real
    if answer is not True:
        return f"{expected_route}: delegated answer not forwarded verbatim: {answer!r}"
    if seen != [(scope_id, "ab", pair, "atlas", "req", "dir", "lim")]:
        return f"{expected_route}: delegated arguments changed: {seen!r}"
    last = json.loads(TALLY.path.read_text().splitlines()[-1])
    if last["decided_by"] != expected_route:
        return f"expected route {expected_route}, recorded {last['decided_by']!r}"
    return None


def _ask(scope_id: str, arrangement: str, left: str, right: str):
    tripwire = _Tripwire()
    pair = (_stand_in(left), _stand_in(right))
    if arrangement == "ba":
        pair = (pair[1], pair[0])
    return _cascading_ask_one_pair(
        scope_id, arrangement, pair, tripwire, tripwire, tripwire, tripwire
    )


def _check_confident(confident) -> str | None:
    for index, (left, right, verdict) in enumerate(confident):
        answer = _ask(f"selftest-{index}", "ab", left, right)
        if type(answer) is not bool:
            return f"head returned {type(answer).__name__}, not bool"
        if answer != verdict["same"]:
            return f"head verdict {answer} disagrees with p={verdict['p']}"
        if _ask(f"selftest-{index}", "ba", left, right) != answer:
            return "arrangement changed the head verdict"
    return None


def _check_v1_fallback(left: str, right: str) -> str | None:
    """Dropping one asset's hash must route that pair to the 384-feature head."""
    HEAD.ready()
    with HEAD._hash_lock:
        kept = HEAD._hashes.pop(left, None)
        HEAD._hashes[left] = None
    try:
        _ask("selftest-v1", "ab", left, right)
    finally:
        with HEAD._hash_lock:
            HEAD._hashes[left] = kept
    route = json.loads(TALLY.path.read_text().splitlines()[-1])["decided_by"]
    if route not in {"head-v1", "model"}:
        return f"missing hash routed to {route!r}, not the v1 head"
    return None


def _check_embed_overlay(pair: tuple[str, str, dict]) -> str | None:
    """A banked asset faked missing must be embedded on demand, then still head-decided.

    Hides a real (already-embedded) asset from `_index` -- exactly what a reservoir
    pick outside `ids.json` looks like to the cascade -- and confirms `_route` embeds
    it into the overlay rather than giving up with "no embedding".
    """
    left, right, verdict = pair
    HEAD.ready()
    if not HEAD._ensure_embedder():
        print(f"  SKIP embed-on-demand check: {HEAD._embed_unavailable}")
        return None
    saved_row = HEAD._index.pop(left, None)
    if saved_row is None:
        return f"selftest asset {left} was not banked to begin with"
    rows_before = HEAD._raw.shape[0]
    try:
        answer = _ask("selftest-embed-on-demand", "ab", left, right)
        grew_by = HEAD._raw.shape[0] - rows_before
        overlay_row = HEAD._index.get(left)
    finally:
        HEAD._index[left] = saved_row
        HEAD._raw = HEAD._raw[:rows_before]
        HEAD._rows = HEAD._rows[:rows_before]
    if type(answer) is not bool:
        return f"embed-on-demand pair returned {type(answer).__name__}, not bool"
    if grew_by != 1:
        return f"on-demand embed appended {grew_by} overlay rows, expected 1"
    if overlay_row != rows_before:
        return f"embedded row {overlay_row}, expected the new overlay row {rows_before}"
    if answer != verdict["same"]:
        return f"embedded-on-demand verdict {answer} disagrees with the banked p={verdict['p']}"
    route = json.loads(TALLY.path.read_text().splitlines()[-1])["decided_by"]
    if route not in {"head", "head-v1"}:
        return f"embedded-on-demand pair routed to {route!r}, not the head"
    return None


def _selftest() -> int:  # noqa: C901 - one assertion per cascade branch
    if _ORIGINAL_RETURN != "bool | None":
        print(f"FAIL: wrapped return annotation is {_ORIGINAL_RETURN!r}, not 'bool | None'")
        return 1
    if selection_selects._ask_one_pair is not _cascading_ask_one_pair:
        print("FAIL: the pair choke point is not patched")
        return 1

    confident, prefiltered, loose_only = _classify_sample()
    if len(confident) != 5 or prefiltered is None or loose_only is None:
        print(
            f"FAIL: sample incomplete -- {len(confident)} confident, "
            f"prefilter={prefiltered is not None}, loose-only={loose_only is not None}"
        )
        return 1

    before = TALLY.path.read_text().count("\n") if TALLY.path.exists() else 0
    failure = _check_confident(confident)
    if failure:
        print(f"FAIL: {failure}")
        return 1
    written = [
        json.loads(line) for line in TALLY.path.read_text().splitlines()[before:] if line.strip()
    ]
    if len(written) != 10 or any(row["decided_by"] not in {"head", "head-v1"} for row in written):
        print(f"FAIL: expected 10 head-decided rows, got {[r['decided_by'] for r in written]}")
        return 1

    left, right, verdict = prefiltered
    if _ask("selftest-prefilter", "ab", left, right) is not False:
        print("FAIL: a prefiltered pair did not answer 'different'")
        return 1
    row = json.loads(TALLY.path.read_text().splitlines()[-1])
    if row["decided_by"] != "prefilter" or row["distance"] <= TAU_FAR:
        print(f"FAIL: prefilter row is {row['decided_by']!r} at distance {row['distance']}")
        return 1

    left, right, verdict = loose_only
    if _ask("selftest-loose", "ab", left, right) != verdict["same"]:
        print("FAIL: a loose-band pair did not follow its probability")
        return 1
    row = json.loads(TALLY.path.read_text().splitlines()[-1])
    if not (row["p"] <= T_DIFF or row["p"] >= T_SAME):
        print(f"FAIL: loose-band row p={row['p']} sits outside the band")
        return 1
    print(f"  loose band decided p={row['p']:.3f}, which 0.945/0.055 would have abstained on")

    for check in (
        _delegation_check(
            "final-duplicate-0001", confident[0][0], confident[0][1], "model-final-dup"
        ),
        _check_v1_fallback(confident[1][0], confident[1][1]),
        _check_embed_overlay(confident[2]),
    ):
        if check:
            print(f"FAIL: {check}")
            return 1

    print(
        f"PASS: band {T_DIFF}/{T_SAME}, tau_far {TAU_FAR:.4f} -- 5 confident pairs x 2 "
        "arrangements head-decided, 1 prefiltered, 1 loose-band, 1 v1 fallback, "
        "1 final-duplicates pair delegated, 1 embedded-on-demand pair head-decided"
    )
    print(f"PASS: decisions appended to {TALLY.path}")
    return 0


if __name__ == "__main__":
    if "--rebuild-model-v2" in sys.argv:
        raise SystemExit(_rebuild_model_v2())
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    # MATRIX_FROZEN_CARDS is optional (see _apply_patches): unset runs the
    # fresh-cards path, rebuilding moment cards for real instead of replaying
    # one exact frozen artifact.
    # verdicts_beside (patched above) only redirects the VISUAL judgment cache_path
    # (probe_smart_edit_matrix.py's cache_path=verdicts_beside(...) call sites). Text
    # calls (_text_phase's cache_path = text_cache_path or out.parent /
    # "text-judgments.db") never call verdicts_beside, so leaving PAIRHEAD_TEXT_CACHE
    # unset means every thesis/allocation call falls back to a fresh db under *this
    # run's own* --out and starts cold against the comparison run's warm cache, even
    # though the frozen cards make its prompt byte-identical -- read as "re-ran/
    # diverged", not a cache bug. --text-cache already exists in matrix.py for
    # exactly this; an explicit --text-cache on the command line still wins.
    text_cache = os.environ.get("PAIRHEAD_TEXT_CACHE")
    if text_cache and "--text-cache" not in sys.argv:
        sys.argv += ["--text-cache", text_cache]
    raise SystemExit(matrix.main())
