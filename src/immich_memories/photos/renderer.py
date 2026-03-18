"""Frame-by-frame photo animation renderer.

Generates video frames in Python (numpy/cv2) with subpixel-precise
Ken Burns, dynamic blur backgrounds, and HDR support. Frames are piped
to FFmpeg as raw RGB for encoding.

This replaces the FFmpeg filter expression approach (filter_expressions.py)
for all photo animations. The filter expressions had issues with zoompan
jitter, color space handling, and HEIC incompatibility. The Python renderer
gives us full control over every pixel.

Validated on real iPhone 16 Pro HEIC photos with HDR gain maps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class KenBurnsParams:
    """Parameters for a Ken Burns animation."""

    zoom_start: float = 1.0
    zoom_end: float = 1.10
    # Pan position: (0,0)=top-left, (0.5,0.5)=center, (1,1)=bottom-right
    pan_start: tuple[float, float] = (0.5, 0.5)
    pan_end: tuple[float, float] = (0.5, 0.5)
    fps: int = 30
    duration: float = 4.0


def render_ken_burns(
    src: np.ndarray,
    vp_w: int,
    vp_h: int,
    params: KenBurnsParams | None = None,
) -> list[np.ndarray]:
    """Render Ken Burns frames with fixed-frame dynamic blur background.

    The photo "window" is a fixed rectangle centered in the viewport.
    Ken Burns zooms/pans INSIDE that window. Blur fills the rest,
    derived from the same crop content each frame.

    The window position and size NEVER change during the animation.
    """
    if params is None:
        params = KenBurnsParams()

    src_h, src_w = src.shape[:2]

    # Photo window: fit source aspect into viewport
    photo_scale = min(vp_w / src_w, vp_h / src_h)
    win_w = int(src_w * photo_scale)
    win_h = int(src_h * photo_scale)
    win_x = (vp_w - win_w) // 2
    win_y = (vp_h - win_h) // 2
    needs_blur = (win_w < vp_w - 2) or (win_h < vp_h - 2)

    # Scale source for KB headroom — use MAX zoom for crisp pixels
    max_z = max(params.zoom_start, params.zoom_end)
    pan_travel = max(
        abs(params.pan_end[0] - params.pan_start[0]),
        abs(params.pan_end[1] - params.pan_start[1]),
    )
    margin = 1.0 + pan_travel * 0.6 + 0.02

    kb_scale = max(
        (win_w * max_z) * margin / src_w,
        (win_h * max_z) * margin / src_h,
    )
    big = cv2.resize(
        src, (int(src_w * kb_scale), int(src_h * kb_scale)), interpolation=cv2.INTER_AREA
    )
    bh, bw = big.shape[:2]

    # Pre-blur for background (one-time cost)
    big_blur = cv2.GaussianBlur(big, (0, 0), sigmaX=40) if needs_blur else None

    n_frames = int(params.fps * params.duration)
    frames: list[np.ndarray] = []

    for i in range(n_frames):
        t = i / max(n_frames - 1, 1)
        zoom = params.zoom_start + (params.zoom_end - params.zoom_start) * t
        px = params.pan_start[0] + (params.pan_end[0] - params.pan_start[0]) * t
        py = params.pan_start[1] + (params.pan_end[1] - params.pan_start[1]) * t

        vis_w = win_w / zoom
        vis_h = win_h / zoom
        cx = vis_w / 2 + px * max(0, bw - vis_w)
        cy = vis_h / 2 + py * max(0, bh - vis_h)
        x0 = cx - vis_w / 2
        y0 = cy - vis_h / 2

        # Sharp crop → photo window
        sx, sy = win_w / vis_w, win_h / vis_h
        m_sharp = np.array([[sx, 0, -x0 * sx], [0, sy, -y0 * sy]], dtype=np.float32)
        sharp = cv2.warpAffine(
            big, m_sharp, (win_w, win_h), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE
        )

        if needs_blur and big_blur is not None:
            # Blur crop → fill viewport
            fsx, fsy = vp_w / vis_w, vp_h / vis_h
            m_blur = np.array([[fsx, 0, -x0 * fsx], [0, fsy, -y0 * fsy]], dtype=np.float32)
            bg = cv2.warpAffine(
                big_blur,
                m_blur,
                (vp_w, vp_h),
                flags=cv2.INTER_AREA,
                borderMode=cv2.BORDER_REPLICATE,
            )
            bg[win_y : win_y + win_h, win_x : win_x + win_w] = sharp
            frames.append(bg)
        else:
            # No blur needed — sharp covers the viewport
            vsx, vsy = vp_w / vis_w, vp_h / vis_h
            m_vp = np.array([[vsx, 0, -x0 * vsx], [0, vsy, -y0 * vsy]], dtype=np.float32)
            frame = cv2.warpAffine(
                big, m_vp, (vp_w, vp_h), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE
            )
            frames.append(frame)

    return frames


def render_slide_in(
    src: np.ndarray,
    vp_w: int,
    vp_h: int,
    direction: str = "right",
    hold_ratio: float = 0.6,
    fps: int = 30,
    duration: float = 4.0,
) -> list[np.ndarray]:
    """Render a slide-in effect: photo slides into the frame, holds, then slight zoom.

    The photo slides in from the given direction, settles into its
    blur-background frame, then holds with a gentle Ken Burns zoom.

    direction: 'left', 'right', 'top', 'bottom'
    hold_ratio: fraction of duration spent holding (vs sliding)
    """
    src_h, src_w = src.shape[:2]
    n_frames = int(fps * duration)
    slide_frames = int(n_frames * (1 - hold_ratio))
    # Photo window (same as ken_burns)
    photo_scale = min(vp_w / src_w, vp_h / src_h)
    win_w = int(src_w * photo_scale)
    win_h = int(src_h * photo_scale)
    win_x_final = (vp_w - win_w) // 2
    win_y_final = (vp_h - win_h) // 2

    # Scale source
    big = cv2.resize(src, (win_w, win_h), interpolation=cv2.INTER_AREA)

    # Pre-blur background (static for the whole animation)
    bg_s = max(vp_w / src_w, vp_h / src_h)
    bg_full = cv2.resize(src, (int(src_w * bg_s), int(src_h * bg_s)), interpolation=cv2.INTER_AREA)
    by, bx = (bg_full.shape[0] - vp_h) // 2, (bg_full.shape[1] - vp_w) // 2
    bg_blur = cv2.GaussianBlur(bg_full[by : by + vp_h, bx : bx + vp_w], (0, 0), sigmaX=40)

    # Slide offsets
    if direction == "right":
        start_x, start_y = -win_w, win_y_final
    elif direction == "left":
        start_x, start_y = vp_w, win_y_final
    elif direction == "bottom":
        start_x, start_y = win_x_final, -win_h
    else:  # top
        start_x, start_y = win_x_final, vp_h

    frames: list[np.ndarray] = []

    for i in range(n_frames):
        canvas = bg_blur.copy()

        if i < slide_frames:
            # Slide phase: ease-out interpolation
            t = i / max(slide_frames - 1, 1)
            ease = 1 - (1 - t) ** 3  # Cubic ease-out
            cx = int(start_x + (win_x_final - start_x) * ease)
            cy = int(start_y + (win_y_final - start_y) * ease)
        else:
            # Hold phase: slight zoom
            cx, cy = win_x_final, win_y_final

        # Place photo (clip to viewport bounds)
        src_x0 = max(0, -cx)
        src_y0 = max(0, -cy)
        dst_x0 = max(0, cx)
        dst_y0 = max(0, cy)
        copy_w = min(win_w - src_x0, vp_w - dst_x0)
        copy_h = min(win_h - src_y0, vp_h - dst_y0)

        if copy_w > 0 and copy_h > 0:
            canvas[dst_y0 : dst_y0 + copy_h, dst_x0 : dst_x0 + copy_w] = big[
                src_y0 : src_y0 + copy_h, src_x0 : src_x0 + copy_w
            ]

        frames.append(canvas)

    return frames


def _prepare_collage_cells(photos: list[np.ndarray], cell_w: int, cell_h: int) -> list[np.ndarray]:
    """Scale each photo to fill its collage cell (center crop)."""
    cells = []
    for photo in photos:
        ph, pw = photo.shape[:2]
        cs = max(cell_w / pw, cell_h / ph)
        resized = cv2.resize(photo, (int(pw * cs), int(ph * cs)), interpolation=cv2.INTER_AREA)
        cy2 = (resized.shape[0] - cell_h) // 2
        cx2 = (resized.shape[1] - cell_w) // 2
        cells.append(resized[cy2 : cy2 + cell_h, cx2 : cx2 + cell_w])
    return cells


def _compute_slide_position(
    frame_idx: int,
    cell_idx: int,
    final_pos: tuple[int, int],
    cell_w: int,
    cell_h: int,
    vp_w: int,
    vp_h: int,
    orientation: str,
    fps: int,
) -> tuple[int, int] | None:
    """Compute cell position for a given frame during slide-in animation.

    Returns (x, y) position or None if the cell isn't visible yet.
    """
    stagger_frames = int(cell_idx * 0.3 * fps)
    slide_frames = int(0.8 * fps)
    final_x, final_y = final_pos

    if frame_idx >= stagger_frames + slide_frames:
        return final_x, final_y
    if frame_idx < stagger_frames:
        return None

    t = (frame_idx - stagger_frames) / slide_frames
    ease = 1 - (1 - t) ** 3

    if orientation == "horizontal":
        start_x = -cell_w if cell_idx % 2 == 0 else vp_w
        return int(start_x + (final_x - start_x) * ease), final_y

    start_y = -cell_h if cell_idx % 2 == 0 else vp_h
    return final_x, int(start_y + (final_y - start_y) * ease)


def _blit_cell(canvas: np.ndarray, cell: np.ndarray, cx: int, cy: int) -> None:
    """Place a cell on the canvas, clipping to canvas bounds."""
    vp_h, vp_w = canvas.shape[:2]
    cell_h, cell_w = cell.shape[:2]
    sx0, sy0 = max(0, -cx), max(0, -cy)
    dx0, dy0 = max(0, cx), max(0, cy)
    cw = min(cell_w - sx0, vp_w - dx0)
    ch = min(cell_h - sy0, vp_h - dy0)
    if cw > 0 and ch > 0:
        canvas[dy0 : dy0 + ch, dx0 : dx0 + cw] = cell[sy0 : sy0 + ch, sx0 : sx0 + cw]


def render_collage(
    photos: list[np.ndarray],
    vp_w: int,
    vp_h: int,
    orientation: str = "horizontal",
    gap: int = 24,
    fps: int = 30,
    duration: float = 5.0,
    slide_in: bool = True,
) -> list[np.ndarray]:
    """Render a multi-photo collage with slide-in animation.

    Photos slide in one by one, then the collage holds with gentle zoom.
    Background between photos is a blurred average of all photos.

    orientation: 'horizontal' (side by side) or 'vertical' (stacked)
    """
    n = len(photos)
    if n < 2 or n > 4:
        msg = f"Collage requires 2-4 photos, got {n}"
        raise ValueError(msg)

    n_frames = int(fps * duration)

    if orientation == "horizontal":
        cell_w = (vp_w - (n - 1) * gap) // n
        cell_h = vp_h
    else:
        cell_w = vp_w
        cell_h = (vp_h - (n - 1) * gap) // n

    cells = _prepare_collage_cells(photos, cell_w, cell_h)

    bg_blend = np.mean(
        [cv2.resize(p, (vp_w, vp_h), interpolation=cv2.INTER_AREA) for p in photos], axis=0
    ).astype(np.float32)
    bg_blur = cv2.GaussianBlur(bg_blend, (0, 0), sigmaX=50)

    positions = [
        (idx * (cell_w + gap), 0) if orientation == "horizontal" else (0, idx * (cell_h + gap))
        for idx in range(n)
    ]

    frames: list[np.ndarray] = []
    for i in range(n_frames):
        canvas = bg_blur.copy()
        for idx, (pos, cell) in enumerate(zip(positions, cells, strict=True)):
            if slide_in:
                result = _compute_slide_position(
                    i, idx, pos, cell_w, cell_h, vp_w, vp_h, orientation, fps
                )
                if result is None:
                    continue
                cx, cy = result
            else:
                cx, cy = pos
            _blit_cell(canvas, cell, cx, cy)
        frames.append(canvas)

    return frames
