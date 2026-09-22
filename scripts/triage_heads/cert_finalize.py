"""Fail-closed approval and reservation for the fresh owner certification cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .cohorts import (
    InventoryAsset,
    InventorySnapshot,
    TruthReservationLedger,
    cohort_selection_sha256,
    load_inventory_snapshot,
)
from .memory import acquire_recovery_pipeline_lock
from .semantic_refinement import (
    AuthenticatedSemanticRefinement,
    SemanticRefinementConfig,
    load_authenticated_semantic_refinement,
)

FRESH_CERT_COHORT_NAME = "fresh-location-cert-v3"
REQUIRED_REVIEWER_COUNT = 3
REQUIRED_PAGE_ATTESTATIONS = 2
REVIEW_METHOD = "label-blind-diversity-only"
_DECISION_SCHEMA = "triage-blinded-diversity-review-decision-v1"
_DECISION_KEYS = frozenset(
    {
        "schema",
        "cohort_name",
        "semantic_run_sha256",
        "review_manifest_sha256",
        "method",
        "accept",
        "reviewer_count",
        "inputs",
        "reviews",
        "decision_sha256",
    }
)
_REVIEW_KEYS = frozenset({"reviewer_id", "accept", "reviewed_pages", "inputs"})
_PAGE_KEYS = frozenset({"path", "sha256"})
_REQUIRED_INPUTS = {
    "raw_preview_contact_sheets": True,
    "opaque_audit_ids": True,
    "dinov2_semantic_diversity_diagnostics": True,
    "truth_labels": False,
    "teacher_labels": False,
    "head_outputs": False,
    "captions": False,
    "semantic_search_results": False,
}


def _canonical_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _require_sha256(value: object, *, field: str) -> str:
    text = str(value)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return text


@dataclass(frozen=True, slots=True)
class BlindedReviewDecision:
    decision_sha256: str
    semantic_run_sha256: str
    review_manifest_sha256: str
    reviewed_page_count: int


def _mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_create_only_idempotent(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"{path.name} already has different immutable content")
        os.chmod(path, 0o600)
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise RuntimeError(f"{path.name} already has different immutable content")
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _jsonl_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    return b"".join(_canonical_bytes(row) for row in rows)


def load_blinded_review_decision(
    path: Path,
    *,
    expected_semantic_run_sha256: str,
    expected_review_manifest_sha256: str,
    expected_pages: Sequence[tuple[str, str]],
) -> BlindedReviewDecision:
    """Authenticate an affirmative, label-blind review of every rendered page."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or set(payload) != _DECISION_KEYS:
        raise ValueError("blinded review decision violates its exact schema")
    if payload["schema"] != _DECISION_SCHEMA:
        raise ValueError("unsupported blinded review decision schema")
    if payload["cohort_name"] != FRESH_CERT_COHORT_NAME:
        raise ValueError("blinded review decision names a different cohort")
    if payload["method"] != REVIEW_METHOD:
        raise ValueError("blinded review method must be label-blind-diversity-only")
    if payload["accept"] is not True:
        raise ValueError("blinded review must explicitly accept the cohort")
    if payload["reviewer_count"] != REQUIRED_REVIEWER_COUNT:
        raise ValueError(f"blinded review requires exactly {REQUIRED_REVIEWER_COUNT} reviewers")
    if payload["inputs"] != _REQUIRED_INPUTS:
        raise ValueError("blinded review decision used forbidden or undeclared inputs")

    semantic_digest = _require_sha256(payload["semantic_run_sha256"], field="semantic_run_sha256")
    review_digest = _require_sha256(
        payload["review_manifest_sha256"], field="review_manifest_sha256"
    )
    if semantic_digest != expected_semantic_run_sha256:
        raise ValueError("blinded review decision belongs to a different semantic run")
    if review_digest != expected_review_manifest_sha256:
        raise ValueError("blinded review decision belongs to a different review manifest")

    reviews = payload["reviews"]
    if not isinstance(reviews, list) or len(reviews) != REQUIRED_REVIEWER_COUNT:
        raise ValueError("blinded review decision has the wrong reviewer cardinality")
    expected = tuple(sorted(expected_pages))
    if not expected or len(set(expected)) != len(expected):
        raise ValueError("sealed review manifest pages must be nonempty and unique")
    for relative, digest in expected:
        if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("sealed review page path must be safe and relative")
        _require_sha256(digest, field="review page sha256")

    reviewer_ids: set[str] = set()
    page_attestations: Counter[tuple[str, str]] = Counter()
    full_bundle_reviewers = 0
    for review in reviews:
        if not isinstance(review, Mapping) or set(review) != _REVIEW_KEYS:
            raise ValueError("individual blinded review violates its exact schema")
        reviewer_id = review["reviewer_id"]
        if not isinstance(reviewer_id, str) or not reviewer_id.strip():
            raise ValueError("blinded review requires a nonempty reviewer id")
        if reviewer_id in reviewer_ids:
            raise ValueError("blinded review reviewer ids must be unique")
        reviewer_ids.add(reviewer_id)
        if review["accept"] is not True:
            raise ValueError("every blinded reviewer must explicitly accept")
        if review["inputs"] != _REQUIRED_INPUTS:
            raise ValueError("blinded review used forbidden or undeclared inputs")
        raw_pages = review["reviewed_pages"]
        if not isinstance(raw_pages, list) or not raw_pages:
            raise ValueError("every blinded reviewer must attest at least one page")
        actual: list[tuple[str, str]] = []
        for row in raw_pages:
            if not isinstance(row, Mapping) or set(row) != _PAGE_KEYS:
                raise ValueError("blinded review page violates its exact schema")
            actual.append(
                (
                    str(row["path"]),
                    _require_sha256(row["sha256"], field="reviewed page sha256"),
                )
            )
        actual_set = set(actual)
        if len(actual_set) != len(actual):
            raise ValueError("one blinded reviewer attested a page more than once")
        if not actual_set <= set(expected):
            raise ValueError("blinded review attests a page outside the sealed bundle")
        if tuple(sorted(actual)) == expected:
            full_bundle_reviewers += 1
        page_attestations.update(actual_set)

    if full_bundle_reviewers < 1:
        raise ValueError("blinded review requires one full-bundle primary review")
    if set(page_attestations) != set(expected) or any(
        page_attestations[page] < REQUIRED_PAGE_ATTESTATIONS for page in expected
    ):
        raise ValueError("every sealed review page requires two distinct attestations")

    claimed_digest = _require_sha256(payload["decision_sha256"], field="decision_sha256")
    unsigned = dict(payload)
    unsigned.pop("decision_sha256")
    if _canonical_sha256(unsigned) != claimed_digest:
        raise ValueError("blinded review decision digest does not reproduce")
    return BlindedReviewDecision(
        decision_sha256=claimed_digest,
        semantic_run_sha256=semantic_digest,
        review_manifest_sha256=review_digest,
        reviewed_page_count=len(expected),
    )


def _validate_frozen_semantic_protocol(
    authenticated: AuthenticatedSemanticRefinement,
) -> tuple[Mapping[str, object], Mapping[str, object], Mapping[str, object]]:
    public = _mapping(authenticated.public_manifest, field="semantic public manifest")
    if public.get("schema") != "triage-semantic-refinement-public-v3":
        raise ValueError("semantic evidence is not the v3 protocol")
    if public.get("status") != "pending_private_visual_inspection_not_truth_reserved":
        raise ValueError("semantic evidence is not awaiting blinded review")
    if authenticated.config != SemanticRefinementConfig():
        raise ValueError("semantic refinement changed the frozen v3 selection gates")
    if public.get("config") != authenticated.config.public_dict():
        raise ValueError("semantic public manifest differs from the replayed config")
    if not authenticated.selection.audit.passed or authenticated.selection.audit.failures:
        raise ValueError("semantic selection quality gates did not pass")

    source = _mapping(public.get("source"), field="semantic source lineage")
    truth = _mapping(public.get("truth"), field="semantic truth lineage")
    final = _mapping(public.get("final_selection"), field="semantic final selection")
    final_store = _mapping(public.get("final_preview_store"), field="semantic final preview store")
    private = _mapping(public.get("private_artifacts"), field="semantic private lineage")
    replayed_source = authenticated.universe.source
    expected_source = {
        "inventory_sha256": replayed_source.inventory_sha256,
        "selection_sha256": replayed_source.selection_sha256,
        "manifest_sha256": replayed_source.manifest_sha256,
        "private_index_sha256": replayed_source.private_index_sha256,
        "selection_lock_sha256": replayed_source.selection_lock_sha256,
        "candidate_count": len(replayed_source.rows),
    }
    if source != expected_source or len(replayed_source.rows) != 2_000:
        raise ValueError("semantic source lineage differs from the exact 2,000-row replay")
    if truth.get("reservation_performed") is not False:
        raise ValueError("semantic evidence already claims a truth reservation")
    expected_audit = authenticated.selection.audit.public_dict()
    if final != {
        "selected_count": len(authenticated.selection.selected),
        "selection_sha256": authenticated.selection.selection_sha256,
        "audit": expected_audit,
    }:
        raise ValueError("semantic public selection differs from its authenticated replay")
    replayed_final_store = authenticated.final_store
    expected_final_store = {
        "relative_path": replayed_final_store.path.name,
        "row_count": len(replayed_final_store.rows),
        "manifest_sha256": replayed_final_store.manifest_sha256,
        "private_index_sha256": replayed_final_store.private_index_sha256,
    }
    if final_store != expected_final_store:
        raise ValueError("semantic final-store lineage differs from its authenticated replay")
    if private.get("review_manifest_sha256") != authenticated.review_manifest_sha256:
        raise ValueError("semantic review digest differs from its authenticated replay")
    if private.get("review_page_count") != len(authenticated.review_pages):
        raise ValueError("semantic review page count differs from its authenticated replay")
    for key in (
        "vector_file_sha256",
        "vector_index_file_sha256",
        "private_selection_file_sha256",
        "private_selection_sha256",
        "review_manifest_sha256",
    ):
        _require_sha256(private.get(key), field=f"semantic {key}")
    for key in (
        "inventory_sha256",
        "selection_sha256",
        "manifest_sha256",
        "private_index_sha256",
        "selection_lock_sha256",
    ):
        _require_sha256(source.get(key), field=f"visual source {key}")
    for value, field in (
        (replayed_final_store.manifest_sha256, "final preview manifest_sha256"),
        (replayed_final_store.private_index_sha256, "final preview private_index_sha256"),
        (replayed_final_store.selection_lock_sha256, "final preview selection_lock_sha256"),
    ):
        _require_sha256(value, field=field)
    _require_sha256(public.get("run_sha256"), field="semantic run_sha256")
    _require_sha256(
        truth.get("ledger_sha256_at_selection"),
        field="truth ledger_sha256_at_selection",
    )
    return source, truth, private


def _selected_inventory_rows(
    *,
    snapshot: InventorySnapshot,
    authenticated: AuthenticatedSemanticRefinement,
) -> tuple[tuple[InventoryAsset, ...], list[dict[str, object]], str]:
    if authenticated.universe.source.inventory_sha256 != snapshot.inventory_sha256:
        raise ValueError("semantic source belongs to a different inventory snapshot")
    if authenticated.final_store.inventory_sha256 != snapshot.inventory_sha256:
        raise ValueError("semantic final store belongs to a different inventory snapshot")
    selected = tuple(authenticated.selection.selected)
    final_rows = tuple(authenticated.final_store.rows)
    if len(selected) != 400 or len(final_rows) != 400:
        raise ValueError("fresh certification requires exactly 400 selected previews")
    source_by_asset = {candidate.preview.asset_id: candidate.preview for candidate in selected}
    final_by_asset = {row.asset_id: row for row in final_rows}
    inventory_by_asset = {row.asset_id: row for row in snapshot.rows}
    if len(source_by_asset) != 400 or len(final_by_asset) != 400:
        raise ValueError("fresh certification rows and final previews must be unique")
    if set(source_by_asset) != set(final_by_asset):
        raise ValueError("semantic selection and final preview store contain different assets")

    inventory_rows: list[InventoryAsset] = []
    private_rows: list[dict[str, object]] = []
    image_digest_rows: list[dict[str, str]] = []
    final_root = authenticated.final_store.path.resolve()
    seen_final_audits: set[str] = set()
    seen_image_paths: set[str] = set()
    for asset_id in sorted(source_by_asset):
        source = source_by_asset[asset_id]
        final = final_by_asset[asset_id]
        inventory = inventory_by_asset.get(asset_id)
        if inventory is None:
            raise ValueError("semantic selection contains an asset absent from the inventory")
        source_metadata = (
            source.source_updated,
            source.captured_at,
            source.capture_day,
            source.component_key,
            source.moment_key,
        )
        inventory_metadata = (
            inventory.updated_at,
            inventory.captured_at,
            inventory.capture_day,
            inventory.component_key,
            inventory.moment_key,
        )
        final_metadata = (
            final.source_updated,
            final.captured_at,
            final.capture_day,
            final.component_key,
            final.moment_key,
        )
        if source_metadata != inventory_metadata or final_metadata != inventory_metadata:
            raise ValueError("semantic row metadata differs from the authenticated inventory")
        if source.preview_sha256 != final.preview_sha256:
            raise ValueError("semantic source and final preview bytes have different digests")
        try:
            relative_image = final.image_path.resolve().relative_to(final_root).as_posix()
        except ValueError as error:
            raise ValueError("semantic final preview path escapes its immutable store") from error
        if final.audit_id in seen_final_audits or relative_image in seen_image_paths:
            raise ValueError("semantic final store reuses an audit id or image path")
        seen_final_audits.add(final.audit_id)
        seen_image_paths.add(relative_image)
        inventory_rows.append(inventory)
        private_rows.append(
            {
                "asset_id": asset_id,
                "source_audit_id": source.audit_id,
                "final_audit_id": final.audit_id,
                "source_updated": inventory.updated_at,
                "captured_at": inventory.captured_at,
                "capture_day": inventory.capture_day,
                "component_key": inventory.component_key,
                "moment_key": inventory.moment_key,
                "stratum": inventory.stratum,
                "image_relpath": relative_image,
                "preview_sha256": final.preview_sha256,
                "preview_width": int(final.preview_width),
                "preview_height": int(final.preview_height),
            }
        )
        image_digest_rows.append(
            {
                "final_audit_id": final.audit_id,
                "image_relpath": relative_image,
                "preview_sha256": final.preview_sha256,
            }
        )

    frozen_rows = tuple(inventory_rows)
    if cohort_selection_sha256(frozen_rows) != authenticated.selection.selection_sha256:
        raise ValueError("semantic selection digest does not reproduce from inventory rows")
    if authenticated.final_store.selection_sha256 != authenticated.selection.selection_sha256:
        raise ValueError("semantic final store has a different selection digest")
    if (
        len({row.capture_day for row in frozen_rows}) != 400
        or len({row.component_key for row in frozen_rows}) != 400
        or len({row.moment_key for row in frozen_rows}) != 400
    ):
        raise ValueError("fresh certification requires 400 distinct days, components, and moments")
    image_set_sha256 = _canonical_sha256(
        {"schema": "triage-fresh-cert-image-set-v3", "rows": image_digest_rows}
    )
    return frozen_rows, private_rows, image_set_sha256


def finalize_authenticated_fresh_certification(
    *,
    snapshot: InventorySnapshot,
    authenticated: AuthenticatedSemanticRefinement,
    truth_ledger: TruthReservationLedger,
    review_decision_path: Path,
    approval_dir: Path,
) -> dict[str, object]:
    """Approve and reserve one already-authenticated v3 semantic selection."""
    source_lineage, truth_lineage, private_lineage = _validate_frozen_semantic_protocol(
        authenticated
    )
    selected_rows, certification_rows, image_set_sha256 = _selected_inventory_rows(
        snapshot=snapshot,
        authenticated=authenticated,
    )
    semantic_run_sha256 = str(authenticated.public_manifest["run_sha256"])
    review = load_blinded_review_decision(
        review_decision_path,
        expected_semantic_run_sha256=semantic_run_sha256,
        expected_review_manifest_sha256=authenticated.review_manifest_sha256,
        expected_pages=authenticated.review_pages,
    )

    approval_root = Path(approval_dir).resolve()
    semantic_root = authenticated.output_dir.resolve()
    if (
        approval_root == semantic_root
        or approval_root.is_relative_to(semantic_root)
        or semantic_root.is_relative_to(approval_root)
    ):
        raise ValueError("approval output must be separate from provisional semantic evidence")

    ledger_manifest = truth_ledger.public_manifest()
    already_reserved = any(
        cohort["name"] == FRESH_CERT_COHORT_NAME for cohort in ledger_manifest["cohorts"]
    )
    selection_ledger_sha256 = str(truth_lineage["ledger_sha256_at_selection"])
    ledger_view_sha256 = truth_ledger.ledger_sha256(
        exclude_cohort=FRESH_CERT_COHORT_NAME if already_reserved else None
    )
    if ledger_view_sha256 != selection_ledger_sha256:
        raise RuntimeError("truth ledger changed since semantic cohort selection")
    blocklist = truth_ledger.blocklist(
        exclude_cohort=FRESH_CERT_COHORT_NAME if already_reserved else None
    )
    if (
        {row.asset_id for row in selected_rows} & blocklist.asset_ids
        or {row.component_key for row in selected_rows} & blocklist.component_keys
        or {row.moment_key for row in selected_rows} & blocklist.moment_keys
    ):
        raise RuntimeError("fresh certification overlaps legacy truth evidence")

    certification_index_bytes = _jsonl_bytes(certification_rows)
    certification_index_sha256 = hashlib.sha256(certification_index_bytes).hexdigest()
    if approval_root.exists() and not approval_root.is_dir():
        raise RuntimeError("approval destination exists but is not a directory")
    if approval_root.is_dir():
        allowed_names = {
            "certification-index.jsonl",
            "approval-private.json",
            "approval-public.json",
        }
        unexpected = {path.name for path in approval_root.iterdir()} - allowed_names
        if unexpected:
            raise RuntimeError("approval destination contains an unexpected collision")
        existing_index = approval_root / "certification-index.jsonl"
        if existing_index.exists() and existing_index.read_bytes() != certification_index_bytes:
            raise RuntimeError("approval destination has a different certification index")
        if not already_reserved and any(
            (approval_root / name).exists()
            for name in ("approval-private.json", "approval-public.json")
        ):
            raise RuntimeError("approval destination claims an uncommitted truth reservation")
    approval_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(approval_root, 0o700)
    _write_create_only_idempotent(
        approval_root / "certification-index.jsonl", certification_index_bytes
    )
    ledger_after_sha256 = truth_ledger.reserve_truth_cohort_if_unchanged(
        name=FRESH_CERT_COHORT_NAME,
        inventory_sha256=snapshot.inventory_sha256,
        rows=selected_rows,
        expected_ledger_sha256=selection_ledger_sha256,
    )

    private_manifest: dict[str, object] = {
        "schema": "triage-fresh-certification-approval-private-v3",
        "status": "truth-reserved",
        "cohort_name": FRESH_CERT_COHORT_NAME,
        "inventory_sha256": snapshot.inventory_sha256,
        "selection_sha256": authenticated.selection.selection_sha256,
        "truth_ledger_sha256_at_selection": selection_ledger_sha256,
        "truth_ledger_sha256_after_reservation": ledger_after_sha256,
        "semantic_output_dir": str(semantic_root),
        "semantic_run_sha256": semantic_run_sha256,
        "source_visual_lineage": dict(source_lineage),
        "semantic_private_lineage": {
            key: private_lineage[key]
            for key in (
                "vector_file_sha256",
                "vector_index_file_sha256",
                "private_selection_file_sha256",
                "private_selection_sha256",
                "review_manifest_sha256",
            )
        },
        "final_preview_store": {
            "path": str(authenticated.final_store.path.resolve()),
            "manifest_sha256": authenticated.final_store.manifest_sha256,
            "private_index_sha256": authenticated.final_store.private_index_sha256,
            "selection_lock_sha256": authenticated.final_store.selection_lock_sha256,
            "image_set_sha256": image_set_sha256,
        },
        "review_decision_sha256": review.decision_sha256,
        "certification_index_file": "certification-index.jsonl",
        "certification_index_sha256": certification_index_sha256,
        "selected_count": len(selected_rows),
    }
    private_manifest["approval_private_sha256"] = _canonical_sha256(private_manifest)
    private_bytes = _canonical_bytes(private_manifest)

    public_manifest: dict[str, object] = {
        "schema": "triage-fresh-certification-approval-public-v3",
        "status": "truth-reserved",
        "cohort_name": FRESH_CERT_COHORT_NAME,
        "selected_count": len(selected_rows),
        "distinct_days": len({row.capture_day for row in selected_rows}),
        "distinct_components": len({row.component_key for row in selected_rows}),
        "distinct_moments": len({row.moment_key for row in selected_rows}),
        "inventory_sha256": snapshot.inventory_sha256,
        "selection_sha256": authenticated.selection.selection_sha256,
        "truth_ledger": {
            "sha256_at_selection": selection_ledger_sha256,
            "sha256_after_reservation": ledger_after_sha256,
        },
        "review": {
            "method": REVIEW_METHOD,
            "accept": True,
            "reviewer_count": REQUIRED_REVIEWER_COUNT,
            "review_page_count": review.reviewed_page_count,
            "minimum_page_attestations": REQUIRED_PAGE_ATTESTATIONS,
            "decision_sha256": review.decision_sha256,
            "review_manifest_sha256": review.review_manifest_sha256,
        },
        "source_evidence": {
            "visual_candidate_manifest_sha256": source_lineage["manifest_sha256"],
            "visual_candidate_private_index_sha256": source_lineage["private_index_sha256"],
            "visual_candidate_selection_lock_sha256": source_lineage["selection_lock_sha256"],
            "semantic_run_sha256": semantic_run_sha256,
            "semantic_private_selection_sha256": private_lineage["private_selection_sha256"],
            "semantic_vector_file_sha256": private_lineage["vector_file_sha256"],
            "semantic_vector_index_file_sha256": private_lineage["vector_index_file_sha256"],
        },
        "final_preview_evidence": {
            "manifest_sha256": authenticated.final_store.manifest_sha256,
            "private_index_sha256": authenticated.final_store.private_index_sha256,
            "selection_lock_sha256": authenticated.final_store.selection_lock_sha256,
            "image_set_sha256": image_set_sha256,
        },
        "quality_gates": authenticated.selection.audit.public_dict(),
        "private_evidence": {
            "certification_index_sha256": certification_index_sha256,
            "approval_private_sha256": private_manifest["approval_private_sha256"],
        },
    }
    public_manifest["approval_public_sha256"] = _canonical_sha256(public_manifest)
    public_bytes = _canonical_bytes(public_manifest)
    public_text = public_bytes.decode("utf-8")
    if any(row.asset_id in public_text for row in selected_rows):
        raise AssertionError("public approval leaked a private asset id")

    _write_create_only_idempotent(approval_root / "approval-private.json", private_bytes)
    _write_create_only_idempotent(approval_root / "approval-public.json", public_bytes)
    _fsync_directory(approval_root)
    return public_manifest


def finalize_fresh_location_certification(
    *,
    inventory_dir: Path,
    source_candidate_store: Path,
    semantic_dir: Path,
    truth_ledger_path: Path,
    review_decision_path: Path,
    approval_dir: Path,
) -> dict[str, object]:
    """Authenticate every sealed source before invoking the fixed v3 commit."""
    snapshot = load_inventory_snapshot(inventory_dir)
    truth_ledger = TruthReservationLedger(truth_ledger_path)
    already_reserved = any(
        cohort["name"] == FRESH_CERT_COHORT_NAME
        for cohort in truth_ledger.public_manifest()["cohorts"]
    )
    authenticated = load_authenticated_semantic_refinement(
        semantic_dir,
        source_candidate_store=source_candidate_store,
        truth_ledger=truth_ledger,
        exclude_truth_cohort=FRESH_CERT_COHORT_NAME if already_reserved else None,
    )
    return finalize_authenticated_fresh_certification(
        snapshot=snapshot,
        authenticated=authenticated,
        truth_ledger=truth_ledger,
        review_decision_path=review_decision_path,
        approval_dir=approval_dir,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inventory-dir", type=Path, required=True)
    parser.add_argument("--source-candidate-store", type=Path, required=True)
    parser.add_argument("--semantic-dir", type=Path, required=True)
    parser.add_argument("--truth-ledger", type=Path, required=True)
    parser.add_argument("--review-decision", type=Path, required=True)
    parser.add_argument("--approval-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _pipeline_lock = acquire_recovery_pipeline_lock()
    result = finalize_fresh_location_certification(
        inventory_dir=args.inventory_dir,
        source_candidate_store=args.source_candidate_store,
        semantic_dir=args.semantic_dir,
        truth_ledger_path=args.truth_ledger,
        review_decision_path=args.review_decision,
        approval_dir=args.approval_dir,
    )
    safe_summary = {
        "status": result["status"],
        "cohort_name": result["cohort_name"],
        "selected_count": result["selected_count"],
        "approval_public_sha256": result["approval_public_sha256"],
        "truth_ledger_sha256": result["truth_ledger"]["sha256_after_reservation"],
    }
    print(json.dumps(safe_summary, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via the public main function
    raise SystemExit(main())
