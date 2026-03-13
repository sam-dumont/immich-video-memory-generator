"""Tests for globe projection and camera keyframe generation."""

from __future__ import annotations

import math

import numpy as np
import pytest


class TestGlobeProjectionKernel:
    """Taichi GPU kernel for equirectangular → sphere projection."""

    @pytest.fixture(autouse=True)
    def _init_taichi(self):
        """Ensure Taichi is initialized before kernel tests."""
        from immich_memories.titles.taichi_kernels import init_taichi

        init_taichi()

    def test_projects_texture_to_output_buffer(self):
        """Kernel should produce non-zero RGB output from equirectangular texture."""
        from immich_memories.titles.taichi_globe import project_globe_frame

        texture = np.full((180, 360, 3), [0.0, 0.2, 0.8], dtype=np.float32)
        output = np.zeros((540, 960, 3), dtype=np.float32)

        project_globe_frame(
            output=output,
            texture=texture,
            cam_lat=0.0,
            cam_lon=0.0,
            cam_distance=3.0,
            width=960,
            height=540,
        )

        # Sphere should be visible — non-zero pixels
        assert output.max() > 0.0
        # Center pixel should have blue channel from texture
        center = output[270, 480]
        assert center[2] > 0.3

    def test_different_camera_positions_produce_different_frames(self):
        """Frames at different camera positions should differ."""
        from immich_memories.titles.taichi_globe import project_globe_frame

        texture = np.random.default_rng(42).random((180, 360, 3)).astype(np.float32)
        frame_0 = np.zeros((270, 480, 3), dtype=np.float32)
        frame_1 = np.zeros((270, 480, 3), dtype=np.float32)

        project_globe_frame(frame_0, texture, 0.0, 0.0, 3.0, 480, 270)
        project_globe_frame(frame_1, texture, 0.7, 0.5, 2.5, 480, 270)

        diff = np.abs(frame_0 - frame_1).mean()
        assert diff > 0.01

    def test_background_is_dark(self):
        """Pixels that miss the sphere should be dark (space)."""
        from immich_memories.titles.taichi_globe import project_globe_frame

        texture = np.full((180, 360, 3), 0.5, dtype=np.float32)
        output = np.zeros((540, 960, 3), dtype=np.float32)

        # Zoomed in close — corners should miss the sphere
        project_globe_frame(output, texture, 0.0, 0.0, 2.0, 960, 540)

        # Corner pixel should be dark (space background)
        corner = output[0, 0]
        assert corner.max() < 0.1


class TestCameraKeyframes:
    """Camera keyframe generation from trip locations."""

    def test_single_destination_produces_two_keyframes(self):
        """Home → destination = 2 keyframes."""
        from immich_memories.titles.globe_renderer import generate_camera_keyframes

        keyframes = generate_camera_keyframes(
            home_lat=50.85,
            home_lon=4.35,
            destinations=[(41.39, 2.17)],
        )
        assert len(keyframes) == 2
        assert abs(keyframes[0].lat - math.radians(50.85)) < 0.02
        assert abs(keyframes[-1].lat - math.radians(41.39)) < 0.02
        assert keyframes[-1].distance < keyframes[0].distance

    def test_multi_destination_produces_n_plus_1_keyframes(self):
        """Home → dest1 → dest2 → dest3 = 4 keyframes."""
        from immich_memories.titles.globe_renderer import generate_camera_keyframes

        keyframes = generate_camera_keyframes(
            home_lat=50.85,
            home_lon=4.35,
            destinations=[(41.39, 2.17), (39.47, -0.38), (37.39, -5.98)],
        )
        assert len(keyframes) == 4


class TestCameraInterpolation:
    """Smooth camera interpolation between keyframes."""

    def test_interpolate_at_start_returns_first_keyframe(self):
        from immich_memories.titles.globe_renderer import (
            GlobeCameraKeyframe,
            interpolate_camera,
        )

        kfs = [
            GlobeCameraKeyframe(lat=0.5, lon=0.1, distance=5.0, time=0.0),
            GlobeCameraKeyframe(lat=0.8, lon=0.3, distance=2.5, time=1.0),
        ]
        lat, lon, dist = interpolate_camera(kfs, 0.0)
        assert abs(lat - 0.5) < 0.01
        assert abs(dist - 5.0) < 0.01

    def test_interpolate_at_end_returns_last_keyframe(self):
        from immich_memories.titles.globe_renderer import (
            GlobeCameraKeyframe,
            interpolate_camera,
        )

        kfs = [
            GlobeCameraKeyframe(lat=0.5, lon=0.1, distance=5.0, time=0.0),
            GlobeCameraKeyframe(lat=0.8, lon=0.3, distance=2.5, time=1.0),
        ]
        lat, lon, dist = interpolate_camera(kfs, 1.0)
        assert abs(lat - 0.8) < 0.01

    def test_interpolate_midpoint_is_between_keyframes(self):
        from immich_memories.titles.globe_renderer import (
            GlobeCameraKeyframe,
            interpolate_camera,
        )

        kfs = [
            GlobeCameraKeyframe(lat=0.0, lon=0.0, distance=5.0, time=0.0),
            GlobeCameraKeyframe(lat=1.0, lon=1.0, distance=2.0, time=1.0),
        ]
        lat, lon, dist = interpolate_camera(kfs, 0.5)
        # Cosine easing at t=0.5 gives exactly the midpoint
        assert 0.3 < lat < 0.7
        assert 0.3 < lon < 0.7
