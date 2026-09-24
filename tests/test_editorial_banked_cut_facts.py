"""What a cut measures about a picture is banked, and the next cut plans with it."""

from contextlib import closing
from dataclasses import replace

from immich_memories.analysis.editorial_bound_sample import source_metadata_digest
from immich_memories.analysis.editorial_motion_facts import DemandedMotionResolver
from immich_memories.analysis.editorial_preparation_motion import read_motion_residuals
from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_material import build_material, read_wall
from immich_memories.analysis.editorial_structure_planner import plan_structure
from immich_memories.analysis.editorial_structure_record import shave_content_duration
from immich_memories.speech.facts import read_speech_regions, speech_producer
from immich_memories.store.cut_measurements import (
    open_cut_measurements,
    remember_speech_regions,
)
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_event_motion_material import live_source


def unmeasured(tmp_path):
    """One burst of Live Photos nobody has measured, and a bank beside it."""
    captured = live_source(tmp_path, pictures=2, add_context=False)
    return replace(captured, motion_residuals={}, store_path=tmp_path / "annotations.sqlite")


def ports(resolve_motion=None):
    return StructurePlannerPorts(
        judge=ControlledStoryJudge(),
        thumbnail_hash=lambda _: None,
        resolve_motion=resolve_motion,
    )


def planned_units(source, resolve_motion=None):
    """The playable units the planner builds, before anything measures them."""
    planner_ports = ports(resolve_motion)
    material = build_material(source, planner_ports, read_wall(source))
    return [unit for units in material.units.values() for unit in units]


def measuring_resolver(source, downloads):
    return DemandedMotionResolver(
        assets=source.assets,
        cache_path=source.store_path,
        # WHY: Immich playback is the transport; the download is what banking saves.
        fetch_video=lambda video_id: downloads.append(video_id) or b"preview",
        # WHY: the optical flow needs decoded pixels of a real companion; the residual it
        # returns is the fact under test, and 0.4 is a Live Photo that does not play.
        measure=lambda _payload: {"residual": 0.4, "frames": 12},
    )


def test_a_residual_a_cut_measured_is_banked_and_read_by_the_next_plan(tmp_path):
    captured = unmeasured(tmp_path)
    downloads: list[str] = []

    plan = plan_structure(captured, ports(measuring_resolver(captured, downloads))).plan

    assert downloads, "the cut measured what its plan could not know"
    assert plan["carriers"][0]["kind"] == "live-still"
    banked = read_motion_residuals(captured.store_path, captured.assets.values())
    assert {key: value["residual"] for key, value in banked.items()} == dict.fromkeys(
        captured.assets, 0.4
    )

    next_units = planned_units(replace(captured, motion_residuals=banked))

    assert [unit["kind"] for unit in next_units] == ["live-still"]
    assert all(unit["motion_assessed"] for unit in next_units)


def test_a_live_photo_nobody_measured_is_still_planned_as_motion(tmp_path):
    captured = unmeasured(tmp_path)

    units = planned_units(captured, measuring_resolver(captured, []))

    assert [unit["kind"] for unit in units] == ["live-motion"]
    assert not any(unit["motion_assessed"] for unit in units)


def test_a_changed_source_retires_the_residual_banked_for_it(tmp_path):
    captured = unmeasured(tmp_path)
    plan_structure(captured, ports(measuring_resolver(captured, [])))
    changed = {
        key: asset.model_copy(update={"live_photo_video_id": f"replaced-{key}"})
        for key, asset in captured.assets.items()
    }

    assert read_motion_residuals(captured.store_path, captured.assets.values())
    assert read_motion_residuals(captured.store_path, changed.values()) == {}


def bank_a_sentence(captured, regions):
    """Bank one measured sentence for every companion of the burst, as a cut would."""
    producer = speech_producer(captured.config.speech)
    with closing(open_cut_measurements(captured.store_path)) as connection:
        for companion in captured.companion_assets.values():
            remember_speech_regions(
                connection,
                asset_id=companion.id,
                producer=producer,
                source_digest=source_metadata_digest(companion),
                regions=regions,
            )
    return read_speech_regions(captured.store_path, captured.companion_assets.values(), producer)


def test_banked_speech_reaches_the_shave_without_measuring_again(tmp_path):
    captured = unmeasured(tmp_path)
    captured = replace(
        captured,
        motion_residuals=dict.fromkeys(captured.assets, {"residual": 9.0}),
        speech_regions=bank_a_sentence(captured, [(0.4, 2.6)]),
    )

    unit = planned_units(captured)[0]
    assert unit["kind"] == "live-motion"
    assert unit["speech_regions"], "the plan reads the sentence the last cut measured"
    held = unit["seconds"]
    shave_content_duration([unit], 1.0)

    assert unit["seconds"] < held, "a shavable hold still gives its seconds back"
    end = unit.get("start_time", 0.0) + unit["seconds"]
    assert not any(left < end < right for left, right in unit["speech_regions"])


def test_a_clip_with_no_banked_speech_is_planned_exactly_as_before(tmp_path):
    captured = unmeasured(tmp_path)
    captured = replace(captured, motion_residuals=dict.fromkeys(captured.assets, {"residual": 9.0}))

    assert "speech_regions" not in planned_units(captured)[0]


def test_a_changed_source_retires_the_speech_banked_for_it(tmp_path):
    captured = unmeasured(tmp_path)
    producer = speech_producer(captured.config.speech)
    bank_a_sentence(captured, [(0.4, 2.6)])
    changed = [
        companion.model_copy(update={"duration_seconds": 9.5})
        for companion in captured.companion_assets.values()
    ]

    assert read_speech_regions(captured.store_path, changed, producer) == {}
