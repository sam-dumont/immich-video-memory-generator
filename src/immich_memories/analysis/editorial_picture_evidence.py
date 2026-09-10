"""Selected-picture observations over an immutable captured annotation snapshot."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from immich_memories.analysis.annotation_lines import AssetAnnotationLine
from immich_memories.analysis.editorial_shareability import (
    _fallback_audience_annotation,
    _visual_body_observation,
)


class PictureEvidenceOverlay:
    """Acquire each material member once without changing discovery or ladder inputs."""

    def __init__(
        self,
        annotations: Mapping[str, AssetAnnotationLine],
        lines: Mapping[str, str],
        observe: Callable[[str], Mapping[str, Any]] | None,
    ) -> None:
        self.annotations = dict(annotations)
        self._lines = lines
        self._observe = observe
        self.records: dict[str, dict[str, Any]] = {}

    @staticmethod
    def material_members(unit: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(str(i) for i in (unit.get("asset_id"), *unit.get("members", ())) if i)
        )

    def enrich(self, unit: dict[str, Any], *, stop_on_body_yes: bool = False) -> str | None:
        """Return a bound positive witness only when the caller can reject the whole unit."""
        if self._observe is None:
            return None
        witness = None
        material = self.material_members(unit)
        if self._has_captioned_companion(unit, material):
            stop_on_body_yes = False
        for asset_id in material:
            if asset_id not in self.records:
                self._acquire(asset_id, self._observe)
            uncovered, stop_on_body_yes = self._body_witness(asset_id, stop_on_body_yes)
            if uncovered:
                witness = asset_id
                break
        # Completion captures this field before its audience callback. Update that
        # same candidate so the subsequent contribution decision sees current facts.
        unit["line"] = self.line(unit)
        return witness

    def _has_captioned_companion(self, unit: Mapping[str, Any], material: Sequence[str]) -> bool:
        """A separately captioned video companion keeps the legacy audience route.

        The material-only image observer cannot establish that companion's body facts.
        """
        captions = []
        for companion in set(unit.get("video_ids", ())) - set(material):
            annotation = self.annotations.get(companion)
            captions.append(
                annotation.description
                if annotation is not None
                else _fallback_audience_annotation(self._lines.get(companion, ""))[0]
            )
        return any(caption and caption.strip() for caption in captions)

    def _body_witness(self, asset_id: str, stop_on_body_yes: bool) -> tuple[bool, bool]:
        """Whether this record witnesses an uncovered person, and whether to keep looking."""
        if not stop_on_body_yes:
            return False, False
        record = self.records[asset_id]
        body = _visual_body_observation(record)
        if body is None:
            return False, record.get("status") != "available"
        return body["uncovered_person"] == "yes", body["uncovered_person"] != "invalid"

    def _acquire(self, asset_id: str, observe: Callable[[str], Mapping[str, Any]]) -> None:
        original = self.annotations.get(asset_id)
        original_line = self._lines.get(asset_id, "")
        description, heads = (
            (original.description, original.heads)
            if original is not None
            else _fallback_audience_annotation(original_line)
        )
        observation = dict(observe(asset_id))
        observed = observation.get("description")
        available = (
            observation.get("status") == "available"
            and isinstance(observed, str)
            and bool(observed.strip())
        )
        selected_description = observed if available else None
        # Setting came from the same compact caption producer. Retain external
        # source metadata and warnings, but do not duplicate superseded prose.
        metadata = [
            part
            for part in original_line.split(" | ")
            if part and part != description and not part.startswith("setting:")
        ]
        selected_line = " | ".join(
            [
                *metadata,
                (
                    f"picture observations: {selected_description}"
                    if available
                    else "picture observations unavailable"
                ),
            ]
        )
        self.annotations[asset_id] = AssetAnnotationLine(
            asset_id=asset_id,
            text=selected_line,
            description=selected_description,
            heads=heads,
            stitching_burst_id=original.stitching_burst_id if original else None,
        )
        self.records[asset_id] = observation | {
            "original_caption": description,
            "original_line": original_line,
            "evidence_scope": "cached preview only; no unsampled motion claims",
        }

    def line(self, unit: Mapping[str, Any]) -> str:
        if self._observe is None:
            return self._lines.get(str(unit["asset_id"]), "")
        members = self.material_members(unit)
        rows = [
            self.annotations[i].text if i in self.records else self._lines.get(i, "")
            for i in members
        ]
        if len(rows) == 1:
            return rows[0]
        return "\n".join(f"Material picture p{index + 1}: {row}" for index, row in enumerate(rows))
