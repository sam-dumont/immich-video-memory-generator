"""The mechanical judge sees scores, and a photo's score is on another scale.

photos.score_penalty documents the difference outright — "photos score 80% of
videos" — and the judge applied a video-calibrated floor to both. A no-people,
non-favorite photo cannot clear it: 0.15 base + 0.05 camera + half the LLM
weight is 0.28 after the penalty, against a floor of 0.30. Landscapes, pets
and scenery were dropped as a class, and the days only they represented went
with them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineConfig, SmartPipeline
from immich_memories.api.models import AssetType
from immich_memories.config import Config
from immich_memories.config_models_analysis import AnalysisConfig
from tests.conftest import make_clip


def _pipeline(tmp_path: Path) -> SmartPipeline:
    return SmartPipeline(
        client=MagicMock(),
        analysis_cache=MagicMock(),
        thumbnail_cache=MagicMock(),
        config=PipelineConfig(),
        analysis_config=AnalysisConfig(),
        app_config=Config(cache={"directory": str(tmp_path / "cache")}),
    )


def _member(asset_id: str, score: float, *, photo: bool, hour: int = 12) -> ClipWithSegment:
    clip = make_clip(
        asset_id, duration=5.0, file_created_at=datetime(2019, 6, 12, hour, tzinfo=UTC)
    )
    if photo:
        clip.asset.type = AssetType.IMAGE
    return ClipWithSegment(clip=clip, start_time=0.0, end_time=4.0, score=score)


def test_a_landscape_nobody_has_looked_at_yet_is_not_an_offender(tmp_path: Path) -> None:
    """0.280 is what a no-people photo scores before the VLM has an opinion.

    Silence is not a verdict, and this one was being read as one.
    """
    quiet_landscape = _member("landscape", 0.280, photo=True)
    others = [_member(f"v-{i}", 0.6, photo=False, hour=13 + i) for i in range(3)]

    offenders = _pipeline(tmp_path).quality.judge_offenders([quiet_landscape, *others])

    assert "landscape" not in offenders


def test_a_photo_the_model_called_dull_is_still_an_offender(tmp_path: Path) -> None:
    """0.232 is a photo the VLM actively scored 0.3. That is a verdict."""
    dull = _member("dull", 0.232, photo=True)
    others = [_member(f"v-{i}", 0.6, photo=False, hour=13 + i) for i in range(3)]

    offenders = _pipeline(tmp_path).quality.judge_offenders([dull, *others])

    assert "dull" in offenders


def test_a_weak_video_is_still_judged_on_the_video_scale(tmp_path: Path) -> None:
    """Nothing about this loosens the gate for footage."""
    weak = _member("weak-video", 0.28, photo=False)
    others = [_member(f"v-{i}", 0.6, photo=False, hour=13 + i) for i in range(3)]

    offenders = _pipeline(tmp_path).quality.judge_offenders([weak, *others])

    assert "weak-video" in offenders


def test_a_selection_spread_across_time_is_still_judged(tmp_path: Path) -> None:
    """The judge must not be talked out of firing by temporal distribution.

    Sparing "whatever covers a period" reads well and switches the gate off:
    a distributed selection — the goal — makes almost every clip the only one
    of its day, and on a pool with no favorites the coverage ids are every
    clip picked. Both were tried; a gate that never fires is worse than a
    blunt one.
    """
    junk = _member("junk", 0.05, photo=False)
    junk.clip.asset.file_created_at = datetime(2019, 3, 2, 12, tzinfo=UTC)
    spread = []
    for index in range(3):
        member = _member(f"v-{index}", 0.6, photo=False)
        member.clip.asset.file_created_at = datetime(2019, 6 + index, 4, 12, tzinfo=UTC)
        spread.append(member)

    offenders = _pipeline(tmp_path).quality.judge_offenders([junk, *spread])

    assert "junk" in offenders


def _in(month: int, day: int, member: ClipWithSegment) -> ClipWithSegment:
    member.clip.asset.file_created_at = datetime(2019, month, day, 12, tzinfo=UTC)
    return member


def test_an_offender_is_dropped_when_its_period_has_something_better(tmp_path: Path) -> None:
    """The gate is not "this clip is bad", it is "we can do better than this"."""
    weak = _in(3, 2, _member("weak", 0.24, photo=False))
    better = _in(3, 9, _member("same-month-better", 0.7, photo=False))
    elsewhere = [_in(6 + i, 4, _member(f"v-{i}", 0.6, photo=False)) for i in range(2)]

    dropped = _pipeline(tmp_path).quality.spare_last_voices({"weak"}, [weak, better, *elsewhere])

    assert dropped == {"weak"}


def test_a_weak_clip_that_is_its_period_is_kept(tmp_path: Path) -> None:
    """Dropping it loses the month, and the judge purges for good."""
    only_march = _in(3, 2, _member("the-only-march", 0.24, photo=False))
    elsewhere = [_in(6 + i, 4, _member(f"v-{i}", 0.6, photo=False)) for i in range(2)]

    dropped = _pipeline(tmp_path).quality.spare_last_voices(
        {"the-only-march"}, [only_march, *elsewhere]
    )

    assert dropped == set()


def test_an_unusable_clip_is_never_worth_a_period(tmp_path: Path) -> None:
    """A pocket, the ground, somebody's feet. Being alone does not redeem it."""
    ground = _in(3, 2, _member("the-ground", 0.05, photo=False))
    elsewhere = [_in(6 + i, 4, _member(f"v-{i}", 0.6, photo=False)) for i in range(2)]

    dropped = _pipeline(tmp_path).quality.spare_last_voices({"the-ground"}, [ground, *elsewhere])

    assert dropped == {"the-ground"}
