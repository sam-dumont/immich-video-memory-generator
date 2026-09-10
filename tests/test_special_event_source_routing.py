"""Canonical events remain exact from discovery through production and wall source.

The probe and matrix script inspections stay on the probe branch.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from immich_memories.analysis.editorial_runtime import (
    EditorialRunContext,
    build_editorial_planner,
)
from immich_memories.analysis.editorial_runtime_ports import EditorialRuntimePorts
from immich_memories.analysis.editorial_source import fetch_full_window_source
from immich_memories.analysis.editorial_structure_contract import StructurePlanningResult
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.analysis.special_event_scope import (
    SpecialEventAdmission,
    validate_special_event_scope,
)
from immich_memories.automation.catalogue import entries_from
from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange
from tests.conftest import make_asset, make_clip
from tests.test_editorial_runtime import _create_annotation_store

WINDOW = DateRange(datetime(2020, 6, 14, tzinfo=UTC), datetime(2020, 6, 14, 23, 59, tzinfo=UTC))


def event_id(members):
    digest = hashlib.sha256(json.dumps(sorted(members), separators=(",", ":")).encode()).hexdigest()
    return f"special-event-{digest[:24]}"


def record(members, key="event"):
    return {
        "key": key,
        "label": "An occasion",
        "product": "special_day",
        "brief": "A memory.",
        "target_seconds": 60,
        "ranges": [
            {"start": WINDOW.start.date().isoformat(), "end": WINDOW.end.date().isoformat()}
        ],
        "event_id": event_id(members),
        "asset_ids": list(members),
        "what": "An occasion",
        "start": WINDOW.start.isoformat(),
        "end": WINDOW.end.isoformat(),
    }


def load_script(name):
    path = Path(__file__).parents[1] / "scripts" / name
    if str(path.parents[0]) not in sys.path:
        sys.path.insert(0, str(path.parents[0]))
    spec = importlib.util.spec_from_file_location(name.replace("/", "_"), path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "identity,members",
    [
        (None, ("asset",)),
        (event_id(("asset",)), ()),
        (event_id(("asset",)), ("other",)),
        (event_id(("asset",)), ("asset", "asset")),
    ],
)
def test_incomplete_or_changed_membership_cannot_become_a_day(identity, members):
    with pytest.raises(ValueError):
        validate_special_event_scope(identity, members)


def test_catalogue_rejects_missing_exact_window_instead_of_using_day(tmp_path):
    path = tmp_path / "catalogue.json"
    path.write_text(
        json.dumps([{"day": "2020-06-14", "event_id": event_id(("a",)), "asset_ids": ["a"]}])
    )
    with pytest.raises(ValueError, match="exact start and end"):
        entries_from(path)


def test_exact_source_filters_overreturn_before_annotation_and_keeps_live_link():
    still = make_asset("race", file_created_at=WINDOW.start)
    still.live_photo_video_id = "race-motion"
    other = make_asset("festival", file_created_at=WINDOW.start)
    standalone = make_asset("race-video", file_created_at=WINDOW.start)
    companion = make_asset("race-motion", file_created_at=WINDOW.start)

    class Client:
        def get_videos_for_date_range(self, dates):
            return [companion, standalone]

        def get_photos_for_date_range(self, dates, person_id=None, person_ids=None):
            return [other, still]

    members = ("race", "race-video")
    sources = fetch_full_window_source(
        Client(), SourceScope(date_ranges=(WINDOW,), asset_ids=members)
    )
    assert {s.id for s in sources} == set(members)
    assert next(s for s in sources if s.id == "race").live_photo_video_id == "race-motion"
    seen_by_evidence = []
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(SourceScope(date_ranges=(WINDOW,), asset_ids=members)),
        EditorialDependencies(
            source_fetcher=lambda _scope: (other, still, companion, standalone),
            source_evidence=lambda source: seen_by_evidence.append(source.id),
        ),
    )
    assert set(prepared.candidate_ids) == set(members)
    assert set(seen_by_evidence) == set(members)
    assert set(prepared.excluded_ids) == {"festival", "race-motion"}
    assert (
        fetch_full_window_source(
            Client(), SourceScope(date_ranges=(WINDOW,), asset_ids=("missing",))
        )
        == ()
    )
    assert len(fetch_full_window_source(Client(), SourceScope(date_ranges=(WINDOW,)))) == 4


@pytest.mark.parametrize("member", ["race", "festival"])
@pytest.mark.parametrize("admitted", [False, True])
def test_production_wall_receives_only_selected_event_even_if_port_returns_whole_day(
    tmp_path, member, admitted
):
    clips = {
        name: make_clip(name, file_created_at=WINDOW.start.replace(hour=10))
        for name in ("race", "festival")
    }
    store = tmp_path / "annotations.sqlite"
    _create_annotation_store(store, {member: "People share an occasion."})
    config = Config(
        llm={"model": "fake-model"},
        editorial={
            "enabled": True,
            "annotation_database": str(store),
            "description_model": "student-v1",
        },
    )
    context = EditorialRunContext(
        key="same-date",
        label="An occasion",
        product="special_day",
        date_ranges=(WINDOW,),
        target_seconds=60,
        artifact_dir=tmp_path / "artifacts",
        special_event_id=event_id((member,)),
        event_asset_ids=(member,),
        event_admission=SpecialEventAdmission.from_catalogue_record(
            record((member,)), evidence_ref="private/catalogue.json"
        )
        if admitted
        else None,
    )
    captured = []

    def plan(source, effects):
        captured.append(source)
        assert tuple(source.assets) == (member,)
        assert set(source.annotations) == {member}
        assert {key for ids in source.moment_asset_ids.values() for key in ids} == {member}
        assert source.case.special_event_id == context.special_event_id
        assert source.case.event_asset_ids == (member,)
        assert source.case.event_admission == context.event_admission
        assert ("already admitted event" in source.intent.narrative_objective) == admitted
        return StructurePlanningResult(
            {
                "carriers": [
                    {
                        "asset_id": member,
                        "taken": WINDOW.start.replace(hour=10).isoformat(),
                        "kind": "video",
                    }
                ]
            },
            "contract",
            "sheet",
            {},
        )

    episode = {
        "schema_version": "episode-reading-text-v1",
        "episodes": [
            {
                "episode": 1,
                "what_happened": "People share an occasion.",
                "representatives": [{"asset": 1, "reason": "The occasion."}],
                "cull": [],
            }
        ],
    }
    period = {
        "schema_version": "period-insight-text-v1",
        "thesis": "An occasion.",
        "evidence": [{"observation": "People meet.", "episodes": [1]}],
        "tensions": [],
        "recurring_threads": [],
    }
    planner = build_editorial_planner(
        client=object(),
        config=config,
        thumbnail_cache=object(),
        context=context,
        ports=EditorialRuntimePorts(
            load_people=lambda: {},
            fetch_full_source=lambda _client, _scope: tuple(clips.values()),
            episode_requester_factory=lambda _config: lambda _prompt: json.dumps(episode),
            period_requester_factory=lambda _config: lambda _prompt: json.dumps(period),
            structure_planner=plan,
            structure_ports_factory=lambda _source: object(),
        ),
    )
    result = planner.plan(
        tuple(ClipWithSegment(clip, 0, 5, 1) for clip in clips.values()), trace=Trace()
    )
    assert [row.asset_id for row in result.selections] == [member], result
    assert len(captured) == 1
    other = "festival" if member == "race" else "race"
    with pytest.raises(ValueError, match="exceeds exact special event membership"):
        replace(
            captured[0],
            case=replace(
                captured[0].case,
                special_event_id=event_id((other,)),
                event_asset_ids=(other,),
                event_admission=None,
            ),
        )
