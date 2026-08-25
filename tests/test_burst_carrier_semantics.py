"""A live-photo carrier falls between three definitions of what it is.

To the photo pool it is not a photo — `semantic_payloads_for` reads
asset_scores, and a burst has no row there. To verify it is not a still —
`is_a_still` excludes anything with a video component, so it goes to the video
analyser, fails in milliseconds, and is marked attempted for good. To the video
cache reader it is not a video, because the segments are stored under the
burst's leader, not the carrier.

A pain-cave clip shipped at 0.34 against a 0.49 bar in a run that dropped five
other screens — purely because the label never reached it. The label existed
the whole time: the leader's segment row says category=screen, "a television
screen displays a professional cyclist in a yellow helmet".
"""

from __future__ import annotations

from datetime import UTC, datetime

from immich_memories.analysis.selection_quality import looks_like_a_photograph
from immich_memories.api.models import Asset, AssetType

WHEN = datetime(2023, 6, 14, 15, 7, tzinfo=UTC)


def _asset(asset_id: str, *, kind: AssetType, live: bool = False) -> Asset:
    return Asset(
        id=asset_id,
        type=kind,
        fileCreatedAt=WHEN,
        fileModifiedAt=WHEN,
        updatedAt=WHEN,
        livePhotoVideoId="vid" if live else None,
    )


class TestTheLookGoesWhereItCanWork:
    def test_a_live_photo_carrier_is_looked_at_as_a_photograph(self):
        """Its still is an image, and the photo scorer can read it.

        Sending it to the video analyser fails in milliseconds and marks it
        attempted, which is permanent blindness.
        """
        assert looks_like_a_photograph(_asset("burst", kind=AssetType.IMAGE, live=True))

    def test_a_plain_photograph_still_is(self):
        assert looks_like_a_photograph(_asset("still", kind=AssetType.IMAGE))

    def test_real_footage_is_not(self):
        assert not looks_like_a_photograph(_asset("clip", kind=AssetType.VIDEO))


class TestTheBurstsOwnWordsReachThePool:
    def test_a_carrier_takes_the_payload_stored_under_its_burst(self, tmp_path):
        """The segments live under the burst's leader, not under the carrier."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from immich_memories.photos.scoring import semantic_payloads_for_bursts

        leader = SimpleNamespace(
            segments=[
                SimpleNamespace(
                    total_score=0.2,
                    llm_description="a dim room",
                    llm_category="object",
                    llm_interestingness=0.3,
                    llm_emotion=None,
                    llm_setting=None,
                    llm_subjects=None,
                    llm_activities=None,
                    llm_quality=None,
                ),
                SimpleNamespace(
                    total_score=0.4,
                    llm_description="a television screen shows a cyclist",
                    llm_category="screen",
                    llm_interestingness=0.6,
                    llm_emotion=None,
                    llm_setting=None,
                    llm_subjects=None,
                    llm_activities=None,
                    llm_quality=None,
                ),
            ]
        )
        cache = MagicMock()
        # WHY: the analysis cache is a database read; the unit here is which
        # segment of which burst member answers for the carrier.
        cache.get_analysis.side_effect = lambda asset_id: leader if asset_id == "leader" else None

        # WHY: the analysis cache is a SQLite read; the unit here is which
        # segment of which burst member answers for the carrier.
        with patch("immich_memories.cache.database.VideoAnalysisCache", return_value=cache):
            found = semantic_payloads_for_bursts(
                tmp_path / "cache.db", {"carrier": ("missing", "leader")}
            )

        assert found["carrier"]["category"] == "screen"
        assert found["carrier"]["description"] == "a television screen shows a cyclist"

    def test_a_burst_nothing_has_analysed_yields_nothing(self):
        """Silence stays silence — it must not be invented."""
        from unittest.mock import MagicMock, patch

        from immich_memories.photos.scoring import semantic_payloads_for_bursts

        cache = MagicMock()
        cache.get_analysis.return_value = None
        # WHY: the analysis cache is a SQLite read.
        with patch("immich_memories.cache.database.VideoAnalysisCache", return_value=cache):
            assert semantic_payloads_for_bursts("db", {"carrier": ("a", "b")}) == {}
