"""Tests for PhotoAnimator — converts photos to .mp4 clips via FFmpeg."""

from __future__ import annotations

from immich_memories.config_models import PhotoConfig
from immich_memories.photos.animator import PhotoAnimator
from immich_memories.photos.models import AnimationMode


class TestPhotoAnimator:
    """Tests for PhotoAnimator class."""

    def test_build_ffmpeg_command_ken_burns(self, tmp_path):
        """Ken Burns mode builds correct FFmpeg command structure."""
        config = PhotoConfig(duration=4.0)
        animator = PhotoAnimator(config, target_w=1920, target_h=1080)

        source = tmp_path / "photo.jpg"
        source.touch()
        output = tmp_path / "photo.mp4"

        cmd = animator.build_ffmpeg_command(
            source_path=source,
            output_path=output,
            width=4000,
            height=3000,
            mode=AnimationMode.KEN_BURNS,
        )

        assert cmd[0] == "ffmpeg"
        assert "-i" in cmd
        assert str(source) in cmd
        assert str(output) in cmd
        # Should have -vf with zoompan
        vf_idx = cmd.index("-vf")
        assert "zoompan=" in cmd[vf_idx + 1]

    def test_build_ffmpeg_command_face_zoom(self, tmp_path):
        """Face zoom mode includes crop in filter."""
        config = PhotoConfig(duration=4.0)
        animator = PhotoAnimator(config, target_w=1920, target_h=1080)

        source = tmp_path / "photo.jpg"
        source.touch()
        output = tmp_path / "photo.mp4"

        cmd = animator.build_ffmpeg_command(
            source_path=source,
            output_path=output,
            width=4000,
            height=3000,
            mode=AnimationMode.FACE_ZOOM,
            face_bbox=(0.3, 0.2, 0.4, 0.5),
        )

        vf_idx = cmd.index("-vf")
        assert "crop=" in cmd[vf_idx + 1]

    def test_build_ffmpeg_command_blur_bg(self, tmp_path):
        """Blur background mode uses filter_complex instead of -vf."""
        config = PhotoConfig(duration=4.0)
        animator = PhotoAnimator(config, target_w=1920, target_h=1080)

        source = tmp_path / "photo.jpg"
        source.touch()
        output = tmp_path / "photo.mp4"

        cmd = animator.build_ffmpeg_command(
            source_path=source,
            output_path=output,
            width=1080,
            height=1920,
            mode=AnimationMode.BLUR_BG,
        )

        # blur_bg uses filter_complex because it has multiple streams
        assert "-filter_complex" in cmd
        vf_idx = cmd.index("-filter_complex")
        assert "boxblur=" in cmd[vf_idx + 1]

    def test_build_ffmpeg_command_adds_silent_audio(self, tmp_path):
        """Output includes silent audio track for assembly compatibility."""
        config = PhotoConfig(duration=4.0)
        animator = PhotoAnimator(config, target_w=1920, target_h=1080)

        source = tmp_path / "photo.jpg"
        source.touch()
        output = tmp_path / "photo.mp4"

        cmd = animator.build_ffmpeg_command(
            source_path=source,
            output_path=output,
            width=1920,
            height=1080,
            mode=AnimationMode.KEN_BURNS,
        )

        # Should include anullsrc for silent audio
        cmd_str = " ".join(cmd)
        assert "anullsrc" in cmd_str

    def test_output_duration_matches_config(self, tmp_path):
        """FFmpeg command limits output to configured duration."""
        config = PhotoConfig(duration=5.0)
        animator = PhotoAnimator(config, target_w=1920, target_h=1080)

        source = tmp_path / "photo.jpg"
        source.touch()
        output = tmp_path / "photo.mp4"

        cmd = animator.build_ffmpeg_command(
            source_path=source,
            output_path=output,
            width=1920,
            height=1080,
            mode=AnimationMode.KEN_BURNS,
        )

        t_idx = cmd.index("-t")
        assert cmd[t_idx + 1] == "5.0"

    def test_seed_from_asset_id(self):
        """Seed is derived from asset_id for reproducible randomness."""
        config = PhotoConfig()
        animator = PhotoAnimator(config, target_w=1920, target_h=1080)
        seed = animator._seed_from_id("abc-123")
        # Same ID always produces same seed
        assert seed == animator._seed_from_id("abc-123")
        # Different IDs produce different seeds
        assert seed != animator._seed_from_id("xyz-456")

    def test_auto_mode_selects_ken_burns_for_landscape(self, tmp_path):
        """AUTO mode resolves to KEN_BURNS for landscape photos without faces."""
        config = PhotoConfig()
        animator = PhotoAnimator(config, target_w=1920, target_h=1080)

        resolved = animator.resolve_auto_mode(width=1920, height=1080, face_bbox=None)
        assert resolved == AnimationMode.KEN_BURNS

    def test_auto_mode_selects_face_zoom_with_faces(self, tmp_path):
        """AUTO mode resolves to FACE_ZOOM when face bbox is present."""
        config = PhotoConfig()
        animator = PhotoAnimator(config, target_w=1920, target_h=1080)

        resolved = animator.resolve_auto_mode(
            width=1920, height=1080, face_bbox=(0.3, 0.2, 0.4, 0.5)
        )
        assert resolved == AnimationMode.FACE_ZOOM

    def test_auto_mode_selects_blur_bg_for_portrait(self, tmp_path):
        """AUTO mode resolves to BLUR_BG for portrait photos."""
        config = PhotoConfig()
        animator = PhotoAnimator(config, target_w=1920, target_h=1080)

        resolved = animator.resolve_auto_mode(width=1080, height=1920, face_bbox=None)
        assert resolved == AnimationMode.BLUR_BG
