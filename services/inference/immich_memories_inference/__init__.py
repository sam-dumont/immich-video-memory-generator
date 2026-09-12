"""The inference service: the frozen pixel classifiers behind one HTTP port.

Modelled on immich-machine-learning — one image per device variant, weights in a
cache volume, idle unload — and bound by one rule: a fact's identity is the
artifact that produced it, never the machine that ran it.
"""

from immich_memories_inference.app import create_app
from immich_memories_inference.producers import DOC_DOCLING, HEADS, NSFW_MARQO
from immich_memories_inference.settings import InferenceSettings

__all__ = [
    "DOC_DOCLING",
    "HEADS",
    "NSFW_MARQO",
    "InferenceSettings",
    "create_app",
]
