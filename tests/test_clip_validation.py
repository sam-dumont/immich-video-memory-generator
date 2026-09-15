"""Tests for clip validation — filtering out bad clips before assembly."""

from __future__ import annotations

from pathlib import Path

from immich_memories.processing.assembly_config import AssemblyClip


def _make_clip(path: Path, duration: float = 3.0, asset_id: str = "test") -> AssemblyClip:
    return AssemblyClip(path=path, duration=duration, asset_id=asset_id)


class TestValidateClips:
    """validate_clips should filter out files that don't exist or can't be probed."""

    def test_removes_nonexistent_files(self, tmp_path: Path):
        from immich_memories.processing.clip_validation import validate_clips

        good = tmp_path / "good.mp4"
        good.write_bytes(b"\x00" * 100)  # exists but not a real video
        bad = tmp_path / "missing.mp4"  # doesn't exist

        clips = [_make_clip(good, asset_id="good"), _make_clip(bad, asset_id="bad")]
        valid, skipped = validate_clips(clips)

        assert len(valid) == 1
        assert valid[0].asset_id == "good"
        assert len(skipped) == 1
        assert skipped[0].asset_id == "bad"

    def test_removes_zero_byte_files(self, tmp_path: Path):
        from immich_memories.processing.clip_validation import validate_clips

        empty = tmp_path / "empty.mp4"
        empty.write_bytes(b"")
        good = tmp_path / "good.mp4"
        good.write_bytes(b"\x00" * 100)

        clips = [_make_clip(empty, asset_id="empty"), _make_clip(good, asset_id="good")]
        valid, skipped = validate_clips(clips)

        assert len(valid) == 1
        assert valid[0].asset_id == "good"

    def test_all_valid_returns_all(self, tmp_path: Path):
        from immich_memories.processing.clip_validation import validate_clips

        a = tmp_path / "a.mp4"
        b = tmp_path / "b.mp4"
        a.write_bytes(b"\x00" * 100)
        b.write_bytes(b"\x00" * 100)

        clips = [_make_clip(a, asset_id="a"), _make_clip(b, asset_id="b")]
        valid, skipped = validate_clips(clips)

        assert len(valid) == 2
        assert len(skipped) == 0

    def test_empty_input(self):
        from immich_memories.processing.clip_validation import validate_clips

        valid, skipped = validate_clips([])
        assert valid == []
        assert skipped == []

    def test_preserves_title_screens(self, tmp_path: Path):
        """Title screen clips with missing paths should still be filtered."""
        from immich_memories.processing.clip_validation import validate_clips

        title = tmp_path / "title.mp4"
        title.write_bytes(b"\x00" * 100)
        clip = AssemblyClip(path=title, duration=3.0, asset_id="title", is_title_screen=True)
        valid, skipped = validate_clips([clip])
        assert len(valid) == 1


class TestGenerateMemoryValidation:
    """generate_memory should validate clips before assembly."""

    def test_generation_rejects_a_missing_extracted_file(self, tmp_path, monkeypatch):
        import pytest

        from immich_memories import generate_render
        from immich_memories.config import Config
        from immich_memories.generate import GenerationError, GenerationParams, generate_memory
        from immich_memories.tracking import RunTracker
        from tests.conftest import make_clip

        config = Config(cache={"directory": str(tmp_path / "cache"), "video_cache_enabled": False})
        tracker = RunTracker(
            "missing-source", db_path=tmp_path / "runs.sqlite", capture_system=False
        )
        clip = make_clip()
        params = GenerationParams(
            clips=[clip], output_path=tmp_path / "film.mp4", config=config, no_music=True
        )
        # WHY: simulate an extraction writer losing its file before assembly validates it.
        monkeypatch.setattr(
            generate_render,
            "_extract_clips_with_optional_prefetch",
            lambda *_args, **_kw: [_make_clip(tmp_path / "missing.mp4", asset_id=clip.asset.id)],
        )
        with pytest.raises(GenerationError, match="No clips could be processed"):
            generate_memory(params, run_tracker=tracker)
        assert not params.output_path.exists()
        assert tracker.db.get_run(tracker.run_id).status == "failed"

    def test_validate_clips_filters_bad_clips_in_pipeline(self, tmp_path: Path):
        """Integration: validate_clips removes missing-file clips before assembly."""
        from immich_memories.processing.clip_validation import validate_clips

        good = tmp_path / "good.mp4"
        good.write_bytes(b"\x00" * 100)
        bad = tmp_path / "missing.mp4"  # doesn't exist

        clips = [
            _make_clip(good, asset_id="good"),
            _make_clip(bad, asset_id="bad"),
        ]
        valid, skipped = validate_clips(clips)
        assert len(valid) == 1
        assert skipped[0].asset_id == "bad"
