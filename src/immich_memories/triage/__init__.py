"""Per-asset perception at a few milliseconds: frozen DINOv2 features and small heads.

A head emits a fact (``location=outdoor``); it never answers a query and never
removes anything from a candidate set. See docs/research/2026-08-31-triage-heads-architecture.md §6.
"""

from immich_memories.triage.heads import HeadBundle, HeadFact

__all__ = ["HeadBundle", "HeadFact"]
