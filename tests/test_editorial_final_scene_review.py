"""The final review also reads the scene a cut repeats, not only the frame it repeats.

An average hash only agrees about two frames of one framing. Two shots of the same path at
dusk, the same couple's selfie a week apart, or the same stage filmed twice in one evening are
framed differently and hash as strangers, yet a viewer sees the film say the same thing twice.
"""

import numpy as np

from immich_memories.analysis.editorial_final_hash_review import review_cut_by_cached_hashes

# Every frame hashes far from every other, so only the scene can call a pair a repeat.
HASHES = {
    "path": "ffffffffffffffff",
    "path-again": "0000000000000000",
    "beach": "ffff0000ffff0000",
    "kitchen": "0000ffff0000ffff",
    "path-third": "00ff00ff00ff00ff",
}
PATH = np.array([1.0, 0.0, 0.0])
PRINTS = {
    "path": PATH,
    "path-again": np.array([0.9, 0.3, 0.0]),  # cosine 0.95 with the path
    "path-third": np.array([0.8, 0.0, 0.6]),  # cosine 0.80 with the path
    "beach": np.array([0.0, 1.0, 0.0]),
    "kitchen": np.array([0.0, 0.0, 1.0]),
}


def _carrier(asset_id, *, day, favourite=False, kind="still", story=None, hour=10):
    return {
        "asset_id": asset_id,
        "taken": f"2024-06-{day:02}T{hour:02}:00:00",
        "favourite": favourite,
        "kind": kind,
        "story_episode": story or f"story-{day}",
        "seconds": 4.0,
    }


def _review(carriers, **kwargs):
    """A film with room: it reaches its target whatever the review takes out."""
    return review_cut_by_cached_hashes(
        carriers,
        thumbnail_hash=HASHES.get,
        scene_print=PRINTS.get,
        **{"content_floor": 0.0, **kwargs},
    )


def _ids(carriers):
    return [c["asset_id"] for c in carriers]


def test_the_same_scene_on_another_day_in_another_story_leaves_the_cut():
    cut = [
        _carrier("path", day=4),
        _carrier("beach", day=6),
        _carrier("path-again", day=11),
    ]

    survivors, record = _review(cut)

    assert _ids(survivors) == ["path", "beach"]
    assert [(r["asset_id"], r["keeper"]) for r in record["removals"]] == [("path-again", "path")]


def test_the_same_scene_past_the_window_is_the_memory_not_an_echo():
    cut = [_carrier("path", day=1), _carrier("beach", day=6), _carrier("path-again", day=20)]

    survivors, record = _review(cut)

    assert _ids(survivors) == ["path", "beach", "path-again"]
    assert record["removals"] == []


def test_a_favourite_is_never_refused_for_the_scene_of_a_picture_the_owner_did_not_star():
    cut = [
        _carrier("path", day=4),
        _carrier("beach", day=6),
        _carrier("path-again", day=11, favourite=True),
    ]

    survivors, record = _review(cut)

    assert _ids(survivors) == ["beach", "path-again"]
    assert record["removals"][0]["asset_id"] == "path"
    assert record["removals"][0]["keeper"] == "path-again"


def test_two_starred_moments_of_one_scene_on_different_days_both_stay():
    cut = [
        _carrier("path", day=4, favourite=True),
        _carrier("beach", day=6),
        _carrier("path-again", day=11, favourite=True),
    ]

    survivors, _ = _review(cut)

    assert _ids(survivors) == ["path", "beach", "path-again"]


def test_two_starred_frames_of_one_scene_on_one_evening_keep_the_one_that_plays():
    """The stage filmed twice in one evening: the true video beats the Live Photo's clip."""
    cut = [
        _carrier("path", day=4, hour=19, favourite=True, kind="live-motion", story="concert"),
        _carrier("path-again", day=4, hour=20, favourite=True, kind="video", story="concert"),
        _carrier("beach", day=6),
    ]

    survivors, record = _review(cut)

    assert _ids(survivors) == ["path-again", "beach"]
    assert record["removals"][0]["same_scene"] == 0.949


def test_a_frame_that_plays_is_never_refused_for_the_scene_of_a_still():
    cut = [
        _carrier("path", day=4, favourite=True),
        _carrier("beach", day=6),
        _carrier("path-again", day=8, kind="video"),
    ]

    survivors, _ = _review(cut)

    assert _ids(survivors) == ["path", "beach", "path-again"]


def test_a_scene_repeat_the_film_can_refill_gives_its_slot_to_something_new():
    cut = [_carrier("path", day=4), _carrier("path-again", day=11)]
    offers = [("story", _carrier("path-third", day=11)), ("story", _carrier("kitchen", day=11))]

    survivors, record = _review(cut, replacements_for=lambda _c: offers)

    assert _ids(survivors) == ["path", "kitchen"]
    assert record["removals"][0]["replacement"] == "kitchen"


def test_a_scene_repeat_stays_when_the_film_would_fall_short_of_its_target_without_it():
    """Keep the better of two only if the film has other material."""
    cut = [_carrier("path", day=4), _carrier("beach", day=6), _carrier("path-again", day=11)]

    survivors, record = _review(cut, content_floor=10.0)

    assert _ids(survivors) == ["path", "beach", "path-again"]
    assert record["removals"] == []


def test_a_film_with_room_for_one_less_repeat_drops_only_that_one():
    cut = [
        _carrier("path", day=4),
        _carrier("path-again", day=8),
        _carrier("path-third", day=11),
    ]

    survivors, record = _review(cut, content_floor=8.0)

    assert _ids(survivors) == ["path", "path-third"]
    assert [r["asset_id"] for r in record["removals"]] == ["path-again"]


def test_without_scene_prints_only_the_hash_is_read():
    cut = [_carrier("path", day=4), _carrier("beach", day=6), _carrier("path-again", day=11)]

    survivors, record = review_cut_by_cached_hashes(cut, thumbnail_hash=HASHES.get)

    assert _ids(survivors) == ["path", "beach", "path-again"]
    assert "scene" not in record


def test_a_frame_with_no_scene_print_is_kept_and_named():
    cut = [_carrier("path", day=4), _carrier("beach", day=6), _carrier("unprinted", day=8)]

    survivors, record = _review(cut)

    assert _ids(survivors) == ["path", "beach", "unprinted"]
    assert record["scene"]["unavailable"] == ["unprinted"]
