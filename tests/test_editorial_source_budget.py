"""Large memories spend their preparation budget before reading any pictures."""

from datetime import UTC, datetime, timedelta

import pytest

from immich_memories.analysis.editorial_planner import EditorialPlan
from immich_memories.analysis.editorial_preparation import prepare_editorial_annotations
from immich_memories.analysis.editorial_runtime import EditorialRunContext, build_editorial_planner
from immich_memories.analysis.editorial_runtime_ports import EditorialRuntimePorts
from immich_memories.analysis.editorial_source_budget import shortlist_sources
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.analysis.selection_trace import Trace
from immich_memories.config_loader import Config
from immich_memories.memory_types.registry import MemoryType
from immich_memories.timeperiod import DateRange
from tests.test_editorial_preparation import preview, successful_ports
from tests.test_editorial_source_route import photo


@pytest.mark.parametrize("memory_type", list(MemoryType))
def test_large_pool_is_bounded_before_cold_preparation(tmp_path, monkeypatch, memory_type):
    start = datetime(2030, 1, 1, tzinfo=UTC)
    sources = [photo(f"picture-{i}", at=start + timedelta(hours=6 * i)) for i in range(1200)]
    config = Config(
        cache={"directory": str(tmp_path / "cache")}, analysis={"min_source_short_side": 0}
    )
    produced, fetched, prepared_ids = [], [], []
    providers = successful_ports(produced)

    def prepare(**kwargs):
        # WHY: the external producer boundary exposes how much expensive work
        # was requested; real preparation and its fact store run below it.
        assert len(kwargs["assets"]) <= 384
        prepared_ids.extend(asset.id for asset in kwargs["assets"])
        return prepare_editorial_annotations(**kwargs, ports=providers)

    def fetch(_client, asset_id):
        # WHY: thumbnail transport is external; no Immich is needed for this
        # cold-cache preparation and source-admission contract.
        fetched.append(asset_id)
        return preview()

    def build():
        options = (
            {"album_ref": "recorded-album", "album_sources": tuple(sources)}
            if memory_type == MemoryType.ALBUM
            else {}
        )
        windows = () if options else (DateRange(start, start + timedelta(days=365)),)
        return build_editorial_planner(
            client=object(),
            config=config,
            thumbnail_cache=tmp_path / "previews",
            context=EditorialRunContext(
                "bounded-memory",
                "A large memory",
                memory_type,
                windows,
                60,
                tmp_path / "runs",
                **options,
            ),
            ports=EditorialRuntimePorts(
                load_people=lambda: {},
                fetch_full_source=lambda *_: sources,
                fetch_preview=fetch,
                prepare_annotations=prepare,
            ),
        )

    planner = build()
    observed = []

    def editor(candidates, *, prepared, **_):
        # WHY: stop at the model-editor boundary; this test owns the real
        # acquisition, budget, preparation and metadata-demand path before it.
        observed.extend(row.clip.asset.id for row in candidates)
        assert set(observed) == set(prepared.candidate_ids)
        return EditorialPlan()

    monkeypatch.setattr(planner._planner, "plan_prepared", editor)
    trace = Trace()
    planner.plan_source(sources, trace=trace)

    assert 60 <= len(prepared_ids) <= 384
    assert set(prepared_ids) == set(fetched) == set(observed)
    selected = [asset for asset in sources if asset.id in observed]
    assert len({asset.file_created_at.month for asset in selected}) == 10
    assert all(trace.story_of(asset.id).reason for asset in sources if asset.id not in observed)
    produced.clear()
    fetched.clear()
    planner.plan_source(sources, trace=Trace())
    assert not produced and not fetched

    previous = set(observed)
    observed.clear()
    added = photo("new-favourite", at=start + timedelta(days=7, hours=1))
    added.is_favorite = True
    sources.append(added)
    planner = build()
    monkeypatch.setattr(planner._planner, "plan_prepared", editor)
    planner.plan_source(sources, trace=Trace())

    newly_selected = set(observed) - previous
    assert added.id in newly_selected
    assert 1 <= len(newly_selected) <= 12
    assert set(fetched) == newly_selected
    captioned = {key for stage, ids in produced if stage == "captions" for key in ids}
    assert captioned == newly_selected


def test_a_dense_burst_cannot_spend_the_budget_before_other_days_are_represented():
    start = datetime(2030, 1, 1, tzinfo=UTC)
    sources = [photo(f"burst-{i}", at=start + timedelta(seconds=i / 2)) for i in range(1000)]
    sources[1].is_favorite = True
    sources += [
        photo(f"outing-{day}-{i}", at=start + timedelta(days=day, seconds=i))
        for day in range(1, 21)
        for i in range(10)
    ]
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(SourceScope(min_source_short_side=0)),
        EditorialDependencies(source_fetcher=lambda _: sources),
    )

    selected = shortlist_sources(
        prepared,
        requested_ids=prepared.candidate_ids,
        limit=96,
    )

    assert len(selected) == 96
    assert "burst-1" in selected
    assert {asset.file_created_at.date() for asset in sources if asset.id in selected} == {
        start.date() + timedelta(days=day) for day in range(21)
    }
    assert sum(key.startswith("burst-") for key in selected) <= 5


def test_sampling_preserves_short_live_sequences_instead_of_isolating_every_shutter():
    from immich_memories.analysis.motion_rendering import motion_renderings

    start = datetime(2030, 1, 1, tzinfo=UTC)
    sources = [
        photo(
            f"live-{hour}-{i}",
            at=start + timedelta(hours=hour, seconds=i),
            live=f"companion-{hour}-{i}",
        )
        for hour in range(120)
        for i in range(3)
    ]
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(SourceScope(min_source_short_side=0)),
        EditorialDependencies(source_fetcher=lambda _: sources),
    )
    selected = shortlist_sources(
        prepared,
        requested_ids=prepared.candidate_ids,
        limit=96,
    )
    offered = motion_renderings([asset for asset in sources if asset.id in selected], Config())

    assert len(selected) == 96
    assert offered and all(rendering.beats_a_still for rendering in offered.values())


def test_many_capture_groups_on_one_day_do_not_erase_other_days():
    start = datetime(2030, 1, 1, tzinfo=UTC)
    sources = [
        photo(f"busy-{group}-{i}", at=start + timedelta(minutes=15 * group, seconds=i))
        for group in range(80)
        for i in range(10)
    ]
    sources += [
        photo(f"quiet-{day}-{i}", at=start + timedelta(days=day, seconds=i))
        for day in range(1, 21)
        for i in range(10)
    ]
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(SourceScope(min_source_short_side=0)),
        EditorialDependencies(source_fetcher=lambda _: sources),
    )

    selected = set(shortlist_sources(prepared, requested_ids=prepared.candidate_ids, limit=96))

    assert {asset.file_created_at.date() for asset in sources if asset.id in selected} == {
        start.date() + timedelta(days=day) for day in range(21)
    }
    assert sum(key.startswith("busy-") for key in selected) <= 5


def test_favourite_can_nominate_a_capture_group_before_its_frames_are_sampled():
    start = datetime(2030, 1, 1, tzinfo=UTC)
    sources = [
        photo(f"scene-{group}-{i}", at=start + timedelta(minutes=20 * group, seconds=i))
        for group in range(30)
        for i in range(3)
    ]
    sources[46].is_favorite = True
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(SourceScope(min_source_short_side=0)),
        EditorialDependencies(source_fetcher=lambda _: sources),
    )

    selected = shortlist_sources(prepared, requested_ids=prepared.candidate_ids, limit=6)

    assert len(selected) == 6
    assert sources[46].id in selected
    assert len({key.split("-")[1] for key in selected}) >= 2


def test_manual_choices_survive_the_automatic_budget_but_exclusions_still_win():
    sources = [photo(f"manual-{i}") for i in range(120)]
    required = tuple(asset.id for asset in sources[:100])
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(
            SourceScope(min_source_short_side=0),
            owner_required_asset_ids=required,
            owner_excluded_asset_ids=(sources[0].id,),
        ),
        EditorialDependencies(source_fetcher=lambda _: sources),
    )

    selected = shortlist_sources(prepared, requested_ids=prepared.candidate_ids, limit=96)

    assert set(selected) == set(required) - {sources[0].id}


def test_changing_one_day_does_not_resample_unrelated_days():
    start = datetime(2030, 1, 1, tzinfo=UTC)
    sources = [
        photo(f"day-{day}-{group}-{i}", at=start + timedelta(days=day, hours=group, seconds=i))
        for day in range(25)
        for group in range(4)
        for i in range(10)
    ]

    def sample():
        prepared = prepare_editorial_source(
            EditorialSelectionRequest(SourceScope(min_source_short_side=0)),
            EditorialDependencies(source_fetcher=lambda _: sources),
        )
        return set(shortlist_sources(prepared, requested_ids=prepared.candidate_ids, limit=96))

    original = sample()
    sources.append(photo("new-on-day-7", at=start + timedelta(days=7, hours=8)))
    changed = sample()

    unrelated = {
        asset.id
        for asset in sources
        if asset.file_created_at.date() != (start + timedelta(days=7)).date()
    }
    assert original & unrelated == changed & unrelated


def test_large_context_cannot_spend_analysis_outside_requested_people_or_membership():
    start = datetime(2030, 1, 1, tzinfo=UTC)
    sources = [photo(f"picture-{i}", at=start + timedelta(hours=i)) for i in range(1000)]
    requested = {asset.id for asset in sources if int(asset.id.split("-")[1]) % 7 == 0}
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(SourceScope(min_source_short_side=0)),
        EditorialDependencies(source_fetcher=lambda _: sources),
    )

    selected = shortlist_sources(prepared, requested_ids=requested, limit=96)

    assert len(selected) == 96
    assert set(selected) <= requested


def test_sampling_keeps_surrounding_episode_context_without_preparing_omitted_photos(
    tmp_path, monkeypatch
):
    from immich_memories.api.models import Person

    start = datetime(2030, 1, 1, tzinfo=UTC)
    sources = [photo(f"visit-{i}", at=start + timedelta(seconds=i)) for i in range(500)]
    full = prepare_editorial_source(
        EditorialSelectionRequest(SourceScope(min_source_short_side=0)),
        EditorialDependencies(source_fetcher=lambda _: sources),
    )
    retained = set(shortlist_sources(full, requested_ids=full.candidate_ids, limit=384))
    omitted = next(asset for asset in sources if asset.id not in retained)
    omitted.people = [Person(id="visitor-id", name="Taylor Example")]
    produced = []
    providers = successful_ports(produced)

    def prepare(**kwargs):
        # WHY: substitute only the external fact producers; acquisition, budget,
        # metadata context, preparation and the persistent fact store are real.
        return prepare_editorial_annotations(**kwargs, ports=providers)

    planner = build_editorial_planner(
        client=object(),
        config=Config(
            cache={"directory": str(tmp_path / "cache")}, analysis={"min_source_short_side": 0}
        ),
        thumbnail_cache=tmp_path / "previews",
        context=EditorialRunContext(
            "visit",
            "A visit",
            "monthly",
            (DateRange(start, start + timedelta(days=1)),),
            60,
            tmp_path / "runs",
        ),
        ports=EditorialRuntimePorts(
            load_people=lambda: {},
            fetch_full_source=lambda *_: sources,
            fetch_preview=lambda *_: preview(),
            prepare_annotations=prepare,
        ),
    )

    def editor(candidates, *, prepared, **_):
        # WHY: stop at the model editor to inspect its real contextual input.
        assert set(prepared.candidate_ids) == retained
        assert set(prepared.episode_context) == retained
        context = prepared.episode_context[candidates[0].clip.asset.id]
        assert "Taylor Example" in context and "500 captures" in context
        assert "08:19" in context and "episode participants" in context
        assert omitted.id not in prepared.candidate_ids
        return EditorialPlan()

    monkeypatch.setattr(planner._planner, "plan_prepared", editor)
    planner.plan_source(sources, trace=Trace())
    captioned = {key for stage, ids in produced if stage == "captions" for key in ids}
    assert captioned == retained
