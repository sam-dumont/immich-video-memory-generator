"""Tests for how many photos get LLM-scored.

A real run scored ~1155 photos to place at most a few dozen, taking hours:
max_ratio 0.5 makes max_photos equal the video count rather than capping it,
the shortlist is 3x that again, and live-photo clips inflate the video count
they are derived from.
"""

from __future__ import annotations

from immich_memories.photos.photo_pipeline import _compute_max_photos


class TestComputeMaxPhotos:
    def test_half_ratio_allows_one_photo_per_video(self):
        """0.5 is a share of the finished timeline: 100 photos + 100 videos is half."""
        assert _compute_max_photos(100, 0.50) == 100

    def test_high_ratio_never_exceeds_the_video_count(self):
        """Above 0.5 the raw formula authorises more photos than videos (300 for
        100 at 0.75), which is a licence to LLM-score most of a library."""
        assert _compute_max_photos(100, 0.75) <= 100

    def test_no_videos_still_bounded(self):
        assert _compute_max_photos(0, 0.5) <= 10


class TestLivePhotosDoNotInflateThePhotoBudget:
    """Live-photo clips are made from photos. Counting them as videos lets the
    photo budget grow from the very content it is meant to be balanced against:
    778 live photos became 349 clips, tripling the photo shortlist.
    """

    def test_live_photo_clips_are_excluded_from_the_video_count(self):
        from immich_memories.photos.photo_pipeline import video_count_for_photo_budget

        assert video_count_for_photo_budget(total_clips=398, live_photo_clips=349) == 49

    def test_never_negative_when_all_clips_are_live_photos(self):
        from immich_memories.photos.photo_pipeline import video_count_for_photo_budget

        assert video_count_for_photo_budget(total_clips=349, live_photo_clips=349) == 0
