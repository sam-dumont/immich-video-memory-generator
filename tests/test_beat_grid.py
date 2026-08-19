"""Tempo chosen so the photo cut cadence lands on whole beats."""

from __future__ import annotations

from pathlib import Path

from immich_memories.audio.beat_grid import beat_aligned_bpm
from immich_memories.generate_music import photo_cadence_seconds
from immich_memories.processing.assembly_config import AssemblyClip


class TestBeatAlignedBpm:
    def test_cadence_becomes_a_whole_number_of_beats(self):
        bpm = beat_aligned_bpm(natural_bpm=123, cadence_seconds=4.0, tempo_range=(72, 150))

        beats = cadence_in_beats(bpm, 4.0)
        assert abs(beats - round(beats)) < 1e-9


def cadence_in_beats(bpm: float, cadence_seconds: float) -> float:
    return cadence_seconds / (60.0 / bpm)


class TestNearestTempo:
    def test_picks_the_aligned_tempo_closest_to_the_natural_one(self):
        """4.0s is 8 beats at 120 and 9 at 135; 123 should land on 120."""
        assert beat_aligned_bpm(natural_bpm=123, cadence_seconds=4.0, tempo_range=(72, 150)) == 120

    def test_never_leaves_the_styles_tempo_range(self):
        """A 150-bpm jazz track would be aligned and wrong."""
        bpm = beat_aligned_bpm(natural_bpm=70, cadence_seconds=4.0, tempo_range=(68, 132))

        assert 68 <= bpm <= 132

    def test_falls_back_to_the_natural_tempo_when_nothing_aligns_in_range(self):
        """A narrow range can exclude every whole-beat tempo; the mood still wins."""
        bpm = beat_aligned_bpm(natural_bpm=100, cadence_seconds=0.37, tempo_range=(99, 101))

        assert bpm == 100


def _clip(duration: float, *, is_photo: bool) -> AssemblyClip:
    return AssemblyClip(path=Path("/x.mp4"), duration=duration, asset_id="x", is_photo=is_photo)


class TestPhotoCadence:
    def test_reads_the_rendered_photo_length_not_the_config_value(self):
        """The final budget trim scales every clip, so config.photos.duration lies."""
        clips = [_clip(3.7, is_photo=True), _clip(3.7, is_photo=True), _clip(9.1, is_photo=False)]

        assert photo_cadence_seconds(clips) == 3.7

    def test_a_single_photo_has_no_cadence(self):
        """One photo is not a rhythm; syncing tempo to it would be arbitrary."""
        clips = [_clip(4.0, is_photo=True), _clip(9.1, is_photo=False)]

        assert photo_cadence_seconds(clips) is None


class TestCaptionTempoFollowsTheCadence:
    """The caption is where the tempo is decided, so that is where it must adapt."""

    def test_tempo_shifts_so_the_cadence_is_whole_beats(self):
        from immich_memories.audio.generators.ace_step_captions import (
            build_ace_caption_structured,
        )

        natural = build_ace_caption_structured("happy", style="electronic")
        aligned = build_ace_caption_structured("happy", style="electronic", cadence_seconds=4.0)

        beats = 4.0 / (60.0 / aligned.bpm)
        assert abs(beats - round(beats)) < 0.05
        assert aligned.bpm != natural.bpm

    def test_the_stated_tempo_matches_the_one_we_ask_the_model_for(self):
        """The caption text conditions the model; a mismatch would fight itself."""
        from immich_memories.audio.generators.ace_step_captions import (
            build_ace_caption_structured,
        )

        result = build_ace_caption_structured("happy", style="electronic", cadence_seconds=4.0)

        assert f"{result.bpm} bpm" in result.caption

    def test_no_cadence_leaves_the_mood_tempo_untouched(self):
        from immich_memories.audio.generators.ace_step_captions import (
            build_ace_caption_structured,
        )

        assert (
            build_ace_caption_structured("calm", style="acoustic").bpm
            == build_ace_caption_structured("calm", style="acoustic", cadence_seconds=None).bpm
        )


class TestCadenceReachesTheModel:
    """The tempo is only useful if it survives the trip to the backend."""

    def test_the_requested_bpm_is_aligned_to_the_photo_cadence(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        import httpx

        from immich_memories.audio.generators.ace_step_backend import (
            ACEStepBackend,
            ACEStepConfig,
        )
        from immich_memories.audio.generators.base import GenerationRequest

        backend = ACEStepBackend(ACEStepConfig(mode="api", api_url="http://fake:8000"))
        payload: dict = {}

        async def fake_post(url, json=None, **kwargs):
            if "/release_task" in url:
                payload.update(json)
                resp = MagicMock()
                resp.json.return_value = {"data": {"task_id": "t"}}
                resp.raise_for_status = MagicMock()
                return resp
            raise httpx.HTTPError("unexpected url")

        with (
            # WHY: replaces the poll-and-download loop against a real ACE-Step server.
            patch.object(backend, "_poll_and_download", new_callable=AsyncMock),
            # WHY: replaces the HTTP transport so the request never leaves the process.
            patch("httpx.AsyncClient") as client_cls,
        ):
            client = AsyncMock()
            client.post = fake_post
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            client_cls.return_value = client

            asyncio.run(
                backend._generate_api(
                    GenerationRequest(
                        prompt="happy",
                        duration_seconds=30,
                        output_dir=Path("/tmp/beat_grid_test"),
                        photo_cadence_seconds=4.0,
                    )
                )
            )

        beats = 4.0 / (60.0 / payload["bpm"])
        assert abs(beats - round(beats)) < 0.05, f"{payload['bpm']} bpm leaves {beats} beats"


class TestMoodOutranksAlignment:
    """Alignment is a nicety; the mood's tempo is the point of the track."""

    def test_a_short_cadence_does_not_drag_a_calm_mood_up_to_dance_tempo(self):
        """1s photos are 1 beat at 60 and 2 at 120; neither belongs under "serene"."""
        bpm = beat_aligned_bpm(natural_bpm=68, cadence_seconds=1.0, tempo_range=(68, 132))

        assert bpm == 68

    def test_a_small_nudge_is_still_taken(self):
        assert beat_aligned_bpm(natural_bpm=123, cadence_seconds=4.0, tempo_range=(72, 150)) == 120
