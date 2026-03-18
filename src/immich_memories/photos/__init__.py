"""Photo support — converts still images to animated video clips."""

from immich_memories.photos.animator import detect_photo_hdr_type
from immich_memories.photos.grouper import PhotoGrouper
from immich_memories.photos.models import AnimationMode, PhotoClipInfo, PhotoGroup
from immich_memories.photos.renderer import (
    KenBurnsParams,
    face_aware_pan,
    render_collage,
    render_ken_burns,
    render_slide_in,
    render_split,
)
from immich_memories.photos.scoring import score_photo

__all__ = [
    "AnimationMode",
    "PhotoClipInfo",
    "PhotoGroup",
    "PhotoGrouper",
    "detect_photo_hdr_type",
    "score_photo",
    "KenBurnsParams",
    "face_aware_pan",
    "render_ken_burns",
    "render_slide_in",
    "render_collage",
    "render_split",
]
