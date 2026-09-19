"""Public production composition for the store-backed editorial planner."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from immich_memories.analysis.editorial_planner import EditorialPlan, EditorialSelection
from immich_memories.analysis.editorial_runtime import (
    EditorialRunContext,
    build_editorial_planner,
    build_smart_pipeline,
)
from immich_memories.analysis.editorial_runtime_ports import EditorialRuntimePorts
from immich_memories.analysis.editorial_structure_contract import StructurePlanningResult
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.analysis.text_episode_reader import TEXT_EPISODE_MAX_OUTPUT_TOKENS
from immich_memories.config_loader import Config
from immich_memories.memory_types.date_builders import build_birthday_windows
from immich_memories.store.episode_readings import EpisodeReadingStore
from immich_memories.timeperiod import DateRange
from tests.conftest import make_asset, make_clip


def _window(year: int, month: int, day: int) -> DateRange:
    start = datetime(year, month, day, tzinfo=UTC)
    return DateRange(start, start.replace(hour=23, minute=59, second=59))


def _create_annotation_store(path: Path, descriptions: dict[str, str]) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE asset_people (
                asset_id TEXT, person_name TEXT, person_id TEXT, birth_date TEXT
            );
            CREATE TABLE descriptions (asset_id TEXT, model TEXT, text TEXT);
            CREATE TABLE description_fields (
                asset_id TEXT, model TEXT, field TEXT, value TEXT
            );
            CREATE TABLE flags (asset_id TEXT, flag TEXT, evidence TEXT, source TEXT);
            CREATE TABLE head_facts (
                asset_id TEXT, head TEXT, version TEXT, label TEXT
            );
            CREATE TABLE pixel_facts (
                asset_id TEXT, producer_key TEXT, sharpness REAL, brightness REAL,
                contrast REAL, dark_fraction REAL, bright_fraction REAL,
                needs_rotation INTEGER
            );
            CREATE TABLE pixel_facts_thresholds (
                name TEXT, value REAL, producer_key TEXT
            );
            CREATE TABLE motion_bursts (
                asset_id TEXT, burst_id TEXT, still_ids TEXT,
                duration_seconds REAL, beats_a_still INTEGER
            );
            """
        )
        connection.executemany(
            "INSERT INTO descriptions VALUES (?, ?, ?)",
            (
                (asset_id, "student-v1", description)
                for asset_id, description in descriptions.items()
            ),
        )


class _ClosingEpisodeStore(EpisodeReadingStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        super().close()


def test_run_context_sorts_exact_windows_without_collapsing_the_gaps(
    tmp_path,
) -> None:
    later = _window(2025, 8, 20)
    earlier = _window(2015, 8, 20)

    context = EditorialRunContext(
        key="on-this-day",
        label="On this day",
        product="on_this_day",
        date_ranges=(later, earlier),
        target_seconds=90,
        artifact_dir=tmp_path,
    )

    assert context.date_ranges == (earlier, later)


def test_run_context_preserves_native_birthday_windows_including_overlap(tmp_path) -> None:
    windows = tuple(build_birthday_windows(date(1990, 4, 2), year=2026, years_back=5))

    context = EditorialRunContext(
        key="birthday",
        label="Birthday year with flashbacks",
        product="person_spotlight",
        date_ranges=windows,
        target_seconds=90,
        artifact_dir=tmp_path,
    )

    assert context.date_ranges == tuple(sorted(windows))
    assert context.case_ranges == context.date_ranges
    overlap = datetime(2025, 4, 3, 12)
    assert sum(window.contains(overlap) for window in context.date_ranges) == 2
    assert not any(window.contains(datetime(2024, 8, 1)) for window in context.date_ranges)
    shared = make_asset("birthday-overlap", file_created_at=overlap)
    gap = make_asset("outside-birthday-windows", file_created_at=datetime(2024, 8, 1))
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope(date_ranges=context.date_ranges)),
        EditorialDependencies(source_fetcher=lambda _scope: (shared, shared, gap)),
    )
    assert prepared.candidate_ids == (shared.id,)
    assert prepared.excluded_ids == (gap.id,)


def test_run_context_accepts_touching_windows_but_rejects_inverted_ranges(tmp_path) -> None:
    earlier = _window(2015, 8, 20)
    later = _window(2025, 8, 20)
    windows = (DateRange(earlier.start, later.start), later)
    context = EditorialRunContext(
        key="touching",
        label="Touching windows",
        product="person_spotlight",
        date_ranges=windows,
        target_seconds=90,
        artifact_dir=tmp_path,
    )
    assert context.date_ranges == windows

    with pytest.raises(ValueError, match="start before"):
        EditorialRunContext(
            key="inverted",
            label="Inverted window",
            product="person_spotlight",
            date_ranges=(DateRange(later.start, earlier.start),),
            target_seconds=90,
            artifact_dir=tmp_path,
        )


def test_missing_store_is_initialized_and_old_flag_cannot_bypass_preparation(tmp_path) -> None:
    context = EditorialRunContext(
        key="month",
        label="August 2026",
        product="monthly_highlights",
        date_ranges=(_window(2026, 8, 1),),
        target_seconds=60,
        artifact_dir=tmp_path / "artifacts",
    )
    store = tmp_path / "annotations.sqlite"

    disabled = Config(
        llm={"model": "text-model"}, editorial={"enabled": False, "annotation_database": str(store)}
    )
    enabled = Config(
        llm={"model": "text-model"}, editorial={"enabled": True, "annotation_database": str(store)}
    )

    planner = build_editorial_planner(
        client=object(),
        config=disabled,
        thumbnail_cache=object(),
        context=context,
        ports=EditorialRuntimePorts(load_people=lambda: {}),
    )
    assert planner._prepare_annotations is not None
    assert store.is_file()
    planner.close()
    with pytest.raises(ValueError, match="Dry-run"):
        build_editorial_planner(
            client=object(),
            config=enabled,
            thumbnail_cache=object(),
            context=context,
            dry_run=True,
        )
    assert store.is_file()


def test_smart_pipeline_factory_preserves_the_existing_constructor_seam(tmp_path) -> None:
    config = Config()
    context = EditorialRunContext(
        key="month",
        label="August 2026",
        product="monthly_highlights",
        date_ranges=(_window(2026, 8, 1),),
        target_seconds=60,
        artifact_dir=tmp_path,
    )
    expected = object()

    planner = object()
    # WHY: both constructors are stubbed so the assertion is on how the pipeline is wired up.
    with (
        # WHY: building the real planner opens the model provider and the annotation database.
        patch(
            "immich_memories.analysis.editorial_runtime.build_editorial_planner",
            return_value=planner,
        ),
        patch(
            "immich_memories.analysis.smart_pipeline.SmartPipeline",
            return_value=expected,
        ) as smart_pipeline,
    ):
        actual = build_smart_pipeline(
            client="client",
            thumbnail_cache="thumbnail-cache",
            config="pipeline-config",
            app_config=config,
            editorial_context=context,
            dry_run=False,
        )

    assert actual is expected
    smart_pipeline.assert_called_once_with(config="pipeline-config", planner=planner)


def test_default_store_is_initialized_in_the_library_cache(tmp_path):
    config = Config(llm={"model": "text-model"}, cache={"directory": str(tmp_path / "cache")})
    context = EditorialRunContext(
        "month",
        "A month",
        "monthly_highlights",
        (_window(2026, 8, 1),),
        60,
        tmp_path / "artifacts",
    )
    planner = build_editorial_planner(
        client=object(),
        config=config,
        thumbnail_cache=object(),
        context=context,
        ports=EditorialRuntimePorts(load_people=lambda: {}),
    )
    with sqlite3.connect(tmp_path / "cache" / "annotations.sqlite") as connection:
        assert connection.execute("SELECT count(*) FROM descriptions").fetchone() == (0,)
    assert planner._prepare_annotations is not None
    planner.close()


def test_explicit_model_runtime_rejects_blank_model_before_opening_the_store(
    tmp_path,
) -> None:
    store = tmp_path / "annotations.sqlite"
    config = Config(editorial={"reader": "model", "annotation_database": str(store)})
    context = EditorialRunContext(
        key="month",
        label="August 2026",
        product="monthly_highlights",
        date_ranges=(_window(2026, 8, 1),),
        target_seconds=60,
        artifact_dir=tmp_path / "artifacts",
    )

    with pytest.raises(ValueError, match="LLM model"):
        build_editorial_planner(
            client=object(),
            config=config,
            thumbnail_cache=object(),
            context=context,
        )

    assert not store.exists()


def test_album_context_keeps_acquisition_windows_empty_and_derives_only_case_span(
    tmp_path,
) -> None:
    later = make_asset("later", file_created_at=datetime(2026, 8, 20, tzinfo=UTC))
    earlier = make_asset("earlier", file_created_at=datetime(2012, 4, 3, tzinfo=UTC))

    context = EditorialRunContext(
        key="album-1",
        label="Owner album",
        product="album",
        date_ranges=(),
        target_seconds=120,
        artifact_dir=tmp_path,
        album_ref="album-1",
        album_sources=(later, earlier),
    )

    assert context.date_ranges == ()
    assert context.album_sources == (later, earlier)
    assert context.case_ranges == (DateRange(earlier.file_created_at, later.file_created_at),)


def test_runtime_acquires_each_exact_window_through_the_real_text_lane(tmp_path) -> None:
    store = tmp_path / "annotations.sqlite"
    sqlite3.connect(store).close()
    config = Config(
        llm={"model": "text-model"},
        editorial={"enabled": True, "annotation_database": str(store)},
    )
    earlier = _window(2015, 8, 20)
    later = _window(2025, 8, 20)
    context = EditorialRunContext(
        key="on-this-day",
        label="On this day",
        product="on_this_day",
        date_ranges=(later, earlier),
        target_seconds=90,
        artifact_dir=tmp_path / "artifacts",
    )
    clip = make_clip("demanded", file_created_at=earlier.start)
    acquired_scopes = []

    def acquire(_client, scope):
        acquired_scopes.append(scope)
        return (clip,)

    planner = build_editorial_planner(
        client=object(),
        config=config,
        thumbnail_cache=object(),
        context=context,
        ports=EditorialRuntimePorts(
            load_people=lambda: {},
            fetch_full_source=acquire,
        ),
    )
    assert planner is not None

    result = planner.plan(
        (ClipWithSegment(clip, 0.0, 5.0, 1.0),),
        trace=Trace(),
    )
    repeated = planner.plan(
        (ClipWithSegment(clip, 0.0, 5.0, 1.0),),
        trace=Trace(),
    )

    assert (
        result.unavailable_reason == repeated.unavailable_reason == ("no readable episode evidence")
    )
    assert tuple(scope.date_ranges for scope in acquired_scopes) == ((earlier, later),)


def test_episode_completion_ceiling_bounds_a_runaway_pack() -> None:
    assert TEXT_EPISODE_MAX_OUTPUT_TOKENS == 4_000


def test_default_people_port_reads_the_people_scan_authority_with_derived_edges() -> None:
    # WHY: the people scan reads the owner's people.yaml from the real home directory.
    with patch(
        "immich_memories.analysis.editorial_runtime_ports.load_people_prompt_context",
        return_value={},
    ) as load_people:
        context = EditorialRuntimePorts().load_people()

    assert context == {}
    load_people.assert_called_once_with(include_derived=True)


def test_album_runtime_uses_only_the_captured_album_corpus(tmp_path) -> None:
    store = tmp_path / "annotations.sqlite"
    sqlite3.connect(store).close()
    config = Config(
        llm={"model": "text-model"},
        editorial={"enabled": True, "annotation_database": str(store)},
    )
    clip = make_clip("album-demanded", file_created_at=datetime(2026, 7, 1, tzinfo=UTC))
    context = EditorialRunContext(
        key="album-1",
        label="Owner album",
        product="album",
        date_ranges=(),
        target_seconds=60,
        artifact_dir=tmp_path / "artifacts",
        album_ref="album-1",
        album_sources=(clip,),
    )

    def forbidden_fetch(_client, _scope):
        raise AssertionError("album runtime must not fetch a min/max date span")

    planner = build_editorial_planner(
        client=object(),
        config=config,
        thumbnail_cache=object(),
        context=context,
        ports=EditorialRuntimePorts(
            load_people=lambda: {},
            fetch_full_source=forbidden_fetch,
        ),
    )
    assert planner is not None

    result = planner.plan(
        (ClipWithSegment(clip, 0.0, 5.0, 1.0),),
        trace=Trace(),
    )

    assert result.unavailable_reason == "no readable episode evidence"


def test_post_card_runtime_projects_selected_wall_rows_in_chronological_order(
    tmp_path,
    monkeypatch,
) -> None:
    store = tmp_path / "annotations.sqlite"
    _create_annotation_store(
        store,
        {
            "earlier": "A family starts a race together.",
            "later": "The same family celebrates at the finish.",
        },
    )
    config = Config(
        llm={"model": "text-model"},
        editorial={
            "enabled": True,
            "annotation_database": str(store),
            "description_model": "student-v1",
        },
    )
    window = _window(2026, 8, 20)
    context = EditorialRunContext(
        key="race-day",
        label="Race day",
        product="special_day",
        date_ranges=(window,),
        target_seconds=60,
        artifact_dir=tmp_path / "artifacts",
    )
    from immich_memories.analysis.editorial_motion_outcomes import MotionOutcomeReplay

    reference = MotionOutcomeReplay(tmp_path / "explicit-prior-motion.json", "a" * 64)
    context = replace(context, motion_outcome_replay=reference)
    earlier = make_clip("earlier", file_created_at=window.start.replace(hour=9))
    later = make_clip("later", file_created_at=window.start.replace(hour=10))
    people_loads = 0
    episode_stores: list[_ClosingEpisodeStore] = []

    def episode_store(path: Path) -> _ClosingEpisodeStore:
        created = _ClosingEpisodeStore(path)
        episode_stores.append(created)
        return created

    def load_people():
        nonlocal people_loads
        people_loads += 1
        return {}

    def episode_requester(_config):
        return lambda _prompt: json.dumps(
            {
                "schema_version": "episode-reading-text-v1",
                "episodes": [
                    {
                        "episode": 1,
                        "what_happened": "A family runs and celebrates together.",
                        "representatives": [{"asset": 1, "reason": "The race begins here."}],
                        "cull": [],
                    }
                ],
            }
        )

    async def unexpected_summary(*args, **kwargs):
        pytest.fail("Selection requested a summary that does not affect the cut")

    # WHY: forbid extra network calls outside the supplied episode and editor boundaries.
    monkeypatch.setattr(
        "immich_memories.analysis.editorial_text_gateway.query_llm", unexpected_summary
    )

    captured = []

    def structure_planner(source, _effects):
        captured.append(source)
        assert source.motion_outcome_replay is reference
        assert source.annotations == {
            "earlier": "2026-08-20 09:00+00:00 | VIDEO 5s raw | "
            "A family starts a race together. | resolution:1920x1080 | duration:5.000s | motion:available",
            "later": "2026-08-20 10:00+00:00 | VIDEO 5s raw | "
            "The same family celebrates at the finish. | resolution:1920x1080 | duration:5.000s | motion:available",
        }
        assert tuple(source.assets) == ("earlier", "later")
        assert {asset for ids in source.moment_asset_ids.values() for asset in ids} == {
            "earlier",
            "later",
        }
        assert source.lineage["episode_readings"]
        assert all(row["evidence_key"] for row in source.lineage["episode_readings"])
        assert source.period_evidence == ()
        assert b"A family starts a race together." in source.wall_bytes
        assert source.case.target_seconds == 60
        assert source.intent.product == "special_day"
        return StructurePlanningResult(
            {
                "carriers": [
                    {
                        "asset_id": "earlier",
                        "taken": earlier.asset.file_created_at.isoformat(),
                        "kind": "video",
                    },
                    {
                        "asset_id": "later",
                        "taken": later.asset.file_created_at.isoformat(),
                        "kind": "video",
                    },
                ]
            },
            "exact contract",
            "private selection sheet",
            {},
        )

    planner = build_editorial_planner(
        client=object(),
        config=config,
        thumbnail_cache=object(),
        context=context,
        ports=EditorialRuntimePorts(
            load_people=load_people,
            fetch_full_source=lambda _client, _scope: (later, earlier),
            episode_requester_factory=episode_requester,
            episode_store_factory=episode_store,
            structure_planner=structure_planner,
            structure_ports_factory=lambda _source: object(),
        ),
    )
    assert planner is not None

    trace = Trace()
    result = planner.plan(
        (
            ClipWithSegment(later, 0.0, 5.0, 1.0),
            ClipWithSegment(earlier, 0.0, 5.0, 1.0),
        ),
        trace=trace,
    )

    assert result == EditorialPlan(
        selections=(
            EditorialSelection("earlier", render_mode="motion"),
            EditorialSelection("later", render_mode="motion"),
        )
    )
    assert people_loads == 1
    assert len(captured) == 1
    assert captured[0].case.brief.startswith(
        "Edit this catalogued occasion as one coherent lived event. Preserve the "
        "event's setting, actions, relationships, and progression without filling "
        "with near-identical portraits.\n\n"
    )
    with pytest.raises(ValueError, match="complete wall alias order"):
        replace(captured[0], moment_asset_ids={})
    with pytest.raises(ValueError, match="absent from its source"):
        replace(captured[0], assets={})
    with pytest.raises(ValueError, match="contract disagree"):
        replace(captured[0], case=replace(captured[0].case, product="trip"))
    assert (context.artifact_dir / "plan.private.json").is_file()
    assert episode_stores[0].close_calls == 1
    assert len(trace.requests) == 1
    assert trace.requests[0].model.startswith("text-model@text-")


@pytest.mark.parametrize(
    "carriers,reason",
    [
        (
            [{"asset_id": "foreign", "taken": "2025-01-01", "kind": "still"}],
            "outside the conserved input",
        ),
        (
            [{"asset_id": "one", "taken": "2025-01-01", "kind": "still"}] * 2,
            "same source more than once",
        ),
        (
            [
                {"asset_id": "two", "taken": "2025-01-02", "kind": "still"},
                {"asset_id": "one", "taken": "2025-01-01", "kind": "still"},
            ],
            "chronological",
        ),
    ],
)
def test_runtime_rejects_structure_carriers_that_break_source_conservation(carriers, reason):
    from immich_memories.analysis.editorial_runtime_backend import _plan_from_structure_result

    result = StructurePlanningResult({"carriers": carriers}, "contract", "sheet", {})
    with pytest.raises(ValueError, match=reason):
        _plan_from_structure_result(result, allowed_ids={"one", "two"})


def test_insufficient_structure_material_preserves_diagnostics_but_has_no_renderable_cut():
    from copy import deepcopy

    from immich_memories.analysis.editorial_runtime_backend import _plan_from_structure_result

    result = StructurePlanningResult(
        {
            "status": "insufficient_material",
            "intent_report": {"status": "insufficient_material", "reason": "Too little material"},
            "carriers": [{"asset_id": "one", "taken": "2025-01-01", "kind": "still"}],
        },
        "contract",
        "diagnostic sheet",
        {},
    )
    before = deepcopy(result.plan)
    plan = _plan_from_structure_result(result, allowed_ids={"one"})

    assert plan == EditorialPlan()
    assert plan.unavailable_reason is None
    assert result.plan == before


@pytest.mark.parametrize("status", ["mechanically_valid", "coverage_incomplete"])
def test_non_abstaining_structure_status_preserves_the_selected_cut(status):
    from immich_memories.analysis.editorial_runtime_backend import _plan_from_structure_result

    result = StructurePlanningResult(
        {
            "status": status,
            "carriers": [{"asset_id": "one", "taken": "2025-01-01", "kind": "still"}],
        },
        "contract",
        "sheet",
        {},
    )
    assert _plan_from_structure_result(result, allowed_ids={"one"}).selected_asset_ids == ("one",)
