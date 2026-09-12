"""The matrix's factual screen/document gate, shared with production acquisition."""

from __future__ import annotations

import re

from immich_memories.analysis.annotation_lines import AnnotationLineBatch

SCREEN_DOCUMENT_GATE_VERSION = "screen-document-source-gate-v1"

SCREEN_DOCUMENT_HEAD_LABELS = frozenset(
    {
        "screenshot_from_computer",
        "screenshot_from_manual",
        "table",
        "line_chart",
        "bar_chart",
        "scatter_plot",
        "flow_chart",
        "qr_code",
        "bar_code",
        "calendar",
        "page_thumbnail",
        "full_page_image",
        "logo",
        "signature",
        "engineering_drawing",
    }
)

SCREEN_DOCUMENT_TEXT = re.compile(
    r"\b(smart ?watch|screenshot|screen (displaying|showing)|phone screen|computer screen|"
    r"monitor displaying|app interface|dashboard|television|tv screen|tv show|laptop screen|"
    r"watching (a|the) (tv|screen|television)|projector screen)\b",
    re.IGNORECASE,
)


def screen_document_rejections(batch: AnnotationLineBatch) -> dict[str, str]:
    """Return the established hard source exclusions in stable input order."""
    rejected: dict[str, str] = {}
    for line in batch.lines:
        label = dict(line.heads).get("doc_docling")
        if label in SCREEN_DOCUMENT_HEAD_LABELS:
            rejected[line.asset_id] = f"screen-docling:{label}"
        elif line.description and SCREEN_DOCUMENT_TEXT.search(line.description):
            rejected[line.asset_id] = "screen-text"
    return rejected
