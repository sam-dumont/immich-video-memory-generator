#!/usr/bin/env python3
"""Replay, pin, and render the failed Slice-1 certification cohort."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from .calibrate_certify import (
    DECIDABLE_CLASSES,
    DEFAULT_ARTIFACT_DIR,
    DEFAULT_TRUTH_DIR,
    OperatingPoint,
    _acquire_artifact_lock,
    _certification_inputs_sha256,
    _pack_snapshot_sha256,
    _snapshot_certification_inputs,
    certification_evidence_sha256,
    decide_probabilities,
    load_certification_rows_bytes,
    score_decisions,
    softmax_temperature,
)
from .embed import EmbeddingCache
from .train import (
    load_linear_artifact,
    load_mlp_artifact,
    load_pca_artifact,
    project_deployment_features,
    snapshot_training_bundle,
)

DEFAULT_EVIDENCE_DIR = DEFAULT_ARTIFACT_DIR / "recovery-1b" / "retired-cert-failures-v1"
DEFAULT_SHEET_DIR = DEFAULT_ARTIFACT_DIR / "recovery-1b" / "failure-contact-sheets-v1"
BLIND_CATEGORIES = frozenset(
    {
        "clear_indoor",
        "clear_outdoor_built",
        "clear_outdoor_nature",
        "clear_outdoor_twilight",
        "mixed_doorway",
        "sheltered_boundary",
        "stage_ambiguous",
        "unresolved_closeup",
    }
)


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_create_only(path: Path, payload: bytes) -> None:
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
        os.link(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _write_create_only_idempotent(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"existing {path.name} has different immutable content")
        return
    _write_create_only(path, payload)


def _assert_metric_replay(observed: dict[str, Any], expected: dict[str, Any]) -> None:
    for key in ("n_total", "n_covered"):
        if observed[key] != expected[key]:
            raise ValueError(f"replayed certification metric differs: {key}")
    for key in ("accuracy_on_covered", "coverage", "emitted_undetermined_rate"):
        if not np.isclose(observed[key], expected[key], rtol=0, atol=1e-12):
            raise ValueError(f"replayed certification metric differs: {key}")


def build_failure_rows(
    certification_rows: list[Any],
    probabilities: np.ndarray,
    classes: tuple[str, ...],
    operating_point: OperatingPoint,
    preview_sha256_by_asset: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return every abstention and covered error with its exact gate reason."""
    array = np.asarray(probabilities, dtype=np.float32)
    decisions = decide_probabilities(array, classes, operating_point)
    if len(certification_rows) != len(array):
        raise ValueError("certification rows and probabilities do not align")
    class_index = {label: index for index, label in enumerate(classes)}
    decidable_indices = np.asarray([class_index[label] for label in DECIDABLE_CLASSES])
    winning_indices = decidable_indices[np.argmax(array[:, decidable_indices], axis=1)]
    raw_labels = np.asarray(classes)[winning_indices]

    failures: list[dict[str, Any]] = []
    for index, row in enumerate(certification_rows):
        covered = bool(decisions.covered[index])
        emitted = str(decisions.labels[index])
        if covered and emitted == row.location:
            continue
        failure_kind = "covered_error" if covered else "abstention"
        raw_label = str(raw_labels[index])
        confidence = float(decisions.confidence[index])
        failed_gates: list[str] = []
        if not covered:
            if confidence < operating_point.confidence_thresholds[raw_label]:
                failed_gates.append("confidence")
            if (
                float(array[index, class_index["undetermined"]])
                >= operating_point.undetermined_threshold
            ):
                failed_gates.append("undetermined_probability")
            if not failed_gates:
                raise AssertionError("an abstention must fail at least one calibrated gate")
        image_path = Path(row.image_path)
        observed_sha256 = _file_sha256(image_path)
        expected_sha256 = preview_sha256_by_asset.get(row.asset_id)
        if expected_sha256 is None or observed_sha256 != expected_sha256:
            raise ValueError("certification image bytes differ from the sealed embedding lineage")
        failures.append(
            {
                "asset_id": row.asset_id,
                "source_image_path": str(image_path),
                "preview_sha256": observed_sha256,
                "truth": row.location,
                "teacher": row.teacher_location,
                "emitted": emitted,
                "raw_prediction": raw_label,
                "confidence": confidence,
                "probabilities": {
                    label: float(array[index, class_index[label]]) for label in classes
                },
                "covered": covered,
                "failure_kind": failure_kind,
                "failed_gates": failed_gates,
            }
        )

    failures.sort(key=lambda item: (item["failure_kind"], item["asset_id"]))
    for index, row in enumerate(failures, start=1):
        row["audit_id"] = f"A{index:03d}"
        row["image_relpath"] = f"images/{row['audit_id']}.jpg"

    gate_counts = Counter(gate for row in failures for gate in row["failed_gates"])
    summary = {
        "row_count": len(failures),
        "failure_kinds": dict(sorted(Counter(row["failure_kind"] for row in failures).items())),
        "truth": dict(sorted(Counter(row["truth"] for row in failures).items())),
        "raw_predictions": dict(sorted(Counter(row["raw_prediction"] for row in failures).items())),
        "failed_gates": dict(sorted(gate_counts.items())),
        "failed_both_gates": sum(len(row["failed_gates"]) == 2 for row in failures),
    }
    if sum(summary["failure_kinds"].values()) != len(failures) or sum(gate_counts.values()) < 1:
        raise AssertionError("failure summary is internally inconsistent")
    return failures, summary


def pin_failure_evidence(
    destination: Path,
    rows: list[dict[str, Any]],
    *,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Publish immutable private image bytes and a create-only manifest."""
    manifest_rows = [
        {key: value for key, value in row.items() if key not in {"asset_id", "source_image_path"}}
        for row in rows
    ]
    private_index = {
        "schema_version": "triage-private-audit-index-v1",
        "rows": [
            {
                "audit_id": row["audit_id"],
                "asset_id": row["asset_id"],
                "source_image_path": row["source_image_path"],
                "preview_sha256": row["preview_sha256"],
            }
            for row in rows
        ],
    }
    private_index_bytes = (
        json.dumps(private_index, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    manifest: dict[str, Any] = {
        "schema_version": "triage-location-failure-audit-v1",
        "privacy": "private owner images; local only",
        "status": "retired certification; development evidence only",
        "private_index_sha256": hashlib.sha256(private_index_bytes).hexdigest(),
        **metadata,
        "rows": manifest_rows,
    }
    manifest["manifest_sha256"] = _canonical_sha256(manifest)
    manifest_bytes = (
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    manifest_path = destination / "manifest.json"
    private_index_path = destination / "private-index.json"
    if destination.exists():
        if not manifest_path.is_file() or not private_index_path.is_file():
            raise RuntimeError("existing evidence directory has no committed manifest/index")
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing != manifest:
            raise RuntimeError("existing evidence directory has different immutable content")
        if (
            hashlib.sha256(private_index_path.read_bytes()).hexdigest()
            != manifest["private_index_sha256"]
        ):
            raise RuntimeError("private audit index failed its manifest digest")
        for row in manifest_rows:
            image_path = destination / row["image_relpath"]
            if _file_sha256(image_path) != row["preview_sha256"]:
                raise RuntimeError("pinned evidence image failed its manifest digest")
        return manifest

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(mode=0o700)
    destination.chmod(0o700)
    images_dir = destination / "images"
    images_dir.mkdir(mode=0o700)
    images_dir.chmod(0o700)
    _fsync_directory(destination.parent)
    try:
        source_by_audit_id = {row["audit_id"]: Path(row["source_image_path"]) for row in rows}
        for row in manifest_rows:
            target = destination / row["image_relpath"]
            source = source_by_audit_id[row["audit_id"]]
            with source.open("rb") as input_handle:
                payload = input_handle.read()
            if hashlib.sha256(payload).hexdigest() != row["preview_sha256"]:
                raise RuntimeError("source image changed while pinning evidence")
            _write_create_only(target, payload)
        _write_create_only(private_index_path, private_index_bytes)
        _write_create_only(manifest_path, manifest_bytes)
        _fsync_directory(destination)
    except Exception:
        # No manifest means the directory is visibly uncommitted. Preserve its
        # bytes for forensic recovery rather than deleting private evidence.
        raise
    return manifest


def render_contact_sheets(
    evidence_dir: Path,
    output_dir: Path,
    *,
    columns: int = 3,
    rows_per_sheet: int = 2,
    reveal_labels: bool = False,
) -> int:
    """Render lossless-aspect audit sheets from an immutable evidence manifest."""
    if columns < 1 or rows_per_sheet < 1:
        raise ValueError("contact-sheet dimensions must be positive")
    manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))
    rows = sorted(
        manifest["rows"],
        key=lambda row: hashlib.sha256(
            f"{manifest['manifest_sha256']}:{row['audit_id']}".encode("ascii")
        ).hexdigest(),
    )
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
    cell_width, image_height, caption_height = 420, 315, 72
    page_size = columns * rows_per_sheet
    for page_index, start in enumerate(range(0, len(rows), page_size), start=1):
        page_rows = rows[start : start + page_size]
        sheet = Image.new(
            "RGB",
            (columns * cell_width, rows_per_sheet * (image_height + caption_height)),
            "white",
        )
        draw = ImageDraw.Draw(sheet)
        for local_index, row in enumerate(page_rows):
            column = local_index % columns
            row_index = local_index // columns
            x = column * cell_width
            y = row_index * (image_height + caption_height)
            with Image.open(evidence_dir / row["image_relpath"]) as source:
                image = ImageOps.contain(source.convert("RGB"), (cell_width, image_height))
            image_x = x + (cell_width - image.width) // 2
            image_y = y + (image_height - image.height) // 2
            sheet.paste(image, (image_x, image_y))
            caption = row["audit_id"]
            if reveal_labels:
                gates = "+".join(row["failed_gates"]) or "covered-error"
                caption = (
                    f"{row['audit_id']} | {row['failure_kind']} | truth={row['truth']}\n"
                    f"raw={row['raw_prediction']} conf={row['confidence']:.3f} | {gates}"
                )
            draw.multiline_text((x + 6, y + image_height + 5), caption, fill="black", spacing=3)
        destination = output_dir / f"failures-{page_index:02d}.png"
        temporary = destination.with_suffix(".tmp.png")
        sheet.save(temporary, format="PNG", optimize=True)
        temporary.replace(destination)
        destination.chmod(0o600)
    return (len(rows) + page_size - 1) // page_size


def seal_blind_annotations(evidence_dir: Path, annotations_path: Path) -> dict[str, Any]:
    """Bind a pixels-only review to the audit manifest, then reveal aggregate strata."""
    manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))
    submitted = json.loads(annotations_path.read_text(encoding="utf-8"))
    categories = submitted.get("categories")
    if not isinstance(categories, dict):
        raise ValueError("blind annotations must contain a category mapping")
    expected_ids = {str(row["audit_id"]) for row in manifest["rows"]}
    if set(categories) != expected_ids:
        raise ValueError("blind annotations must cover every audit id exactly once")
    unknown_categories = set(map(str, categories.values())) - BLIND_CATEGORIES
    if unknown_categories:
        raise ValueError(f"unknown blind categories: {sorted(unknown_categories)}")
    sealed: dict[str, Any] = {
        "schema_version": "triage-location-blind-scene-audit-v1",
        "source_manifest_sha256": manifest["manifest_sha256"],
        "review_protocol": str(submitted.get("review_protocol", "")),
        "categories": {key: str(categories[key]) for key in sorted(categories)},
    }
    sealed["annotations_sha256"] = _canonical_sha256(sealed)
    annotation_bytes = (
        json.dumps(sealed, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    _write_create_only_idempotent(evidence_dir / "blind-annotations.json", annotation_bytes)

    rows = [
        {**row, "blind_category": sealed["categories"][row["audit_id"]]} for row in manifest["rows"]
    ]
    category_counts = Counter(row["blind_category"] for row in rows)
    summary: dict[str, Any] = {
        "schema_version": "triage-location-blind-scene-summary-v1",
        "source_manifest_sha256": manifest["manifest_sha256"],
        "annotations_sha256": sealed["annotations_sha256"],
        "row_count": len(rows),
        "category_counts": dict(sorted(category_counts.items())),
        "clear_scene_count": sum(
            count for category, count in category_counts.items() if category.startswith("clear_")
        ),
        "ambiguous_or_boundary_count": sum(
            count
            for category, count in category_counts.items()
            if not category.startswith("clear_")
        ),
        "failure_kind_by_category": {
            category: dict(
                sorted(
                    Counter(
                        row["failure_kind"] for row in rows if row["blind_category"] == category
                    ).items()
                )
            )
            for category in sorted(category_counts)
        },
        "truth_by_category": {
            category: dict(
                sorted(
                    Counter(
                        row["truth"] for row in rows if row["blind_category"] == category
                    ).items()
                )
            )
            for category in sorted(category_counts)
        },
        "gate_reason_by_category": {
            category: dict(
                sorted(
                    Counter(
                        gate
                        for row in rows
                        if row["blind_category"] == category
                        for gate in (row["failed_gates"] or ["covered_error"])
                    ).items()
                )
            )
            for category in sorted(category_counts)
        },
    }
    summary["summary_sha256"] = _canonical_sha256(summary)
    summary_bytes = (
        json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    _write_create_only_idempotent(evidence_dir / "blind-audit-summary.json", summary_bytes)
    return summary


def replay_failures(
    artifact_dir: Path,
    truth_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Replay the exact certified candidate and return its failure cohort."""
    certification_report = json.loads(
        (artifact_dir / "certification-metrics.json").read_text(encoding="utf-8")
    )
    if certification_evidence_sha256(certification_report) != certification_report.get(
        "certification_evidence_sha256"
    ):
        raise ValueError("certification evidence digest does not reproduce")
    training_bundle = snapshot_training_bundle(artifact_dir)
    train_report = training_bundle.report
    selected = str(certification_report["selected_candidate"])
    training_run_sha256 = training_bundle.training_run_sha256
    if training_run_sha256 != certification_report["training_run_sha256"]:
        raise ValueError("certification and training identities differ")
    component_bytes = training_bundle.component_bytes
    certification_inputs = _snapshot_certification_inputs(truth_dir)
    if (
        _certification_inputs_sha256(certification_inputs)
        != certification_report["certification_inputs_sha256"]
    ):
        raise ValueError("certification truth inputs changed after the sealed run")
    certification_rows, missing_images = load_certification_rows_bytes(
        certification_inputs["battery_key.json"],
        {
            name: payload
            for name, payload in certification_inputs.items()
            if name.startswith("battery_shards/")
        },
        certification_inputs["review_data.json"],
    )
    if missing_images != certification_report["missing_certification_images"]:
        raise ValueError("certification image availability changed after the sealed run")

    cache = EmbeddingCache(artifact_dir / "embeddings.db")
    staging_encoder_key = str(train_report["staging_encoder_key"])
    snapshot = cache.staging_snapshot(
        [row.asset_id for row in certification_rows], staging_encoder_key
    )
    cohort_sha256 = _pack_snapshot_sha256(
        snapshot,
        metadata_by_asset={
            row.asset_id: {
                "truth_location": row.location,
                "teacher_location": row.teacher_location,
            }
            for row in certification_rows
        },
    )
    if cohort_sha256 != certification_report["certification_cohort_sha256"]:
        raise ValueError("certification embedding cohort changed after the sealed run")
    pca = load_pca_artifact(io.BytesIO(component_bytes["pca_artifact"]))
    features = project_deployment_features(
        pca,
        np.stack([snapshot[row.asset_id][0] for row in certification_rows]),
    )
    loaders = {"linear": load_linear_artifact, "mlp": load_mlp_artifact}
    if selected not in loaders:
        raise ValueError(f"unsupported selected candidate: {selected}")
    model = loaders[selected](io.BytesIO(component_bytes[f"{selected}_candidate"]))
    candidate_report = certification_report["candidates"][selected]
    operating_payload = candidate_report["operating_point"]
    operating_point = OperatingPoint(
        confidence_thresholds={
            key: float(value) for key, value in operating_payload["confidence_thresholds"].items()
        },
        undetermined_threshold=float(operating_payload["undetermined_threshold"]),
    )
    probabilities = softmax_temperature(
        model.logits(features), float(candidate_report["temperature"])
    )
    decisions = decide_probabilities(probabilities, model.classes, operating_point)
    observed_metrics = score_decisions(
        np.asarray([row.location for row in certification_rows]), decisions
    )
    _assert_metric_replay(observed_metrics, candidate_report["certification"])
    failures, summary = build_failure_rows(
        certification_rows,
        probabilities,
        model.classes,
        operating_point,
        {asset_id: values[1] for asset_id, values in snapshot.items()},
    )
    metadata = {
        "source_certification_evidence_sha256": certification_report[
            "certification_evidence_sha256"
        ],
        "source_certification_cohort_sha256": cohort_sha256,
        "source_training_run_sha256": training_run_sha256,
        "selected_candidate": selected,
        "certification_total": len(certification_rows),
        "summary": summary,
    }
    return failures, summary, metadata


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--truth-dir", type=Path, default=DEFAULT_TRUTH_DIR)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    parser.add_argument("--sheet-dir", type=Path, default=DEFAULT_SHEET_DIR)
    parser.add_argument("--annotations", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        pipeline_lock = _acquire_artifact_lock(
            args.artifact_dir,
            cache_db=args.artifact_dir / "embeddings.db",
            shared_root=DEFAULT_ARTIFACT_DIR,
        )
    except RuntimeError as error:
        raise SystemExit(f"STOP: {error}") from error
    try:
        failures, summary, metadata = replay_failures(args.artifact_dir, args.truth_dir)
        manifest = pin_failure_evidence(args.evidence_dir, failures, metadata=metadata)
        sheet_count = render_contact_sheets(args.evidence_dir, args.sheet_dir)
        annotation_summary = (
            seal_blind_annotations(args.evidence_dir, args.annotations)
            if args.annotations is not None
            else None
        )
    finally:
        pipeline_lock.close()
    print(
        "failure audit replayed: "
        f"abstentions={summary['failure_kinds'].get('abstention', 0)} "
        f"covered_errors={summary['failure_kinds'].get('covered_error', 0)} "
        f"manifest={manifest['manifest_sha256']} sheets={sheet_count}",
        flush=True,
    )
    if annotation_summary is not None:
        print(
            "blind audit sealed: "
            f"clear={annotation_summary['clear_scene_count']} "
            f"ambiguous_or_boundary={annotation_summary['ambiguous_or_boundary_count']} "
            f"summary={annotation_summary['summary_sha256']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
