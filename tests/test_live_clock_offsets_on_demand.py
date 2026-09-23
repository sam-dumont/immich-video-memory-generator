"""A Live burst's clock offsets are measured for the cut, once, and banked for every next one."""

from dataclasses import replace
from datetime import timedelta

import numpy as np

from immich_memories.analysis.editorial_source_route import project_source_rendering
from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_material import build_material, read_wall
from immich_memories.analysis.editorial_structure_planner import plan_structure
from immich_memories.analysis.live_clock_offsets import BankedClockOffsets
from immich_memories.analysis.motion_rendering import motion_renderings
from tests.conftest import make_asset
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_duration_planner_integration import source
from tests.test_editorial_source_route import demand

GAP_SECONDS = 2.0
# The second companion's clock starts earlier than the metadata midpoint puts it.
MEASURED_SECONDS = 1.8


def bursts(tmp_path, count):
    """`count` two-picture Live bursts, an hour apart, that nobody has measured."""
    captured = source(tmp_path, seconds=15, pictures=2 * count)
    assets = dict(captured.assets)
    ordered = sorted(assets.values(), key=lambda asset: asset.file_created_at)
    start = ordered[0].file_created_at
    for index, asset in enumerate(ordered):
        burst, member = divmod(index, 2)
        asset.file_created_at = start + timedelta(hours=burst, seconds=GAP_SECONDS * member)
        asset.live_photo_video_id = f"video-{index}"
    return replace(
        captured,
        assets=assets,
        companion_assets={
            asset.live_photo_video_id: make_asset(asset.live_photo_video_id, duration=3.0)
            for asset in assets.values()
        },
        motion_residuals={key: {"residual": 9.0} for key in assets},
        store_path=tmp_path / "annotations.sqlite",
    )


def counting_offsets(asked):
    """Every join measures the same offset; each question is written down."""

    def offsets(video_ids):
        asked.append(tuple(video_ids))
        return [MEASURED_SECONDS] * (len(video_ids) - 1)

    return offsets


def ports(clock_offsets):
    return StructurePlannerPorts(
        judge=ControlledStoryJudge(),
        thumbnail_hash=lambda _: None,
        rank=lambda _query, documents: dict.fromkeys(range(len(documents)), 1.0),
        reranker_identity={"endpoint": "test://local", "model": "controlled-ranker"},
        clock_offsets=clock_offsets,
    )


def live_carriers(plan):
    return [c for c in plan["carriers"] if str(c["kind"]).startswith("live")]


def test_a_draft_plans_every_burst_without_measuring_one(tmp_path):
    captured = bursts(tmp_path, 6)
    asked: list[tuple[str, ...]] = []

    material = build_material(captured, ports(counting_offsets(asked)), read_wall(captured))

    units = [unit for units in material.units.values() for unit in units]
    assert asked == [], "no companion is downloaded before the draft exists"
    assert sum(unit["kind"].startswith("live") for unit in units) == 6


def test_the_cut_measures_only_the_bursts_it_keeps(tmp_path):
    captured = bursts(tmp_path, 12)
    asked: list[tuple[str, ...]] = []

    plan = plan_structure(captured, ports(counting_offsets(asked))).plan

    kept = {tuple(c["video_ids"]) for c in live_carriers(plan)}
    assert kept, "the film keeps at least one burst"
    assert set(asked) == kept
    assert len(kept) < 12


def test_a_kept_burst_gets_the_trims_measuring_everything_first_gave_it(tmp_path):
    captured = bursts(tmp_path, 3)
    offsets = counting_offsets([])

    plan = plan_structure(captured, ports(offsets)).plan

    upfront = motion_renderings(
        list(captured.assets.values()),
        captured.config,
        companion_assets=captured.companion_assets,
        clock_offsets=offsets,
    )
    assert live_carriers(plan)
    for carrier in live_carriers(plan):
        rendering = upfront[carrier["asset_id"]]
        assert carrier["trim_points"] == [list(pair) for pair in rendering.trim_points]
        assert carrier["raw_seconds"] == round(rendering.duration_seconds, 2)

    # The renderer re-derives the stitch with the same probe and accepts the manifest.
    _, candidates = demand(list(captured.assets.values()))
    projected = project_source_rendering(
        plan["carriers"],
        candidates,
        config=captured.config,
        include_live_photos=True,
        companion_assets=captured.companion_assets,
        clock_offsets=offsets,
    )
    shipped = {row.clip.asset.id: row.clip for row in projected.candidates}
    playing = [c for c in live_carriers(plan) if c["kind"] == "live-motion"]
    assert playing
    for carrier in playing:
        trims = shipped[carrier["asset_id"]].live_burst_trim_points
        assert [list(pair) for pair in trims] == carrier["trim_points"]


def test_a_kept_burst_the_measurement_refuses_ships_as_its_photograph(tmp_path):
    captured = bursts(tmp_path, 3)

    plan = plan_structure(captured, ports(lambda video_ids: [None] * (len(video_ids) - 1))).plan

    assert plan["carriers"], "a refused stitch still leaves a film"
    assert live_carriers(plan) == []
    assert all(c["video_ids"] == [] and c["trim_points"] == [] for c in plan["carriers"])


def moving_frames(offset):
    """A textured scene panning one column a frame, starting `offset` frames in."""
    base = (np.random.default_rng(7).random((36, 64)) * 200 + 20).astype(np.float32)
    return np.stack([np.roll(base, index + offset, axis=1) for index in range(40)])


class Library:
    """The Immich playback transport and the frame decoder, both counted."""

    def __init__(self, starts):
        self.starts = starts
        self.downloads: list[str] = []
        self.decodes = 0

    def fetch(self, video_id):
        self.downloads.append(video_id)
        return video_id.encode()

    def frames(self, payload):
        self.decodes += 1
        start = self.starts[payload.decode()]
        if start is None:
            return (
                np.random.default_rng(len(payload))
                .integers(0, 255, (40, 36, 64))
                .astype(np.float32)
            )
        return moving_frames(start)


def banked_offsets(tmp_path, library, companions):
    return BankedClockOffsets(
        store_path=tmp_path / "annotations.sqlite",
        companions=companions,
        # WHY: Immich playback is the transport; a download is what the bank saves.
        fetch=library.fetch,
        # WHY: decoding needs ffmpeg and real companion bytes; the frames stand in for them.
        frames=library.frames,
    )


def companions_of(*video_ids):
    return {video_id: make_asset(video_id, duration=3.0) for video_id in video_ids}


def test_each_companion_of_a_burst_is_downloaded_and_decoded_once(tmp_path):
    library = Library({"a": 0, "b": 9, "c": 18})

    measured = banked_offsets(tmp_path, library, companions_of("a", "b", "c"))(["a", "b", "c"])

    assert measured == [0.6, 0.6]
    assert sorted(library.downloads) == ["a", "b", "c"]
    assert library.decodes == 3


def test_a_second_cut_over_the_same_live_photos_downloads_nothing(tmp_path):
    companions = companions_of("a", "b", "c")
    first = Library({"a": 0, "b": 9, "c": 18})
    measured = banked_offsets(tmp_path, first, companions)(["a", "b", "c"])
    second = Library({"a": 0, "b": 9, "c": 18})

    again = banked_offsets(tmp_path, second, companions)(["a", "b", "c"])

    assert again == measured
    assert second.downloads == []
    assert second.decodes == 0


def test_a_refused_join_is_an_answer_the_next_cut_reads(tmp_path):
    companions = companions_of("a", "noise")
    banked_offsets(tmp_path, Library({"a": 0, "noise": None}), companions)(["a", "noise"])
    second = Library({"a": 0, "noise": None})

    assert banked_offsets(tmp_path, second, companions)(["a", "noise"]) == [None]
    assert second.downloads == []


def test_a_changed_companion_is_measured_again(tmp_path):
    companions = companions_of("a", "b")
    banked_offsets(tmp_path, Library({"a": 0, "b": 9}), companions)(["a", "b"])
    edited = companions | {"b": make_asset("b", duration=2.5)}
    second = Library({"a": 0, "b": 9})

    banked_offsets(tmp_path, second, edited)(["a", "b"])

    assert sorted(second.downloads) == ["a", "b"]


def test_a_download_that_fails_is_not_banked_as_a_refusal(tmp_path):
    companions = companions_of("a", "b")
    failing = Library({"a": 0, "b": 9})

    def unreachable(video_id):
        raise OSError("connection reset")

    failing.fetch = unreachable
    assert banked_offsets(tmp_path, failing, companions)(["a", "b"]) == [None]
    second = Library({"a": 0, "b": 9})

    assert banked_offsets(tmp_path, second, companions)(["a", "b"]) == [0.6]
    assert sorted(second.downloads) == ["a", "b"]
