"""Animated satellite map fly-over — van Wijk smooth zoom (d3.interpolateZoom)."""

from __future__ import annotations

import logging
import math
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from staticmap import CircleMarker, StaticMap

from .encoding import _get_gpu_encoder_args
from .map_renderer import _draw_gradient_band, _overlay_composite, _wrap_text

logger = logging.getLogger(__name__)

_CITY_ZOOM = 14  # Start/end zoom (city-level, ~30 m/px)
_MIN_ZOOM_FLOOR = 3  # Never zoom out past this
_RHO = math.sqrt(2)  # Zoom/pan trade-off (d3 default)
_SAT_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)

_ViewInterp = Callable[[float], tuple[float, float, float]]
_PAN_THRESHOLD_ZOOM = 10  # If mid-transit zoom > this, pan instead of zoom


@dataclass
class _PinData:
    """Pin with optional label."""

    lat: float
    lon: float
    name: str | None = None


@dataclass
class _FlyConfig:
    """Animation state: interpolators, pins, overlay, dimensions."""

    interps: list[_ViewInterp] = field(default_factory=list)
    pins: list[_PinData] = field(default_factory=list)
    title_overlay: Image.Image | None = None
    width: int = 1920
    height: int = 1080
    dest_zoom: float = 9.0  # zoom level at destination (pins reference size)


_tile_cache: dict[str, bytes] = {}


class _CachedStaticMap(StaticMap):
    """StaticMap with shared cross-instance tile cache."""

    def get(self, url: str, **kwargs):  # type: ignore[override]
        """Return cached tile bytes or fetch + cache."""
        if url in _tile_cache:
            return 200, _tile_cache[url]
        status, content = super().get(url, **kwargs)
        if status == 200:
            _tile_cache[url] = content
        return status, content


# -- Web Mercator (zoom-0 pixel space, 256 px = world) ---------------------


def _to_world(lat: float, lon: float) -> tuple[float, float]:
    """(lat, lon) → zoom-0 pixel coords."""
    x = (lon + 180.0) / 360.0 * 256.0
    lat_r = math.radians(max(-85.0, min(85.0, lat)))
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * 256.0
    return x, y


def _to_latlon(wx: float, wy: float) -> tuple[float, float]:
    """Zoom-0 pixel coords → (lat, lon)."""
    lon = wx / 256.0 * 360.0 - 180.0
    n = math.pi - 2.0 * math.pi * wy / 256.0
    return math.degrees(math.atan(math.sinh(n))), lon


def _geo_to_screen(
    pin_lat: float,
    pin_lon: float,
    cam_lat: float,
    cam_lon: float,
    zoom: float,
    w: int,
    h: int,
) -> tuple[int, int]:
    """(lat, lon) → screen pixel given camera state."""
    pw, py_w = _to_world(pin_lat, pin_lon)
    cw, cy_w = _to_world(cam_lat, cam_lon)
    scale = 2.0**zoom
    return int(w / 2 + (pw - cw) * scale), int(h / 2 + (py_w - cy_w) * scale)


def _van_wijk(
    p0: tuple[float, float, float],
    p1: tuple[float, float, float],
    rho: float = _RHO,
) -> _ViewInterp:
    """Optimal zoom+pan: (cx,cy,w) views → f(t) interpolator."""
    ux0, uy0, w0 = p0
    ux1, uy1, w1 = p1
    dx, dy = ux1 - ux0, uy1 - uy0
    d2 = dx * dx + dy * dy
    rho2, rho4 = rho * rho, rho**4

    if d2 < 1e-12:
        s_tot = math.log(w1 / w0) / rho if w0 > 0 and w1 > 0 else 0.0

        def _pure_zoom(t: float) -> tuple[float, float, float]:
            return ux0 + t * dx, uy0 + t * dy, w0 * math.exp(rho * t * s_tot)

        return _pure_zoom

    d1 = math.sqrt(d2)
    b0 = (w1 * w1 - w0 * w0 + rho4 * d2) / (2.0 * w0 * rho2 * d1)
    b1 = (w1 * w1 - w0 * w0 - rho4 * d2) / (2.0 * w1 * rho2 * d1)
    r0 = math.log(math.sqrt(b0 * b0 + 1.0) - b0)
    r1 = math.log(math.sqrt(b1 * b1 + 1.0) - b1)
    s_tot = (r1 - r0) / rho
    coshr0 = math.cosh(r0)
    sinhr0 = math.sinh(r0)

    def _zoom_pan(t: float) -> tuple[float, float, float]:
        s = t * s_tot
        u = w0 / (rho2 * d1) * (coshr0 * math.tanh(rho * s + r0) - sinhr0)
        return ux0 + u * dx, uy0 + u * dy, w0 * coshr0 / math.cosh(rho * s + r0)

    return _zoom_pan


def _linear_pan(p0: tuple[float, float, float], p1: tuple[float, float, float]) -> _ViewInterp:
    """Smooth ease-in-out pan at fixed zoom for short distances."""
    ux0, uy0, w0 = p0
    ux1, uy1, _ = p1
    w_use = max(w0, p1[2])  # wider viewport so both points stay visible

    def _pan(t: float) -> tuple[float, float, float]:
        t = t * t * (3.0 - 2.0 * t)  # smoothstep
        return ux0 + t * (ux1 - ux0), uy0 + t * (uy1 - uy0), w_use

    return _pan


def _pick_interpolator(
    p0: tuple[float, float, float],
    p1: tuple[float, float, float],
    width: int,
) -> _ViewInterp:
    """Van Wijk zoom for long distances, linear pan for short hops."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    d = math.sqrt(dx * dx + dy * dy)
    mid_w = max(p0[2], p1[2], d * 1.5)
    mid_zoom = math.log2(width / mid_w) if mid_w > 0 else _CITY_ZOOM
    if mid_zoom >= _PAN_THRESHOLD_ZOOM:
        logger.info("Short hop — linear pan (mid-zoom %.1f)", mid_zoom)
        return _linear_pan(p0, p1)
    return _van_wijk(p0, p1)


# ---------------------------------------------------------------------------
# Per-frame rendering
# ---------------------------------------------------------------------------


def _render_satellite(lat: float, lon: float, zoom: float, w: int, h: int) -> Image.Image:
    """Satellite tiles at fractional zoom via oversample + resize."""
    z_int = max(1, min(19, math.ceil(zoom)))
    frac = z_int - zoom
    oversample = 2.0**frac

    rw = int(math.ceil(w * oversample))
    rh = int(math.ceil(h * oversample))

    sm = _CachedStaticMap(rw, rh, url_template=_SAT_URL)
    sm.add_marker(CircleMarker((lon, lat), "#00000000", 1))  # required by staticmap

    try:
        img = sm.render(zoom=z_int, center=[lon, lat])
    except Exception:
        logger.warning("Tile fetch failed z=%d (%.2f,%.2f)", z_int, lat, lon)
        img = Image.new("RGB", (rw, rh), (40, 50, 60))

    if img.size != (w, h):
        img = img.resize((w, h), Image.Resampling.LANCZOS)
    return img


def _draw_pins(
    frame: Image.Image,
    cam_lat: float,
    cam_lon: float,
    zoom: float,
    pins: list[_PinData],
    dest_zoom: float,
    w: int,
    h: int,
) -> Image.Image:
    """Draw destination pins + city labels, fading in near destination zoom."""
    if not pins:
        return frame
    pin_alpha = max(0.0, min(1.0, (zoom - dest_zoom + 2.5) / 2.5))
    if pin_alpha < 0.05:
        return frame

    from .map_renderer import _get_font

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    base_r = max(6, int(min(w, h) * 0.012))
    font = _get_font(max(12, int(min(w, h) * 0.022)), bold=True)
    a_w, a_f = int(200 * pin_alpha), int(230 * pin_alpha)
    a_l, a_s = int(220 * pin_alpha), int(140 * pin_alpha)

    for pin in pins:
        sx, sy = _geo_to_screen(pin.lat, pin.lon, cam_lat, cam_lon, zoom, w, h)
        margin = base_r * 3
        if sx < -margin or sx > w + margin or sy < -margin or sy > h + margin:
            continue
        r_out = base_r + 3
        draw.ellipse((sx - r_out, sy - r_out, sx + r_out, sy + r_out), fill=(255, 255, 255, a_w))
        draw.ellipse((sx - base_r, sy - base_r, sx + base_r, sy + base_r), fill=(232, 93, 74, a_f))

        if pin.name:
            bbox = draw.textbbox((0, 0), pin.name, font=font)
            lx = sx - (bbox[2] - bbox[0]) // 2
            ly = sy - r_out - getattr(font, "size", 14) - 6
            draw.text((lx + 1, ly + 1), pin.name, fill=(0, 0, 0, a_s), font=font)
            draw.text((lx, ly), pin.name, fill=(255, 255, 255, a_l), font=font)

    return Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")


def _render_frame(
    lat: float,
    lon: float,
    zoom: float,
    cfg: _FlyConfig,
) -> Image.Image:
    """Render satellite + pins + title for one animation frame."""
    frame = _render_satellite(lat, lon, zoom, cfg.width, cfg.height)
    return _draw_pins(frame, lat, lon, zoom, cfg.pins, cfg.dest_zoom, cfg.width, cfg.height)


def _destination_overview(
    destinations: list[tuple[float, float]],
    width: int,
    height: int,
) -> tuple[float, float, float]:
    """Compute (wx, wy, w) that shows all destinations with 2x padding."""
    worlds = [_to_world(lat, lon) for lat, lon in destinations]
    wxs, wys = [v[0] for v in worlds], [v[1] for v in worlds]
    cx, cy = sum(wxs) / len(wxs), sum(wys) / len(wys)
    span_x = (max(wxs) - min(wxs)) if len(wxs) > 1 else 0.0
    span_y = (max(wys) - min(wys)) if len(wys) > 1 else 0.0
    w_overview = max(span_x * 2.0, span_y * (width / height) * 2.0)
    w_overview = max(width / (2.0**12), min(width / (2.0**_MIN_ZOOM_FLOOR), w_overview))
    return cx, cy, w_overview


def create_map_fly_video(
    departure: tuple[float, float],
    destinations: list[tuple[float, float]],
    title_text: str,
    output_path: Path,
    width: int = 1920,
    height: int = 1080,
    duration: float = 5.0,
    fps: float = 30.0,
    hold_start: float = 0.5,
    hold_end: float = 1.0,
    hdr: bool = False,
    departure_name: str | None = None,
    destination_names: list[str] | None = None,
) -> Path:
    """Google Earth-style fly-over using van Wijk smooth zoom."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    all_names = [departure_name or "departure"]
    if destination_names:
        all_names.extend(destination_names)
    logger.info("Map fly: %s (%.1fs, %dx%d)", " → ".join(all_names), duration, width, height)

    # Build pin data — departure + destinations
    pins: list[_PinData] = [_PinData(lat=departure[0], lon=departure[1], name=departure_name)]
    for i, (lat, lon) in enumerate(destinations):
        name = destination_names[i] if destination_names and i < len(destination_names) else None
        pins.append(_PinData(lat=lat, lon=lon, name=name))

    # Departure → destination overview (single segment)
    dep_wx, dep_wy = _to_world(*departure)
    w_city = width / (2.0**_CITY_ZOOM)
    dest_cx, dest_cy, w_overview = _destination_overview(destinations, width, height)

    interps: list[_ViewInterp] = [
        _pick_interpolator((dep_wx, dep_wy, w_city), (dest_cx, dest_cy, w_overview), width),
    ]

    dz = math.log2(width / w_overview) if w_overview > 0 else float(_CITY_ZOOM)
    cfg = _FlyConfig(
        interps=interps,
        pins=pins,
        title_overlay=_render_title_overlay(title_text, width, height),
        width=width,
        height=height,
        dest_zoom=max(3.0, min(14.0, dz)),
    )

    _tile_cache.clear()
    _pipe_frames(cfg, output_path, duration, fps, hold_start, hold_end, hdr)

    logger.info("Map fly done: %s (%d tiles cached)", output_path, len(_tile_cache))
    return output_path


def _view_at(
    t: float,
    interps: list[_ViewInterp],
    n: int,
    w: int,
) -> tuple[float, float, float]:
    """Get (lat, lon, zoom) at animation time t ∈ [0, 1]."""
    seg_t = t * n
    idx = min(int(seg_t), n - 1)
    local = max(0.0, min(1.0, seg_t - idx))

    wx, wy, vw = interps[idx](local)
    lat, lon = _to_latlon(wx, wy)
    zoom = math.log2(w / vw) if vw > 0 else float(_CITY_ZOOM)
    return lat, lon, max(float(_MIN_ZOOM_FLOOR), min(float(_CITY_ZOOM), zoom))


def _get_frame_at(
    i: int,
    total: int,
    hold_s: int,
    hold_e: int,
    anim: int,
    cfg: _FlyConfig,
    n_segs: int,
    w: int,
    cached: list[Image.Image | None],
) -> tuple[Image.Image, float]:
    """Return the rendered frame and zoom level for frame index i.

    cached is a 2-element list: [first_frame, last_frame] (mutated in-place).
    """
    if i < hold_s:
        if cached[0] is None:
            lat, lon, z = _view_at(0.0, cfg.interps, n_segs, w)
            frame = _render_frame(lat, lon, z, cfg)
            cached[0] = frame
            return frame, z
        return cached[0], float(_CITY_ZOOM)
    if i >= total - hold_e:
        if cached[1] is None:
            lat, lon, z = _view_at(1.0, cfg.interps, n_segs, w)
            frame = _render_frame(lat, lon, z, cfg)
            cached[1] = frame
            return frame, z
        return cached[1], float(_CITY_ZOOM)
    t = (i - hold_s) / anim
    lat, lon, z = _view_at(t, cfg.interps, n_segs, w)
    return _render_frame(lat, lon, z, cfg), z


def _pipe_frames(
    cfg: _FlyConfig,
    output_path: Path,
    duration: float,
    fps: float,
    hold_start: float,
    hold_end: float,
    hdr: bool,
) -> None:
    """Render animation frames and pipe raw RGB to FFmpeg."""
    total = int(duration * fps)
    hold_s = int(hold_start * fps)
    hold_e = int(hold_end * fps)
    anim = max(1, total - hold_s - hold_e)
    n_segs = len(cfg.interps)
    w, h = cfg.width, cfg.height

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-s",
        f"{w}x{h}",
        "-pix_fmt",
        "rgb24",
        "-r",
        str(fps),
        "-i",
        "-",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo",
        *_get_gpu_encoder_args(hdr=hdr),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-t",
        str(duration),
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    proc = subprocess.Popen(  # noqa: S603
        cmd,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None  # noqa: S101

    cached: list[Image.Image | None] = [None, None]
    z = float(_CITY_ZOOM)

    try:
        for i in range(total):
            frame, z = _get_frame_at(i, total, hold_s, hold_e, anim, cfg, n_segs, w, cached)

            if cfg.title_overlay is not None:
                a = _title_alpha(i / max(1, total - 1))
                if a > 0.01:
                    frame = _overlay_composite(frame, cfg.title_overlay, a)

            proc.stdin.write(np.array(frame).tobytes())

            if i % 30 == 0:
                logger.info("Map fly %d/%d (z=%.1f, %d tiles)", i, total, z, len(_tile_cache))

    except BrokenPipeError:
        pass

    proc.stdin.close()
    proc.wait()
    err = proc.stderr.read() if proc.stderr else b""
    if proc.returncode != 0:
        raise RuntimeError(f"Map fly FFmpeg failed: {err.decode()[-500:]}")


def _title_alpha(p: float) -> float:
    """Title fade: in 0–15 %, full 15–85 %, out 85–100 %."""
    if p < 0.15:
        return p / 0.15
    return max(0.0, (1.0 - p) / 0.15) if p > 0.85 else 1.0


def _render_title_overlay(text: str, w: int, h: int) -> Image.Image | None:
    """Pre-render title text as an RGBA overlay — big, centered."""
    if not text:
        return None
    from .map_renderer import _get_font

    is_portrait = h > w
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    fs = int(w * 0.12) if is_portrait else int(h * 0.09)
    font = _get_font(fs, bold=True)

    lines = _wrap_text(text, draw, font, int(w * 0.88))
    line_h = int(fs * 1.2)
    total_h = line_h * len(lines)
    block_y = int(h * 0.75) - total_h // 2 if not is_portrait else (h - total_h) // 2
    band_pad = int(fs * 0.8)
    _draw_gradient_band(draw, block_y - band_pad, total_h + 2 * band_pad, w, h)
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x = (w - tw) // 2
        y = block_y + i * line_h
        draw.text((x + 2, y + 2), line, fill=(0, 0, 0, 130), font=font)
        draw.text((x, y), line, fill=(255, 255, 255, 240), font=font)

    return img
