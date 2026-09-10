"""Honor certified editorial Live material without legacy subset or trim fallback."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from immich_memories.processing.live_material import LiveRenderMaterial
from immich_memories.processing.probe_cache import ProbeCache
from immich_memories.security import write_secret_file

RENDER_VERSION = "editorial-live-render-v1"
FRAME_QUANTIZATION = "source-packet-cadence-tail-and-output-frame-hold-v2"


def validate_editorial_live_clip(clip: Any) -> LiveRenderMaterial:
    certificate = clip.editorial_live_manifest
    if (
        not isinstance(certificate, dict)
        or set(certificate) != {"version", "material", "selected_interval"}
        or certificate["version"] != RENDER_VERSION
    ):
        raise ValueError("Invalid editorial Live render certificate")
    material = LiveRenderMaterial.from_dict(certificate["material"])
    material.assert_arrays(
        still_ids=clip.live_burst_still_ids,
        video_ids=clip.live_burst_video_ids,
        trim_points=clip.live_burst_trim_points,
        shutter_timestamps=clip.live_burst_shutter_timestamps,
    )
    if clip.asset.id not in material.still_ids or clip.live_burst_material != material.as_dict():
        raise ValueError("Editorial Live certificate does not bind this carrier lineage")
    primary = next(entry for entry in material.source_entries if entry.still_id == clip.asset.id)
    if clip.asset.live_photo_video_id != primary.video_id:
        raise ValueError("Editorial Live certificate changed the carrier's companion source")
    interval = certificate["selected_interval"]
    if not isinstance(interval, list) or len(interval) != 2:
        raise ValueError("Editorial Live certificate needs its selected interval")
    material.displayed_interval(*interval)
    if clip.duration_seconds != material.duration_seconds:
        raise ValueError("Editorial Live source duration differs from certified material")
    return material


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _video_end(probe) -> float:
    if probe.video_duration_seconds:
        return probe.video_duration_seconds + getattr(probe, "video_start_seconds", 0.0)
    return probe.duration_seconds


def _output_duration(probe) -> float:
    """Normalized outputs use playable duration, never their absolute endpoint."""
    start = getattr(probe, "video_start_seconds", 0.0)
    clock = getattr(probe, "video_time_base", None)
    tick = float(Fraction(clock)) if clock else 0.0
    if not math.isfinite(start) or tick < 0 or abs(start) > tick:
        raise ValueError("Editorial Live encoded output has a nonzero video start")
    return probe.video_duration_seconds or probe.duration_seconds


def _source_timing(probes, path, entry) -> dict:
    probe = probes.get(path)
    end = _video_end(probe)
    start = getattr(probe, "video_start_seconds", 0.0)
    if (
        not probe.has_video
        or not math.isfinite(end)
        or end <= 0
        or not math.isfinite(start)
        or entry.start < start
    ):
        raise ValueError("Editorial Live interval exceeds actual video source")
    if not math.isfinite(probe.fps) or probe.fps <= 0:
        raise ValueError("Editorial Live source has no verified frame rate")
    evidence = {
        "declared_end_seconds": entry.end,
        "video_start_seconds": start,
        "video_end_seconds": end,
        "container_seconds": probe.duration_seconds,
        "frame_seconds": 1 / probe.fps,
    }
    if entry.end > end:
        # Immich's whole-source duration has millisecond precision and follows
        # the container. It can extend just beyond the final video packet.
        if entry.end != round(probe.duration_seconds, 3):
            raise ValueError("Editorial Live interval exceeds actual video source")
        tail = probes.last_video_frame(path)
        gap = entry.end - tail["end_seconds"]
        if entry.start >= tail["end_seconds"] or gap < 0 or gap > tail["frame_seconds"]:
            raise ValueError("Editorial Live interval exceeds actual video source")
        evidence.update(
            final_packet=tail,
            source_tail_seconds=gap,
            boundary="millisecond-container-end-within-final-source-frame",
        )
    return evidence


def _hold_last_frame(path: Path, nominal: float, probe, *, hardware_enabled: bool) -> dict:
    """Represent a measured subframe shortfall, preserving every encoded frame."""
    from immich_memories.processing.live_photo_merger import (
        _append_encoding_args,
        burst_encoding_plan,
    )

    fps = probe.fps
    duration = _output_duration(probe)
    if not math.isfinite(fps) or fps <= 0 or not 0 < nominal - duration <= 1 / fps:
        raise ValueError("Editorial Live encoded shortfall exceeds one output frame")
    frames = math.ceil(nominal * fps)
    target = path.with_name(path.stem + ".frame-hold.mp4")
    if target.exists():
        raise ValueError("Editorial Live frame-hold attempt already exists")
    command = [
        "ffmpeg",
        "-n",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        f"tpad=stop_mode=clone:stop_duration={2 / fps:.17g},"
        f"fps={fps:.17g},trim=end_frame={frames},setpts=PTS-STARTPTS",
    ]
    plan = burst_encoding_plan(
        is_hdr=bool(getattr(probe, "hdr_type", None)), hardware_enabled=hardware_enabled
    )
    _append_encoding_args(command, plan, getattr(probe, "has_audio", False), target)
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    write_secret_file(target.with_suffix(".stderr.log"), result.stderr)
    if result.returncode or not target.is_file():
        raise ValueError("Editorial Live final-frame hold failed")
    actual = ProbeCache().get(target)
    actual_end = _output_duration(actual)
    if (
        not actual.has_video
        or not math.isfinite(actual_end)
        or not nominal <= actual_end <= nominal + 1 / fps + 1e-6
    ):
        raise ValueError("Editorial Live final-frame hold changed its nominal interval")
    evidence = {
        "input_sha256": _sha(path),
        "before_seconds": duration,
        "nominal_seconds": nominal,
        "render_fps": fps,
        "target_frames": frames,
        "after_seconds": actual_end,
        "output_sha256": _sha(target),
        "hold_seconds": actual_end - duration,
        "output_frame_seconds": 1 / fps,
    }
    target.replace(path)
    return evidence


def _reused_merge(target: Path, record_path: Path, identity: dict) -> Path | None:
    """Reuse only a record that still names this exact identity and these exact bytes."""
    if not (record_path.is_file() and target.is_file()):
        return None
    record = json.loads(record_path.read_text())
    if record.get("identity") == identity and record.get("output_sha256") == _sha(target):
        return target
    raise ValueError("Cached editorial Live merge changed its conserved identity")


def _source_timings(probes, paths, material) -> tuple[list[dict], float]:
    rounding = 0.0
    source_timing = []
    for path, entry in zip(paths, material.segments, strict=True):
        evidence = _source_timing(probes, path, entry)
        evidence["render_cadence"] = probes.render_frame_rate(path)
        source_timing.append(evidence)
        rounding += evidence["frame_seconds"]
    return source_timing, rounding


def _quantized_encode(
    probes, target: Path, material, rounding: float, *, hardware_enabled: bool
) -> tuple[float, dict | None, float]:
    """Accept the encode only within its measured rounding, holding a subframe shortfall."""
    actual = probes.get(target)
    actual_duration = _output_duration(actual)
    if (
        not actual.has_video
        or not math.isfinite(actual_duration)
        or actual_duration <= 0
        or (abs(actual_duration - material.duration_seconds) > rounding + 1e-6)
    ):
        raise ValueError("Editorial Live merge changed its certified duration")
    hold = None
    if actual_duration < material.duration_seconds:
        hold = _hold_last_frame(
            target, material.duration_seconds, actual, hardware_enabled=hardware_enabled
        )
        probes.invalidate(target)
        actual = probes.get(target)
        actual_duration = _output_duration(actual)
    if (
        not math.isfinite(actual.fps)
        or actual.fps <= 0
        or actual_duration > material.duration_seconds + 1 / actual.fps + 1e-6
    ):
        raise ValueError("Editorial Live merge exceeds one rendered frame of quantization")
    return actual_duration, hold, actual.fps


def render_certified_live(clip, paths, output_dir, *, merge, hardware_enabled=True) -> Path:
    """The only reusable merge is bound to this manifest and exact original bytes."""
    material = validate_editorial_live_clip(clip)
    if len(paths) != len(material.segments) or any(
        not path.is_file() or path.suffix == ".part" or path.stat().st_size == 0 for path in paths
    ):
        raise ValueError("Editorial Live rendering requires every declared source")
    inputs = [_sha(path) for path in paths]
    identity = {
        "version": RENDER_VERSION,
        "frame_quantization": FRAME_QUANTIZATION,
        "certificate": clip.editorial_live_manifest,
        "input_sha256": inputs,
        "hardware_enabled": hardware_enabled,
    }
    key = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    directory = Path(output_dir) / ".live_merges"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"editorial-{key}.mp4"
    record_path = target.with_suffix(".json")
    reused = _reused_merge(target, record_path, identity)
    if reused is not None:
        return reused
    probes = ProbeCache()
    source_timing, rounding = _source_timings(probes, paths, material)
    render_rate = max(Fraction(row["render_cadence"]["rate"]) for row in source_timing)
    result = merge(
        list(paths),
        list(material.trim_points),
        target,
        shutter_timestamps=list(material.shutter_timestamps),
        hardware_enabled=hardware_enabled,
        strict_material=True,
        render_frame_rate=str(render_rate),
    )
    if result != target or not target.is_file():
        raise ValueError("Editorial Live merge failed; material fallback is forbidden")
    actual_duration, hold, fps = _quantized_encode(
        probes, target, material, rounding, hardware_enabled=hardware_enabled
    )
    if clip.editorial_live_manifest["selected_interval"][1] > actual_duration:
        raise ValueError("Editorial Live selected interval exceeds the encoded material")
    write_secret_file(
        record_path,
        json.dumps(
            {
                "identity": identity,
                "output_sha256": _sha(target),
                "declared_duration_seconds": material.duration_seconds,
                "encoded_duration_seconds": actual_duration,
                "frame_rounding_bound_seconds": rounding,
                "frame_quantization": {
                    "contract": FRAME_QUANTIZATION,
                    "sources": source_timing,
                    "final_frame_hold": hold,
                    "render_frame_seconds": 1 / fps,
                    "requested_render_frame_rate": str(render_rate),
                    "nominal_to_encoded_seconds": actual_duration - material.duration_seconds,
                },
            },
            sort_keys=True,
            indent=2,
        ),
    )
    return target


def extract_certified_live(clip, video_path, output_dir, *, extract, config) -> tuple[Path, float]:
    """Extract the certified interval accurately, without an unbound path-only cache hit."""
    validate_editorial_live_clip(clip)
    start, end = clip.editorial_live_manifest["selected_interval"]
    nominal = end - start
    identity = {
        "version": RENDER_VERSION,
        "frame_quantization": FRAME_QUANTIZATION,
        "certificate": clip.editorial_live_manifest,
        "merged_sha256": _sha(video_path),
        "reencode": True,
        "hardware": config.hardware.model_dump(mode="json"),
        "output": config.output.model_dump(mode="json"),
    }
    key = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    directory = Path(output_dir) / ".live_segments"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"editorial-{key}.mp4"
    record_path = target.with_suffix(".json")
    if target.is_file():
        if record_path.is_file():
            record = json.loads(record_path.read_text())
            if record.get("identity") == identity and record.get("output_sha256") == _sha(target):
                return target, nominal
        raise ValueError("Cached editorial Live segment lacks exact conserved provenance")
    result = extract(
        video_path,
        start_time=start,
        end_time=end,
        output_path=target,
        reencode=True,
        buffer_start=False,
        buffer_end=False,
        config=config,
    )
    if result != target or not target.is_file():
        raise ValueError("Certified editorial Live extraction failed")
    actual = ProbeCache().get(target)
    duration = _output_duration(actual)
    hold = None
    if actual.has_video and math.isfinite(duration) and duration < nominal:
        hold = _hold_last_frame(target, nominal, actual, hardware_enabled=config.hardware.enabled)
        actual = ProbeCache().get(target)
        duration = _output_duration(actual)
    if (
        not actual.has_video
        or not math.isfinite(duration)
        or duration < nominal
        or (
            not math.isfinite(actual.fps)
            or actual.fps <= 0
            or duration > nominal + 1 / actual.fps + 1e-6
        )
    ):
        raise ValueError("Editorial Live extraction cannot fulfill its certified interval")
    write_secret_file(
        record_path,
        json.dumps(
            {
                "identity": identity,
                "output_sha256": _sha(target),
                "selected_duration_seconds": nominal,
                "encoded_duration_seconds": duration,
                "frame_rounding_bound_seconds": 1 / actual.fps,
                "frame_quantization": {
                    "contract": FRAME_QUANTIZATION,
                    "final_frame_hold": hold,
                    "nominal_to_encoded_seconds": duration - nominal,
                },
            },
            sort_keys=True,
            indent=2,
        ),
    )
    return target, nominal
