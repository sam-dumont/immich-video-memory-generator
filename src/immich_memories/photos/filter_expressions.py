"""Pure functions returning FFmpeg filter strings for photo animations.

Each function takes photo/target dimensions and animation parameters,
returning a complete FFmpeg filter graph string. No I/O — just math and strings.

Every function accepts an optional `seed` for reproducible randomness —
each photo gets a unique pan direction, zoom start point, etc.
"""

from __future__ import annotations

import random

# Pan directions for Ken Burns: (start_offset_x, start_offset_y) normalized
# Each direction gives a distinct camera movement feel
_PAN_DIRECTIONS = [
    (0.0, 0.0),  # center → center (pure zoom)
    (-0.3, 0.0),  # left → center
    (0.3, 0.0),  # right → center
    (0.0, -0.2),  # top → center
    (0.0, 0.2),  # bottom → center
    (-0.2, -0.15),  # top-left → center
    (0.2, -0.15),  # top-right → center
    (-0.2, 0.15),  # bottom-left → center
    (0.2, 0.15),  # bottom-right → center
]


def ken_burns_filter(
    width: int,
    height: int,
    target_w: int,
    target_h: int,
    duration: float,
    fps: int,
    zoom_factor: float = 1.15,
    face_center: tuple[float, float] | None = None,
    seed: int | None = None,
) -> str:
    """Generate a Ken Burns (slow zoom + pan) filter.

    Scales the source image up, then uses zoompan to animate from 100%
    to zoom_factor over the clip duration. Pan direction is randomized
    per photo via seed. If face_center is provided, pans toward the face
    instead.
    """
    total_frames = int(fps * duration)
    zoom_step = (zoom_factor - 1.0) / total_frames

    scale_w = int(target_w * zoom_factor * 1.1)
    scale_expr = f"scale={scale_w}:-1"

    if face_center:
        fx, fy = face_center
        x_expr = f"iw/2-(iw/zoom/2)+({fx}-0.5)*(iw/zoom)*0.3"
        y_expr = f"ih/2-(ih/zoom/2)+({fy}-0.5)*(ih/zoom)*0.3"
    else:
        # Pick a random pan direction for variety
        rng = random.Random(seed)
        dx, dy = rng.choice(_PAN_DIRECTIONS)

        if dx == dy == 0.0:
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"
        else:
            # Pan from offset position toward center over the duration
            # on/N decreases from 1→0 over N frames, creating smooth movement
            x_expr = f"iw/2-(iw/zoom/2)+{dx}*(iw/zoom)*(1-on/{total_frames})"
            y_expr = f"ih/2-(ih/zoom/2)+{dy}*(ih/zoom)*(1-on/{total_frames})"

    zoompan = (
        f"zoompan=z='min(zoom+{zoom_step:.6f},{zoom_factor})':"
        f"x='{x_expr}':"
        f"y='{y_expr}':"
        f"d={total_frames}:s={target_w}x{target_h}:fps={fps}"
    )

    return f"{scale_expr},{zoompan}"


def face_zoom_filter(
    width: int,
    height: int,
    target_w: int,
    target_h: int,
    duration: float,
    fps: int,
    face_bbox: tuple[float, float, float, float] = (0.3, 0.2, 0.4, 0.5),
    seed: int | None = None,
) -> str:
    """Generate a face zoom filter (crop to face region + gentle zoom).

    face_bbox is (x, y, w, h) normalized 0-1. The crop region is 1.5x the
    face bounding box, then a gentle 10% zoom is applied. Zoom direction
    is randomized via seed — either zoom in or zoom out.
    """
    fx, fy, fw, fh = face_bbox

    # Expand face bbox by 1.5× for breathing room
    expand = 1.5
    region_w = fw * expand
    region_h = fh * expand
    region_cx = fx + fw / 2
    region_cy = fy + fh / 2

    # Ensure crop region maintains target aspect ratio
    target_ar = target_w / target_h
    if region_w / max(region_h, 0.001) > target_ar:
        region_h = region_w / target_ar
    else:
        region_w = region_h * target_ar

    # Convert to pixel coordinates, clamped to image bounds
    crop_w = max(1, min(int(region_w * width), width))
    crop_h = max(1, min(int(region_h * height), height))
    crop_x = max(0, min(int((region_cx - region_w / 2) * width), width - crop_w))
    crop_y = max(0, min(int((region_cy - region_h / 2) * height), height - crop_h))

    total_frames = int(fps * duration)
    zoom_amount = 0.1

    # Randomize zoom direction: zoom in vs zoom out
    rng = random.Random(seed)
    zoom_in = rng.choice([True, False])

    if zoom_in:
        zoom_step = zoom_amount / total_frames
        z_expr = f"min(zoom+{zoom_step:.6f},{1.0 + zoom_amount})"
    else:
        # Start zoomed in, zoom out to 1.0
        start_zoom = 1.0 + zoom_amount
        zoom_step = zoom_amount / total_frames
        z_expr = f"max({start_zoom}-on*{zoom_step:.6f},1.0)"

    crop_expr = f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}"
    scale_expr = f"scale={int(target_w * 1.2)}:-1"
    zoompan = (
        f"zoompan=z='{z_expr}':"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"d={total_frames}:s={target_w}x{target_h}:fps={fps}"
    )

    return f"{crop_expr},{scale_expr},{zoompan}"


def blur_bg_filter(
    width: int,
    height: int,
    target_w: int,
    target_h: int,
    duration: float,
    fps: int,
    seed: int | None = None,
) -> str:
    """Generate a blur background filter for portrait photos in landscape output.

    Reuses the proven pattern from scaling_utilities.py: splits the image into
    a blurred background (scaled up to fill) and a foreground (scaled to fit),
    then overlays centered. Adds a subtle Ken Burns zoom on the foreground.
    """
    total_frames = int(fps * duration)

    # Randomize subtle zoom (5-10%) and direction
    rng = random.Random(seed)
    zoom_amount = rng.uniform(0.05, 0.10)
    zoom_in = rng.choice([True, False])

    if zoom_in:
        zoom_step = zoom_amount / total_frames
        z_expr = f"min(zoom+{zoom_step:.6f},{1.0 + zoom_amount})"
    else:
        start_zoom = 1.0 + zoom_amount
        zoom_step = zoom_amount / total_frames
        z_expr = f"max({start_zoom}-on*{zoom_step:.6f},1.0)"

    # Background: scale up to fill + heavy blur
    bg = (
        f"split[bg][fg];"
        f"[bg]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h},"
        f"boxblur=luma_radius=150:chroma_radius=150:luma_power=3:chroma_power=3[blurred];"
    )
    # Foreground: fit within frame + subtle zoom
    fg = (
        f"[fg]scale={int(target_w * (1.0 + zoom_amount) * 1.05)}:-1:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"zoompan=z='{z_expr}':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={total_frames}:s={target_w}x{target_h}:fps={fps}[zoomed];"
    )
    # Overlay centered
    overlay = "[blurred][zoomed]overlay=(W-w)/2:(H-h)/2"

    return f"{bg}{fg}{overlay}"


def collage_filter(
    photos: list[tuple[int, int]],
    target_w: int,
    target_h: int,
    duration: float,
    fps: int,
    stagger: float = 0.5,
    seed: int | None = None,
) -> str:
    """Generate a multi-photo collage filter (Apple-style slide-in stack).

    2-3 landscape photos slide in from the right with a stagger delay,
    stack vertically, hold, then the composition zooms slightly.
    Slide direction is randomized via seed.
    """
    n = len(photos)
    if n < 2 or n > 4:
        msg = f"Collage requires 2-4 photos, got {n}"
        raise ValueError(msg)

    # Randomize slide direction
    rng = random.Random(seed)
    directions = ["right", "left", "bottom", "top"]
    slide_dir = rng.choice(directions)

    row_h = target_h // n

    # Scale each photo to fill its row
    inputs = [
        f"[{i}:v]scale={target_w}:{row_h}:force_original_aspect_ratio=increase,crop={target_w}:{row_h}[p{i}]"
        for i in range(n)
    ]

    # Build overlay chain with staggered slide-in
    chain = f"color=c=black:s={target_w}x{target_h}:d={duration}:r={fps}[base]"
    prev = "base"
    for i in range(n):
        delay = i * stagger
        y_pos = i * row_h

        if slide_dir == "right":
            x_expr = f"min(0\\, -W + (t-{delay})*W/{0.8})"
            y_expr = str(y_pos)
        elif slide_dir == "left":
            x_expr = f"max(0\\, W - (t-{delay})*W/{0.8})"
            y_expr = str(y_pos)
        elif slide_dir == "bottom":
            x_expr = "0"
            y_expr = f"min({y_pos}\\, -{row_h} + (t-{delay})*({y_pos}+{row_h})/{0.8})"
        else:  # top
            x_expr = "0"
            y_expr = f"max({y_pos}\\, {target_h} - (t-{delay})*({target_h}-{y_pos})/{0.8})"

        out = f"s{i}" if i < n - 1 else "vout"
        chain += f";[{prev}][p{i}]overlay=x='{x_expr}':y='{y_expr}':enable='gte(t,{delay})'[{out}]"
        prev = out

    return ";".join(inputs) + ";" + chain
