"""Heavy scopes narrow before descriptions without pretending metadata has taste."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import probe_description_moment_cut as prototype
import probe_smart_edit_matrix as matrix
from probe_description_allocation import build_description_workprint
from probe_smart_edit_matrix import (
    _needs_optional_asset_cut,
    _required_fine_cut_ids,
    _resolved_card_mode,
    _restore_required_fine_cut_candidates,
)

from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.selection_source import EditorialGroup
from immich_memories.analysis.selection_structure import StructureMoment, StructureWorkprint
from immich_memories.api.models import Person
from tests.conftest import make_asset

START = datetime(2020, 1, 1, tzinfo=UTC)


def test_chapter_thesis_can_be_texture_without_a_sustained_thread() -> None:
    payload = {
        "schema_version": prototype.THESIS_SCHEMA,
        "thesis": "A quiet chapter of ordinary daily life.",
        "sustained_threads": [],
        "turning_points": [],
        "ordinary_texture": ["Routine scenes establish the period baseline."],
    }

    parsed = prototype._read_thesis(
        json.dumps(payload),
        frozenset({"M001"}),
        require_sustained=False,
    )

    assert parsed["sustained_threads"] == []


def test_chapter_selection_can_reject_every_moment_without_a_fake_comparison() -> None:
    payload = {
        "schema_version": prototype.SELECTION_SCHEMA,
        "keep": [],
        "audit_summary": "None of the available moments earns scarce runtime.",
        "comparisons": [],
        "overall_reason": "The chapter adds no necessary beat to the memory.",
    }

    parsed = prototype._read_selection(
        json.dumps(payload),
        frozenset({"M001", "M002"}),
        1,
    )

    assert parsed["keep"] == []


def test_chapter_allocation_scales_model_weights_to_runtime_capacity() -> None:
    def chapter(chapter_id: str, start: int, count: int) -> matrix.Chapter:
        cards = []
        for index in range(start, start + count):
            source = _moment(index)
            moment = prototype.Moment(
                alias=f"M{index:03d}",
                group=EditorialGroup(source.moment_id, source.candidates),
                descriptions=(),
            )
            cards.append(prototype.MomentCard(moment, "Literal scene.", None))
        return matrix.Chapter(chapter_id, chapter_id, tuple(cards))

    chapters = (
        chapter("C001", 1, 10),
        chapter("C002", 20, 5),
        chapter("C003", 40, 2),
    )
    payload = {
        "schema_version": matrix.ALLOCATION_SCHEMA,
        "allocations": [
            {"chapter_id": "C001", "slots": 10, "reason": "Primary thread."},
            {"chapter_id": "C002", "slots": 5, "reason": "Secondary thread."},
            {"chapter_id": "C003", "slots": 1, "reason": "Required onset."},
        ],
        "overall_reason": "Relative room before the runtime applies scarcity.",
    }

    allocation = matrix._read_allocation(
        json.dumps(payload),
        chapters,
        capacity=8,
        minimum_slots={"C003": 1},
    )

    assert [row["slots"] for row in allocation["allocations"]] == [5, 2, 1]
    assert allocation["slot_normalization"] == {
        "applied": True,
        "model_total": 16,
        "runtime_total": 8,
        "capacity": 8,
    }


def test_selection_reorders_a_grounded_keep_set_chronologically() -> None:
    payload = {
        "schema_version": prototype.SELECTION_SCHEMA,
        "keep": [
            {"moment_id": "M002", "reason": "A later necessary beat."},
            {"moment_id": "M001", "reason": "The earlier necessary beat."},
        ],
        "audit_summary": "The two retained beats displace the weaker alternative.",
        "comparisons": [
            {
                "kept_moment_id": "M001",
                "rejected_moment_id": "M003",
                "reason": "It carries more visible meaning.",
            }
        ],
        "overall_reason": "The cut keeps the coherent sequence.",
    }

    parsed = matrix._read_selection_with_comparison_repair(
        json.dumps(payload),
        frozenset({"M001", "M002", "M003"}),
        2,
    )

    assert [row["moment_id"] for row in parsed["keep"]] == ["M001", "M002"]
    assert parsed["chronological_keep_repair"] is True


def _one_fake_page(*_args, **_kwargs):
    return (object(),)


def _described_moment(count: int = 2):
    candidates = tuple(_moment(index).candidates[0] for index in range(1, count + 1))
    return matrix.prototype.Moment(
        alias="M001",
        group=EditorialGroup("group-1", candidates),
        descriptions=tuple(
            matrix.prototype.Description(candidate.asset_id, f"Literal scene {index}")
            for index, candidate in enumerate(candidates, start=1)
        ),
    )


def _moment(
    index: int,
    *,
    year: int = 2020,
    favourite: bool = False,
    people: tuple[str, ...] = (),
) -> StructureMoment:
    asset = make_asset(
        f"asset-{index}",
        file_created_at=START.replace(year=year) + timedelta(days=index),
    )
    asset.is_favorite = favourite
    asset.people = [Person(id=f"person-{name}", name=name) for name in people]
    candidate = EditorialCandidate(
        asset_id=asset.id,
        taken_at=asset.file_created_at,
        media_kind="photo",
        live_photo_stitch_member_ids=(),
        rendering_family_id=None,
        favourite=favourite,
        source=asset,
        proposed_segment=None,
        shippable_duration=0.0,
        grounded_annotations=(),
    )
    return StructureMoment(f"moment-{index}", (candidate,), candidate)


def _allocate(*moments: StructureMoment, relationships: tuple[str, ...] = ()):
    return build_description_workprint(
        StructureWorkprint(tuple(moments)),
        chapter_key=lambda moment: moment.candidates[0].taken_at.year,
        relationship_names=relationships,
        reduce_above_moments=0,
    )


def _combined_moment(*moments: StructureMoment) -> StructureMoment:
    candidates = tuple(moment.candidates[0] for moment in moments)
    return StructureMoment("combined", candidates, candidates[0])


def test_every_favourite_moment_enters_without_collapsing_same_chapter_favourites() -> None:
    first = _moment(1, favourite=True)
    second = _moment(2, favourite=True)

    allocated = _allocate(first, second)

    assert allocated.moments == (first, second)


def test_favourite_chapter_projects_exact_assets_without_reopening_full_moment() -> None:
    ordinary = _moment(1)
    favourite = _moment(2, favourite=True)
    combined = _combined_moment(ordinary, favourite)

    allocated = _allocate(combined)

    assert allocated.candidates == favourite.candidates
    assert allocated.moments[0].candidates == favourite.candidates
    assert allocated.reservoir_moments == (combined,)


def test_complete_favourite_chapter_coverage_uses_banked_descriptions() -> None:
    allocated = _allocate(_moment(1, favourite=True), _moment(2, favourite=True))

    assert _resolved_card_mode("auto", allocated) == "model"


def test_an_unstarred_chapter_switches_auto_to_complete_fused_moments() -> None:
    allocated = _allocate(_moment(1, year=2020, favourite=True), _moment(2, year=2021))

    assert _resolved_card_mode("auto", allocated) == "fused-vision"


def test_description_card_retries_one_incomplete_answer(monkeypatch, tmp_path: Path) -> None:
    attempts = 0

    async def answer(prompt, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("LLM returned incomplete content")
        return matrix.TextCall(
            prompt,
            json.dumps(
                {
                    "schema_version": matrix.prototype.CARD_SCHEMA,
                    "summary": "Two distinct literal scenes.",
                }
            ),
            0.1,
            False,
            False,
        )

    monkeypatch.setattr(matrix, "_ask_text", answer)
    cards, calls = asyncio.run(
        matrix._build_cards(
            (_described_moment(),),
            facts={},
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=1,
            timeout_seconds=30,
            card_mode="model",
        )
    )

    assert cards[0].summary == "Two distinct literal scenes."
    assert attempts == 2
    assert calls[0].warning == "initial description card failed: ValueError"


def test_description_card_falls_back_to_banked_literal_text(monkeypatch, tmp_path: Path) -> None:
    async def incomplete(*_args, **_kwargs):
        raise ValueError("LLM returned incomplete content")

    monkeypatch.setattr(matrix, "_ask_text", incomplete)
    cards, calls = asyncio.run(
        matrix._build_cards(
            (_described_moment(),),
            facts={},
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=1,
            timeout_seconds=30,
            card_mode="model",
        )
    )

    assert cards[0].summary == "Literal scene 1 ; Literal scene 2"
    assert "used deterministic description fallback" in calls[0].warning


def test_fused_card_retries_an_incomplete_visual_answer(monkeypatch, tmp_path: Path) -> None:
    group = EditorialGroup("group-1", _moment(1).candidates)

    class Requester:
        def __init__(self) -> None:
            self.requests = []

        def ask(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                raise ValueError("LLM returned incomplete content")
            return SimpleNamespace(
                raw_text=json.dumps(
                    {
                        "schema_version": matrix.prototype.CARD_SCHEMA,
                        "summary": "A compact literal scene.",
                    }
                ),
                provenance=SimpleNamespace(cache_hit=False),
            )

    requester = Requester()
    monkeypatch.setattr(matrix, "build_contact_sheets", _one_fake_page)
    cards, calls = asyncio.run(
        matrix._build_fused_cards(
            (group,),
            facts={},
            atlas=SimpleNamespace(tile_for=lambda _asset_id: object()),
            requester=requester,
            output_dir=tmp_path,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=1,
            timeout_seconds=30,
        )
    )

    assert cards[0].summary == "A compact literal scene."
    assert calls[0].warning == "initial fused card call failed: ValueError"
    assert requester.requests[1].pass_version == matrix.FUSED_CARD_RETRY_PASS_VERSION
    assert requester.requests[1].limits.max_output_tokens == 1200


def test_fused_card_uses_honest_fallback_after_two_incomplete_answers(
    monkeypatch, tmp_path: Path
) -> None:
    group = EditorialGroup("group-1", _moment(1).candidates)

    class Requester:
        def ask(self, _request):
            raise ValueError("LLM returned incomplete content")

    monkeypatch.setattr(matrix, "build_contact_sheets", _one_fake_page)
    cards, calls = asyncio.run(
        matrix._build_fused_cards(
            (group,),
            facts={},
            atlas=SimpleNamespace(tile_for=lambda _asset_id: object()),
            requester=Requester(),
            output_dir=tmp_path,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=1,
            timeout_seconds=30,
        )
    )

    assert cards[0].summary.startswith(
        "Visual contents unavailable after two incomplete card calls"
    )
    assert "used metadata-only fallback" in calls[0].warning


def test_fused_card_mechanically_repairs_schema_and_unsafe_punctuation(
    monkeypatch, tmp_path: Path
) -> None:
    group = EditorialGroup("group-1", _moment(1).candidates)

    class Requester:
        def ask(self, _request):
            return SimpleNamespace(
                raw_text=json.dumps(
                    {
                        "schema_version": "model-echoed-the-wrong-schema",
                        "moment_id": "M001",
                        "confidence": 0.99,
                        "summary": 'A sign reads "unsafe quoted text" beside the group\\path.',
                    }
                ),
                provenance=SimpleNamespace(cache_hit=False),
            )

    async def compact_repair(*_args, **_kwargs):
        raise AssertionError("mechanical envelope hygiene must not call the model")

    monkeypatch.setattr(matrix, "build_contact_sheets", _one_fake_page)
    monkeypatch.setattr(matrix, "_ask_text", compact_repair)
    cards, calls = asyncio.run(
        matrix._build_fused_cards(
            (group,),
            facts={},
            atlas=SimpleNamespace(tile_for=lambda _asset_id: object()),
            requester=Requester(),
            output_dir=tmp_path,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=1,
            timeout_seconds=30,
        )
    )

    assert cards[0].summary == "A sign reads ”unsafe quoted text” beside the group∖path."
    assert calls[0].warning == (
        "fused card content needed repair: ValueError; "
        "repaired schema/display punctuation mechanically"
    )


def test_graph_grounded_asset_is_restored_when_selects_absorbs_it() -> None:
    tagged = _moment(1, people=("Casey",))
    untagged = _moment(2)
    reservoir = _combined_moment(tagged, untagged)
    text_result = {
        "moment_alias_by_group": {reservoir.moment_id: "M001"},
        "lifecycle_requirements": [{"anchor_id": "M001", "person_name": "Casey"}],
    }
    required = _required_fine_cut_ids(
        reservoir.candidates,
        reservoirs=(reservoir,),
        text_result=text_result,
    )

    restored = _restore_required_fine_cut_candidates(
        untagged.candidates,
        reservoir_candidates=reservoir.candidates,
        required_asset_ids=required,
    )

    assert required == (tagged.candidates[0].asset_id,)
    assert {candidate.asset_id for candidate in restored} == {
        tagged.candidates[0].asset_id,
        untagged.candidates[0].asset_id,
    }


def test_abundant_duration_still_requires_the_optional_asset_quality_cut() -> None:
    candidates = tuple(
        matrix.FineCutCandidate(
            alias=f"A{index:03d}",
            asset_id=f"asset-{index}",
            moment_id="M001",
            taken_at=START + timedelta(minutes=index),
            media_kind="photo",
            favourite=False,
            description=f"Scene {index}",
        )
        for index in range(1, 4)
    )

    assert _needs_optional_asset_cut(candidates, required_aliases=(), capacity=143)
    assert not _needs_optional_asset_cut(candidates[:1], required_aliases=("A001",), capacity=143)


def test_abundant_duration_still_asks_for_moment_quality(monkeypatch, tmp_path: Path) -> None:
    group = EditorialGroup("group-1", _moment(1).candidates)
    card = matrix.prototype.MomentCard(
        matrix.prototype.Moment(alias="M001", group=group, descriptions=()),
        "One distinct lived scene.",
        None,
    )
    answer = json.dumps(
        {
            "schema_version": matrix.prototype.SELECTION_SCHEMA,
            "keep": [{"moment_id": "M001", "reason": "It earns runtime."}],
            "audit_summary": "The only moment is a lived scene.",
            "comparisons": [],
            "overall_reason": "The one-moment wall is coherent.",
        }
    )
    called = False

    async def select_call(*_args, **_kwargs):
        nonlocal called
        called = True
        return matrix.TextCall("prompt", answer, 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", select_call)
    selection, calls = asyncio.run(
        matrix._select_cards(
            (card,),
            case=matrix.Case(
                key="case",
                label="A small memory",
                product="monthly_highlights",
                ranges=(),
                target_seconds=60.0,
                brief="Make the strongest truthful memory.",
            ),
            facts={},
            thesis={"thesis": "One grounded scene."},
            capacity=13,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            timeout_seconds=30,
            thinking=False,
        )
    )

    assert called
    assert len(calls) == 1
    assert [row["moment_id"] for row in selection["keep"]] == ["M001"]


def test_selection_repairs_comparisons_that_only_compare_kept_moments(
    monkeypatch, tmp_path: Path
) -> None:
    cards = tuple(
        matrix.prototype.MomentCard(
            matrix.prototype.Moment(
                alias=f"M00{index}",
                group=EditorialGroup(f"group-{index}", _moment(index).candidates),
                descriptions=(),
            ),
            f"Distinct lived scene {index}.",
            None,
        )
        for index in (1, 2, 3)
    )
    answers = iter(
        (
            {
                "schema_version": prototype.SELECTION_SCHEMA,
                "keep": [
                    {"moment_id": "M001", "reason": "It carries the strongest beat."},
                    {"moment_id": "M002", "reason": "It adds necessary contrast."},
                ],
                "audit_summary": "Two moments earn runtime.",
                "comparisons": [
                    {
                        "kept_moment_id": "M001",
                        "rejected_moment_id": "M002",
                        "reason": "An invalid comparison between two retained moments.",
                    }
                ],
                "overall_reason": "The cut keeps the useful beats.",
            },
            {
                "keep": [
                    {"moment_id": "M001", "reason": "It carries the strongest beat."},
                    {"moment_id": "M002", "reason": "It adds necessary contrast."},
                ],
                "audit_summary": {"main_tradeoff": "Two moments beat the weaker alternative."},
                "comparisons": [
                    {
                        "kept_moment_id": "M001",
                        "rejected_moment_id": "M003",
                        "reason": "Its visible action contributes more than the alternative.",
                    }
                ],
                "overall_reason": "The cut keeps the useful beats.",
            },
        )
    )

    async def select_call(prompt, *_args, **_kwargs):
        return matrix.TextCall(prompt, json.dumps(next(answers)), 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", select_call)
    selection, calls = asyncio.run(
        matrix._select_cards(
            cards,
            case=matrix.Case(
                key="case",
                label="A small memory",
                product="monthly_highlights",
                ranges=(),
                target_seconds=60.0,
                brief="Make the strongest truthful memory.",
            ),
            facts={},
            thesis={"thesis": "Three grounded scenes."},
            capacity=2,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            timeout_seconds=30,
            thinking=False,
        )
    )

    assert len(calls) == 2
    assert selection["comparisons"][0]["rejected_moment_id"] == "M003"


def test_selection_repairs_an_overfull_keep_against_exact_capacity(
    monkeypatch, tmp_path: Path
) -> None:
    cards = tuple(
        prototype.MomentCard(
            prototype.Moment(
                alias=f"M00{index}",
                group=EditorialGroup(f"group-{index}", _moment(index).candidates),
                descriptions=(),
            ),
            f"Distinct lived scene {index}.",
            None,
        )
        for index in (1, 2, 3)
    )
    answers = iter(
        (
            {
                "schema_version": prototype.SELECTION_SCHEMA,
                "keep": [
                    {"moment_id": f"M00{index}", "reason": "It contributes."} for index in (1, 2, 3)
                ],
                "audit_summary": "The first cut exceeded its actual capacity.",
                "comparisons": [],
                "overall_reason": "All three initially looked useful.",
            },
            {
                "schema_version": prototype.SELECTION_SCHEMA,
                "keep": [
                    {"moment_id": "M001", "reason": "It carries the main beat."},
                    {"moment_id": "M003", "reason": "It adds necessary contrast."},
                ],
                "audit_summary": "Two moments beat the weakest third choice.",
                "comparisons": [
                    {
                        "kept_moment_id": "M003",
                        "rejected_moment_id": "M002",
                        "reason": "Its visible contrast adds more to the sequence.",
                    }
                ],
                "overall_reason": "The repaired cut obeys the two-slot scarcity.",
            },
        )
    )

    prompts = []

    async def select_call(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return matrix.TextCall(prompt, json.dumps(next(answers)), 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", select_call)
    selection, calls = asyncio.run(
        matrix._select_cards(
            cards,
            case=matrix.Case(
                key="case",
                label="A small memory",
                product="monthly_highlights",
                ranges=(),
                target_seconds=60.0,
                brief="Make the strongest truthful memory.",
            ),
            facts={},
            thesis={"thesis": "Three grounded scenes."},
            capacity=2,
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            timeout_seconds=30,
            thinking=False,
        )
    )

    assert len(calls) == 2
    assert [row["moment_id"] for row in selection["keep"]] == ["M001", "M003"]
    assert "previous keep has 3 rows" in prompts[1]
    assert "Return at most 2 keep rows" in prompts[1]


def test_a_chapter_with_no_favourites_stays_complete() -> None:
    old = (_moment(1, year=2010), _moment(2, year=2010))
    recent = (_moment(3, year=2020, favourite=True), _moment(4, year=2020))

    allocated = _allocate(*old, *recent)

    assert {moment.moment_id for moment in allocated.moments} == {
        old[0].moment_id,
        old[1].moment_id,
        recent[0].moment_id,
    }


def test_missing_relationship_context_gets_first_and_last_views_not_every_view() -> None:
    favourite = _moment(1, favourite=True, people=("Casey",))
    with_partner = tuple(_moment(index, people=("Morgan",)) for index in range(2, 6))

    allocated = _allocate(favourite, *with_partner, relationships=("Morgan",))

    assert allocated.moments == (favourite, with_partner[0], with_partner[-1])


def test_a_persons_first_appearance_and_return_after_two_years_enter() -> None:
    first = _moment(1, year=2010, people=("Seb",))
    ordinary = _moment(2, year=2010, people=("Seb",), favourite=True)
    returned = _moment(3, year=2013, people=("Seb",))

    allocated = _allocate(first, ordinary, returned)
    by_id = {item.moment.moment_id: item.reasons for item in allocated.admissions}

    assert "first-copresence:Seb" in by_id[first.moment_id]
    assert "resumption:Seb" in by_id[returned.moment_id]


def test_small_walls_pass_through_without_applying_the_heavy_scope_rules() -> None:
    moments = tuple(_moment(index) for index in range(3))

    allocated = build_description_workprint(
        StructureWorkprint(moments),
        chapter_key=lambda moment: moment.candidates[0].taken_at.year,
        reduce_above_moments=3,
    )

    assert allocated.moments == moments
    assert {item.reasons for item in allocated.admissions} == {("complete-wall",)}
