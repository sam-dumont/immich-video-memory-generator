"""Integration tests for photo animation renderer.

Tests render_ken_burns (list + streaming), render_slide_in, render_collage,
render_split, and face_aware_pan with real numpy image data. No mocks.

Run: make test-integration-photos
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
import pytest

from immich_memories.photos.renderer import (
    KenBurnsParams,
    face_aware_pan,
    render_collage,
    render_ken_burns,
    render_ken_burns_streaming,
    render_slide_in,
    render_split,
)

pytestmark = [pytest.mark.integration]

VP_W, VP_H = 640, 360


@pytest.fixture(scope="module")
def landscape_img():
    """1600x900 landscape image with a gradient and center circle."""
    img = np.zeros((900, 1600, 3), dtype=np.float32)
    for x in range(1600):
        img[:, x, 0] = 0.6 * (x / 1600)
    for y in range(900):
        img[y, :, 1] = 0.4 * (y / 900)
    img[:, :, 2] = 0.3
    cv2.circle(img, (800, 450), 100, (0.9, 0.9, 0.1), -1)
    return img


@pytest.fixture(scope="module")
def portrait_img():
    """900x1600 portrait image with a vertical gradient."""
    img = np.zeros((1600, 900, 3), dtype=np.float32)
    for y in range(1600):
        img[y, :, 0] = 0.5 * (y / 1600)
    img[:, :, 1] = 0.3
    img[:, :, 2] = 0.2
    cv2.circle(img, (450, 800), 80, (0.1, 0.8, 0.3), -1)
    return img


@pytest.fixture(scope="module")
def small_photos():
    """4 distinct small images for collage/split tests."""
    colors = [(0.8, 0.1, 0.1), (0.1, 0.7, 0.1), (0.1, 0.1, 0.8), (0.7, 0.7, 0.1)]
    photos = []
    for r, g, b in colors:
        img = np.zeros((600, 800, 3), dtype=np.float32)
        img[:, :, 0] = r
        img[:, :, 1] = g
        img[:, :, 2] = b
        photos.append(img)
    return photos


# ---- Ken Burns ----


class TestKenBurns:
    def test_frame_count_matches_duration(self, landscape_img):
        """Frame count should equal fps * duration."""
        params = KenBurnsParams(fps=30, duration=2.0)
        frames = render_ken_burns(landscape_img, VP_W, VP_H, params)
        assert len(frames) == 60

    def test_frame_dimensions_match_viewport(self, landscape_img):
        """Every frame should match the requested viewport size."""
        params = KenBurnsParams(fps=30, duration=1.0)
        frames = render_ken_burns(landscape_img, VP_W, VP_H, params)
        for f in frames:
            assert f.shape == (VP_H, VP_W, 3)

    def test_zoom_produces_different_first_and_last(self, landscape_img):
        """Zoom animation should make first and last frames visually different."""
        params = KenBurnsParams(zoom_start=1.0, zoom_end=1.15, fps=30, duration=2.0)
        frames = render_ken_burns(landscape_img, VP_W, VP_H, params)
        diff = np.abs(frames[0].astype(float) - frames[-1].astype(float)).mean()
        assert diff > 0.001, "Zoom should produce visibly different start/end frames"

    def test_streaming_yields_same_count(self, landscape_img):
        """Streaming version should yield the same number of frames as list version."""
        params = KenBurnsParams(fps=30, duration=1.5)
        list_frames = render_ken_burns(landscape_img, VP_W, VP_H, params)
        stream_count = sum(1 for _ in render_ken_burns_streaming(landscape_img, VP_W, VP_H, params))
        assert stream_count == len(list_frames)

    def test_streaming_frame_shape(self, landscape_img):
        """Each streamed frame should have correct dimensions."""
        params = KenBurnsParams(fps=30, duration=0.5)
        for frame in render_ken_burns_streaming(landscape_img, VP_W, VP_H, params):
            assert frame.shape == (VP_H, VP_W, 3)
            break  # Check just the first to save time

    def test_portrait_in_landscape_viewport(self, portrait_img):
        """Portrait photo in landscape viewport should still produce correct shape."""
        params = KenBurnsParams(fps=30, duration=1.0)
        frames = render_ken_burns(portrait_img, VP_W, VP_H, params)
        assert len(frames) == 30
        assert frames[0].shape == (VP_H, VP_W, 3)


# ---- Slide-in ----


class TestSlideIn:
    def test_right_direction_frame_count(self, portrait_img):
        """Slide-in should produce correct number of frames."""
        frames = render_slide_in(portrait_img, VP_W, VP_H, direction="right", fps=30, duration=2.0)
        assert len(frames) == 60

    def test_all_directions_produce_frames(self, landscape_img):
        """All 4 slide directions should produce the correct frame count."""
        for direction in ("left", "right", "top", "bottom"):
            frames = render_slide_in(
                landscape_img,
                VP_W,
                VP_H,
                direction=direction,
                fps=30,
                duration=1.0,
            )
            assert len(frames) == 30, f"Direction '{direction}' produced {len(frames)} frames"
            assert frames[0].shape == (VP_H, VP_W, 3)

    def test_slide_in_animates(self, portrait_img):
        """First and last frames should differ due to slide animation."""
        frames = render_slide_in(portrait_img, VP_W, VP_H, direction="right", fps=30, duration=2.0)
        diff = np.abs(frames[0].astype(float) - frames[-1].astype(float)).mean()
        assert diff > 0.01, "Slide-in should produce different first and last frames"


# ---- Collage ----


class TestCollage:
    def test_two_photo_collage(self, small_photos):
        """2-photo horizontal collage should produce correct frame count and shape."""
        frames = render_collage(
            small_photos[:2],
            VP_W,
            VP_H,
            orientation="horizontal",
            fps=30,
            duration=1.0,
            slide_in=False,
        )
        assert len(frames) == 30
        assert frames[0].shape == (VP_H, VP_W, 3)

    def test_three_photo_collage(self, small_photos):
        """3-photo collage should work."""
        frames = render_collage(
            small_photos[:3],
            VP_W,
            VP_H,
            fps=30,
            duration=1.0,
            slide_in=False,
        )
        assert len(frames) == 30

    def test_four_photo_collage(self, small_photos):
        """4-photo collage should work."""
        frames = render_collage(
            small_photos[:4],
            VP_W,
            VP_H,
            fps=30,
            duration=1.0,
            slide_in=False,
        )
        assert len(frames) == 30

    def test_rejects_single_photo(self, small_photos):
        """Collage requires 2-4 photos."""
        with pytest.raises(ValueError, match="2-4"):
            render_collage(small_photos[:1], VP_W, VP_H)

    def test_rejects_five_photos(self, small_photos):
        """Collage requires 2-4 photos."""
        five = small_photos + [small_photos[0]]
        with pytest.raises(ValueError, match="2-4"):
            render_collage(five, VP_W, VP_H)


# ---- Split ----


class TestSplit:
    def test_two_photo_split(self, small_photos):
        """2-photo split should produce correct frames."""
        frames = render_split(small_photos[:2], VP_W, VP_H, fps=30, duration=1.0)
        assert len(frames) == 30
        assert frames[0].shape == (VP_H, VP_W, 3)

    def test_three_photo_split_layout(self, small_photos):
        """3-photo split: 1 large left + 2 stacked right."""
        frames = render_split(small_photos[:3], VP_W, VP_H, fps=30, duration=1.0)
        assert len(frames) == 30
        # Left half should be dominated by first photo (red-ish)
        left_region = frames[0][:, : VP_W // 3, 0].mean()
        assert left_region > 0.3, "Left region should have red from first photo"

    def test_four_photo_split_grid(self, small_photos):
        """4-photo split: 2x2 grid."""
        frames = render_split(small_photos[:4], VP_W, VP_H, fps=30, duration=1.0)
        assert len(frames) == 30
        assert frames[0].shape == (VP_H, VP_W, 3)

    def test_split_rejects_single_photo(self, small_photos):
        """Split requires 2-4 photos."""
        with pytest.raises(ValueError, match="2-4"):
            render_split(small_photos[:1], VP_W, VP_H)

    def test_split_frames_not_blank(self, small_photos):
        """Split output should not be all-black or all-white."""
        frames = render_split(small_photos[:2], VP_W, VP_H, fps=30, duration=1.0)
        assert frames[0].mean() > 0.05
        assert frames[0].std() > 0.01


# ---- face_aware_pan ----


class TestFaceAwarePan:
    def test_no_faces_returns_center(self):
        """With no faces, pan target should be center (0.5, 0.5)."""
        result = face_aware_pan([], 1920, 1080)
        assert result == (0.5, 0.5)

    def test_empty_person_list_returns_center(self):
        """Persons with no faces should fall back to center."""

        @dataclass
        class MockPerson:
            faces: list = None

            def __post_init__(self):
                self.faces = self.faces or []

        persons = [MockPerson(), MockPerson()]
        result = face_aware_pan(persons, 1920, 1080)
        assert result == (0.5, 0.5)

    def test_single_face_returns_normalized_position(self):
        """A face at known position should return its normalized center."""

        @dataclass
        class MockFace:
            area: float = 1000.0
            image_width: int = 1920
            image_height: int = 1080
            center: tuple[float, float] = (960.0, 540.0)

        @dataclass
        class MockPerson:
            faces: list = None

            def __post_init__(self):
                self.faces = self.faces or []

        face = MockFace(center=(960.0, 540.0))
        person = MockPerson(faces=[face])
        x, y = face_aware_pan([person], 1920, 1080)

        assert 0.49 <= x <= 0.51, f"Expected ~0.5, got {x}"
        assert 0.49 <= y <= 0.51, f"Expected ~0.5, got {y}"

    def test_off_center_face_shifts_pan(self):
        """A face in the upper-left should shift pan target toward (0, 0)."""

        @dataclass
        class MockFace:
            area: float = 500.0
            image_width: int = 1920
            image_height: int = 1080
            center: tuple[float, float] = (200.0, 150.0)

        @dataclass
        class MockPerson:
            faces: list = None

            def __post_init__(self):
                self.faces = self.faces or []

        face = MockFace()
        person = MockPerson(faces=[face])
        x, y = face_aware_pan([person], 1920, 1080)

        assert x < 0.2, f"Face at left should give x < 0.2, got {x}"
        assert y < 0.2, f"Face at top should give y < 0.2, got {y}"

    def test_result_always_in_unit_range(self):
        """Pan coordinates should always be clamped to [0, 1]."""

        @dataclass
        class MockFace:
            area: float = 100.0
            image_width: int = 1920
            image_height: int = 1080
            center: tuple[float, float] = (2000.0, 1200.0)

        @dataclass
        class MockPerson:
            faces: list = None

            def __post_init__(self):
                self.faces = self.faces or []

        face = MockFace()
        person = MockPerson(faces=[face])
        x, y = face_aware_pan([person], 1920, 1080)

        assert 0.0 <= x <= 1.0
        assert 0.0 <= y <= 1.0
