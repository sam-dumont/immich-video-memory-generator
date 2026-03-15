"""Scaling and aspect ratio utilities.

This module provides functions for handling video aspect ratio mismatches,
smart cropping based on face detection, and mood aggregation from clips.
"""

from __future__ import annotations

import logging
import subprocess
from collections import Counter
from pathlib import Path

__all__ = [
    "_get_aspect_ratio_filter",
    "_get_smart_crop_filter",
    "_detect_face_center_in_video",
    "aggregate_mood_from_clips",
]

logger = logging.getLogger(__name__)


def _get_aspect_ratio_filter(
    clip_index: int,
    src_w: int,
    src_h: int,
    target_w: int,
    target_h: int,
    face_center: tuple[float, float] | None,
    pix_fmt: str = "yuv420p",
    target_fps: int = 60,
    rotation_filter: str = "",
    hdr_conversion: str = "",
    colorspace_filter: str = "",
    output_suffix: str = "scaled",
    scale_mode: str = "blur",
) -> str:
    """Generate FFmpeg filter for aspect ratio handling.

    Handles portrait/landscape mismatch with different modes:
    - "blur": Blur background (Instagram-style)
    - "smart_zoom": Smart crop centered on face
    - "black_bars": Simple letterbox/pillarbox with black bars

    Args:
        clip_index: Index of clip (for unique labels)
        src_w: Source video width
        src_h: Source video height
        target_w: Target output width
        target_h: Target output height
        face_center: Tuple of (x, y) normalized 0-1 or None (used for smart_zoom)
        pix_fmt: Pixel format for output
        target_fps: Target frame rate
        rotation_filter: Optional rotation filter prefix (e.g., "transpose=1,")
        hdr_conversion: Optional HDR conversion filter
        colorspace_filter: Optional colorspace filter
        output_suffix: Suffix for output label (default "scaled")
        scale_mode: "blur", "smart_zoom", or "black_bars"

    Returns:
        FFmpeg filter string.
    """
    output_label = f"[v{clip_index}{output_suffix}]"
    common_suffix = f"fps={target_fps},settb=1/{target_fps},format={pix_fmt}{hdr_conversion}{colorspace_filter},setsar=1"

    # Check if aspect ratios are significantly different (>5% difference)
    src_ar = src_w / src_h
    target_ar = target_w / target_h
    ar_diff = abs(src_ar - target_ar) / max(src_ar, target_ar)

    if ar_diff < 0.05:
        # Aspect ratios are similar - simple scale
        return (
            f"[{clip_index}:v]{rotation_filter}setpts=PTS-STARTPTS,"
            f"scale={target_w}:{target_h}:flags=lanczos,"
            f"{common_suffix}{output_label}"
        )

    # Handle different scale modes
    if scale_mode == "black_bars":
        # Simple letterbox/pillarbox with black bars (no blur)
        return (
            f"[{clip_index}:v]{rotation_filter}setpts=PTS-STARTPTS,"
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
            f"{common_suffix}{output_label}"
        )

    if scale_mode == "smart_zoom" and face_center is not None:
        # Smart crop centered on face
        crop_filter = _get_smart_crop_filter(
            src_w, src_h, target_w, target_h, face_center[0], face_center[1]
        )
        return (
            f"[{clip_index}:v]{rotation_filter}setpts=PTS-STARTPTS,"
            f"{crop_filter},"
            f"{common_suffix}{output_label}"
        )

    # Default: Blur background (Instagram-style)
    # Also used when smart_zoom is selected but no face is found
    bg_label = f"bg{clip_index}"
    fg_label = f"fg{clip_index}"
    blurred_label = f"blurred{clip_index}"
    scaled_label = f"scaled{clip_index}"

    return (
        f"[{clip_index}:v]{rotation_filter}setpts=PTS-STARTPTS,split[{bg_label}][{fg_label}];"
        f"[{bg_label}]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h},boxblur=luma_radius=150:chroma_radius=150:luma_power=3:chroma_power=3[{blurred_label}];"
        f"[{fg_label}]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:flags=lanczos[{scaled_label}];"
        f"[{blurred_label}][{scaled_label}]overlay=(W-w)/2:(H-h)/2,"
        f"{common_suffix}{output_label}"
    )


def _get_smart_crop_filter(
    src_w: int,
    src_h: int,
    target_w: int,
    target_h: int,
    face_center_x: float,
    face_center_y: float,
) -> str:
    """Generate FFmpeg crop filter centered on face position.

    Calculates the optimal crop region that:
    1. Maintains the target aspect ratio
    2. Centers on the detected face position
    3. Stays within source video bounds

    Args:
        src_w: Source video width
        src_h: Source video height
        target_w: Target output width
        target_h: Target output height
        face_center_x: Face center X position (normalized 0-1, left to right)
        face_center_y: Face center Y position (normalized 0-1, top to bottom)

    Returns:
        FFmpeg filter string for crop and scale (e.g., "crop=1080:1920:100:50,scale=1080:1920")
    """
    target_ar = target_w / target_h
    src_ar = src_w / src_h

    # Calculate crop dimensions to match target aspect ratio
    if src_ar > target_ar:
        # Source is wider - crop width
        crop_h = src_h
        crop_w = int(src_h * target_ar)
    else:
        # Source is taller - crop height
        crop_w = src_w
        crop_h = int(src_w / target_ar)

    # Convert normalized face position to pixels
    face_x = int(face_center_x * src_w)
    face_y = int(face_center_y * src_h)

    # Center crop on face with bounds checking
    crop_x = max(0, min(face_x - crop_w // 2, src_w - crop_w))
    crop_y = max(0, min(face_y - crop_h // 2, src_h - crop_h))

    return f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={target_w}:{target_h}:flags=lanczos"


def _detect_face_center_in_video(video_path: Path) -> tuple[float, float] | None:
    """Detect average face center position in a video.

    Samples multiple frames throughout the video and detects faces in each.
    Returns the average face position if faces are found, otherwise None.

    Uses Apple Vision Framework on macOS (GPU-accelerated) with OpenCV fallback.

    Args:
        video_path: Path to video file

    Returns:
        Tuple of (center_x, center_y) in normalized 0-1 coordinates, or None if no faces found.
        Coordinates are: X from left (0) to right (1), Y from top (0) to bottom (1).
    """
    import tempfile

    try:
        # Try Apple Vision first (GPU-accelerated on macOS)
        from immich_memories.analysis.apple_vision import VisionFaceDetector

        detector = VisionFaceDetector()
    except ImportError:
        # Fallback to OpenCV
        try:
            import cv2  # noqa: F401

            detector = None  # Will use OpenCV directly
        except ImportError:
            logger.debug("No face detection available (Apple Vision or OpenCV)")
            return None

    # Get video duration
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
        )
        duration = float(result.stdout.strip())
    except (ValueError, subprocess.SubprocessError):
        duration = 10.0  # Default fallback

    # Sample 3 frames at 20%, 50%, 80% through video
    sample_times = [duration * p for p in (0.2, 0.5, 0.8)]
    all_face_positions: list[tuple[float, float]] = []

    for sample_time in sample_times:
        try:
            # Extract frame to temp file
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                frame_path = Path(tmp.name)

            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(sample_time),
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    str(frame_path),
                ],
                capture_output=True,
                timeout=30,
            )

            if not frame_path.exists():
                continue

            if detector is not None:
                # Apple Vision
                import cv2

                _img = cv2.imread(str(frame_path))
                if _img is None:
                    continue
                faces = detector.detect_faces(_img)
                for face in faces:
                    # Apple Vision returns bounding box with origin at bottom-left
                    # Convert to top-left origin normalized coordinates
                    center_x = face.bounds.x + face.bounds.width / 2
                    center_y = 1.0 - (face.bounds.y + face.bounds.height / 2)  # Flip Y
                    all_face_positions.append((center_x, center_y))
            else:
                # OpenCV fallback
                import cv2

                img = cv2.imread(str(frame_path))
                if img is not None:
                    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                    face_cascade = cv2.CascadeClassifier(
                        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                    )
                    faces_cv = face_cascade.detectMultiScale(gray, 1.1, 4)
                    h, w = img.shape[:2]
                    for x, y, fw, fh in faces_cv:
                        center_x = (x + fw / 2) / w
                        center_y = (y + fh / 2) / h
                        all_face_positions.append((center_x, center_y))

            # Cleanup
            frame_path.unlink(missing_ok=True)

        except Exception as e:
            logger.debug(f"Face detection failed for frame at {sample_time}s: {e}")
            continue

    if not all_face_positions:
        return None

    # Average all face positions
    avg_x = sum(p[0] for p in all_face_positions) / len(all_face_positions)
    avg_y = sum(p[1] for p in all_face_positions) / len(all_face_positions)

    logger.debug(
        f"Detected {len(all_face_positions)} faces, average center: ({avg_x:.2f}, {avg_y:.2f})"
    )
    return (avg_x, avg_y)


def aggregate_mood_from_clips(clips: list) -> str | None:
    """Aggregate the dominant mood from a list of clips.

    Analyzes the llm_emotion from each clip and returns the most common mood.
    Maps various emotion labels to the mood categories used by title screens.

    Args:
        clips: List of clips with llm_emotion attributes.

    Returns:
        Dominant mood string (e.g., "happy", "calm") or None if no emotions found.
    """
    # Mapping from LLM emotion labels to title screen mood categories
    emotion_to_mood = {
        # Happy family
        "happy": "happy",
        "joyful": "happy",
        "cheerful": "happy",
        "delighted": "happy",
        "content": "happy",
        # Calm family
        "calm": "calm",
        "serene": "calm",
        "relaxed": "calm",
        "tranquil": "calm",
        # Energetic family
        "energetic": "energetic",
        "excited": "energetic",
        "enthusiastic": "energetic",
        "dynamic": "energetic",
        # Playful family
        "playful": "playful",
        "fun": "playful",
        "silly": "playful",
        "mischievous": "playful",
        # Nostalgic family
        "nostalgic": "nostalgic",
        "sentimental": "nostalgic",
        "wistful": "nostalgic",
        # Romantic family
        "romantic": "romantic",
        "loving": "romantic",
        "tender": "romantic",
        "affectionate": "romantic",
        # Peaceful family
        "peaceful": "peaceful",
        "quiet": "peaceful",
        "gentle": "peaceful",
        # Exciting family
        "exciting": "exciting",
        "thrilling": "exciting",
        "adventurous": "exciting",
    }

    # Collect emotions from clips
    emotions = []
    for clip in clips:
        emotion = getattr(clip, "llm_emotion", None)
        if emotion:
            # Normalize and map to mood category
            emotion_lower = emotion.lower().strip()
            mood = emotion_to_mood.get(emotion_lower, emotion_lower)
            emotions.append(mood)

    if not emotions:
        return None

    # Count and return most common
    mood_counts = Counter(emotions)
    dominant_mood, count = mood_counts.most_common(1)[0]

    logger.info(f"Dominant mood from {len(emotions)} clips: {dominant_mood} ({count} occurrences)")
    return dominant_mood
