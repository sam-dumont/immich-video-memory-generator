"""The private smart-edit matrix preserves each product's acquisition scope."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import probe_selection_final_cut as final_cut_contract
import probe_smart_edit_matrix as matrix

from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange


def test_case_manifest_preserves_an_album_reference(tmp_path: Path) -> None:
    manifest = tmp_path / "cases.json"
    manifest.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "key": "curated-trip",
                        "label": "Curated trip",
                        "product": "album",
                        "ranges": [{"start": "2022-07-21", "end": "2022-08-03"}],
                        "target_seconds": 120,
                        "brief": "Edit only the curated album membership.",
                        "album_ref": "private-album-id",
                    }
                ]
            }
        )
    )

    (case,) = matrix._load_cases(manifest)

    assert case.album_ref == "private-album-id"


def test_album_case_fetches_curated_membership_instead_of_the_date_range(monkeypatch) -> None:
    older = SimpleNamespace(id="older", file_created_at=datetime(2022, 7, 21, tzinfo=UTC))
    newer = SimpleNamespace(id="newer", file_created_at=datetime(2022, 8, 3, tzinfo=UTC))
    resolved = SimpleNamespace(id="resolved-album")
    client = SimpleNamespace(resolve_album=lambda _reference: resolved)
    observed: dict[str, object] = {}

    def fetch_album_media(
        actual_client,
        album,
        *,
        config,
        use_live_photos,
        use_photos,
    ):
        observed.update(
            {
                "client": actual_client,
                "album": album,
                "config": config,
                "use_live_photos": use_live_photos,
                "use_photos": use_photos,
            }
        )
        return SimpleNamespace(videos=[newer], photos=[older])

    monkeypatch.setattr(matrix, "fetch_album_media", fetch_album_media)
    monkeypatch.setattr(
        matrix,
        "fetch_photos",
        lambda **_kwargs: pytest.fail("an album must not fall back to date-range acquisition"),
    )
    config = Config()
    case = matrix.Case(
        key="curated-trip",
        label="Curated trip",
        product="album",
        ranges=(
            DateRange(
                start=datetime(2022, 7, 21, tzinfo=UTC),
                end=datetime(2022, 8, 3, 23, 59, tzinfo=UTC),
            ),
        ),
        target_seconds=120,
        brief="Edit only the curated album membership.",
        album_ref="private-album-id",
    )

    assets = matrix._fetch_assets(client, config, case, {})

    assert [asset.id for asset in assets] == ["older", "newer"]
    assert observed == {
        "client": client,
        "album": resolved,
        "config": config,
        "use_live_photos": True,
        "use_photos": True,
    }


def test_final_refinement_motion_prefers_the_rendition_fetcher(monkeypatch) -> None:
    """The 480p Immich rendition is tried before ever touching the full original."""
    calls: list[str] = []

    def fake_rendition(_client, _asset, *, cache_dir):
        calls.append("rendition")
        return Path("/rendition/path.mp4")

    def fake_original(_client, _candidate, _batch):
        calls.append("original")
        return Path("/original/path.mp4")

    monkeypatch.setattr(matrix, "_fetch_motion_rendition", fake_rendition)
    monkeypatch.setattr(matrix, "_fetch_motion_original", fake_original)
    candidate = SimpleNamespace(asset_id="clip-1", source=SimpleNamespace(id="clip-1"))

    path = matrix._resolve_motion_path(
        candidate,
        client=SimpleNamespace(),
        batch=SimpleNamespace(),
        cache_dir=Path("/cache"),
        existing_motion_paths={},
        warned=set(),
    )

    assert path == Path("/rendition/path.mp4")
    assert calls == ["rendition"]


def test_final_refinement_motion_falls_back_to_original_on_rendition_failure(
    monkeypatch,
) -> None:
    """A failed rendition fetch falls open onto the slower full-original path."""
    calls: list[str] = []

    def fake_rendition(_client, _asset, *, cache_dir):
        calls.append("rendition")
        return None

    def fake_original(_client, _candidate, _batch):
        calls.append("original")
        return Path("/original/path.mp4")

    monkeypatch.setattr(matrix, "_fetch_motion_rendition", fake_rendition)
    monkeypatch.setattr(matrix, "_fetch_motion_original", fake_original)
    candidate = SimpleNamespace(asset_id="clip-2", source=SimpleNamespace(id="clip-2"))

    path = matrix._resolve_motion_path(
        candidate,
        client=SimpleNamespace(),
        batch=SimpleNamespace(),
        cache_dir=Path("/cache"),
        existing_motion_paths={},
        warned=set(),
    )

    assert path == Path("/original/path.mp4")
    assert calls == ["rendition", "original"]


def test_final_refinement_motion_falls_back_to_the_known_path_when_both_fetchers_fail(
    monkeypatch,
) -> None:
    """Neither fetcher working must never drop motion evidence the run already had."""
    monkeypatch.setattr(matrix, "_fetch_motion_rendition", lambda *_a, **_k: None)
    monkeypatch.setattr(matrix, "_fetch_motion_original", lambda *_a, **_k: None)
    candidate = SimpleNamespace(asset_id="clip-3", source=SimpleNamespace(id="clip-3"))
    known = Path("/already/known.mp4")

    path = matrix._resolve_motion_path(
        candidate,
        client=SimpleNamespace(),
        batch=SimpleNamespace(),
        cache_dir=Path("/cache"),
        existing_motion_paths={"clip-3": known},
        warned=set(),
    )

    assert path == known


def test_sscd_model_flag_threads_into_a_non_none_copy_embedder(monkeypatch) -> None:
    """--sscd-model has to survive the hop from args into the duplicate-review embedder."""
    sentinel = object()

    # WHY: torch.jit.load and the checkpoint file are the external boundary;
    # this test pins the args-to-embedder hop, not the loader itself.
    monkeypatch.setattr(matrix, "_sscd_copy_embedder", lambda _model_path: sentinel)
    args = SimpleNamespace(sscd_model=Path("/fake/sscd_disc_mixup.torchscript.pt"))

    assert matrix._resolve_copy_embedder(args) is sentinel


def test_missing_sscd_model_flag_resolves_to_none_without_loading(monkeypatch) -> None:
    """No --sscd-model must mean no attempt to load a checkpoint at all."""

    def _fail(model_path: Path):
        raise AssertionError("must not build an embedder when --sscd-model is unset")

    # WHY: proves the None short-circuit never reaches the torch.jit.load boundary.
    monkeypatch.setattr(matrix, "_sscd_copy_embedder", _fail)
    args = SimpleNamespace(sscd_model=None)

    assert matrix._resolve_copy_embedder(args) is None


def _floor_wall_rows() -> tuple[object, ...]:
    return tuple(
        final_cut_contract.FineCutCandidate(
            alias=f"A{index:03d}",
            asset_id=f"private-{index}",
            moment_id=moment,
            taken_at=datetime(2007, 8, 6, 12, index, tzinfo=UTC),
            media_kind="photo",
            favourite=False,
            description=f"Visible scene {index}",
        )
        for index, moment in ((1, "M001"), (2, "M002"), (3, "M002"))
    )


def test_the_floor_waives_the_aliases_a_correctness_pass_removed() -> None:
    # Both correctness classes at once: a same-picture dedup names production asset ids,
    # the anti-resurrection guard names wall aliases.
    wall = _floor_wall_rows()
    cut = {
        "keep": [{"asset_id": "A001", "reason": "The model chose this beat."}],
        "duplicate_review": {"absorbed": [{"asset_id": "private-2", "kept_asset_id": "private-1"}]},
        "deliberation": {
            "iterations": [
                {"calls": {"visual_pool": {"skipped_review_cut_assets": ["A003"]}}},
                {"calls": {"reconsideration_review_cut": {"skipped_review_cut_assets": ["A003"]}}},
            ]
        },
    }

    assert matrix._correctness_cut_aliases(cut, wall=wall) == ("A002", "A003")


def _concert_wall() -> tuple[object, ...]:
    # Three concerts on three occasions. A004 shares A001's evening; A002 and A003 do not.
    days = ((1, 7, 12), (2, 9, 26), (3, 10, 11), (4, 7, 12))
    return tuple(
        final_cut_contract.FineCutCandidate(
            alias=f"A{index:03d}",
            asset_id=f"private-{index}",
            moment_id=f"M{index:03d}",
            taken_at=datetime(2007, month, day, 21, index, tzinfo=UTC),
            media_kind="photo",
            favourite=False,
            description="A band on a lit stage",
        )
        for index, month, day in days
    )


def test_a_redundancy_cut_against_another_occasion_is_refused() -> None:
    proposals = [
        {
            "change_id": "C001",
            "add_asset_ids": [],
            "remove_asset_ids": ["A001"],
            "reason": "A001 is redundant; A002 already shows the same band on the same stage.",
        }
    ]

    eligible, decisions, refused = matrix._filter_cross_occasion_redundancy_proposals(
        proposals,
        wall=_concert_wall(),
    )

    assert eligible == []
    assert decisions == [
        {"change_id": "C001", "verdict": "reject", "reason": "cross-occasion-similarity"}
    ]
    assert refused == ["A001"]


def test_a_redundancy_cut_inside_one_occasion_still_stands() -> None:
    proposals = [
        {
            "change_id": "C001",
            "add_asset_ids": [],
            "remove_asset_ids": ["A001"],
            "reason": "A001 is redundant; A004 already covers this evening, and A002 echoes it.",
        }
    ]

    eligible, decisions, refused = matrix._filter_cross_occasion_redundancy_proposals(
        proposals,
        wall=_concert_wall(),
    )

    assert eligible == proposals
    assert (decisions, refused) == ([], [])


def test_a_cut_on_any_ground_other_than_sameness_is_never_refused_for_its_occasion() -> None:
    proposals = [
        {
            "change_id": "C001",
            "add_asset_ids": [],
            "remove_asset_ids": ["A001"],
            "reason": "A001 is out of focus and unreadable next to A002.",
        }
    ]

    eligible, _decisions, refused = matrix._filter_cross_occasion_redundancy_proposals(
        proposals,
        wall=_concert_wall(),
    )

    assert (eligible, refused) == (proposals, [])


def test_a_redundancy_cut_that_cites_no_counterpart_is_left_for_the_next_pass() -> None:
    # Nothing is cited, so nothing proves the comparison crossed an occasion.
    proposals = [
        {
            "change_id": "C001",
            "add_asset_ids": [],
            "remove_asset_ids": ["A001"],
            "reason": "Redundant stage view.",
        }
    ]

    eligible, _decisions, refused = matrix._filter_cross_occasion_redundancy_proposals(
        proposals,
        wall=_concert_wall(),
    )

    assert (eligible, refused) == (proposals, [])


def test_a_classified_redundancy_cut_is_read_from_its_classification_not_its_prose() -> None:
    proposals = [
        {
            "change_id": "C001",
            "add_asset_ids": [],
            "remove_asset_ids": ["A001"],
            "classification": "duplicate-beat",
            "reason": "A002 carries the stage far better than this frame does.",
        }
    ]

    eligible, decisions, refused = matrix._filter_cross_occasion_redundancy_proposals(
        proposals,
        wall=_concert_wall(),
    )

    assert (eligible, refused) == ([], ["A001"])
    assert decisions[0]["reason"] == "cross-occasion-similarity"


def _reservoir(group_id: str, *, asset_id: str, taken_at: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        moment_id=group_id,
        candidates=(
            SimpleNamespace(
                asset_id=asset_id,
                taken_at=taken_at,
                media_kind="photo",
                favourite=False,
                grounded_annotations=(),
                source=SimpleNamespace(people=()),
            ),
        ),
    )


def _kept_face_row(index: int) -> object:
    return final_cut_contract.FineCutCandidate(
        alias=f"A{index:03d}",
        asset_id=f"private-{index}",
        moment_id="M001",
        taken_at=datetime(2007, 2, 20, 18, index, tzinfo=UTC),
        media_kind="photo",
        favourite=False,
        description="Two friends laughing across a table",
        people_context=("P01:tier=inner;relationship=confirmed;source=owner",),
    )


# Exactly the rows the run record holds for a moment the chapter cut dropped: the fused card
# carries a moment_id, its production group_id and one summary -- and no people field at all.
_RUN_SHAPED_REJECTED_CARDS = (
    {
        "moment_id": "M008",
        "group_id": "group-8",
        "summary": (
            "A rectangular two-layer cleaning sponge with a dark abrasive top layer, "
            "resting on the counter beside the water tap."
        ),
    },
    {
        "moment_id": "M009",
        "group_id": "group-9",
        "summary": (
            "An empty snow-covered hillside under grey cloud, and the valley below "
            "open to the horizon."
        ),
    },
)


def _run_shaped_place_offer() -> tuple[tuple[object, ...], tuple[dict[str, object], ...]]:
    """Rebuild the live offer chain: reservoirs plus record cards, into pool rows and cards."""
    reservoirs = (
        _reservoir("group-8", asset_id="private-8", taken_at=datetime(2007, 2, 20, 12, tzinfo=UTC)),
        _reservoir("group-9", asset_id="private-9", taken_at=datetime(2007, 2, 21, 12, tzinfo=UTC)),
    )
    offers = matrix._rejected_grounding_candidates(
        reservoirs,
        cards=_RUN_SHAPED_REJECTED_CARDS,
        alias_by_group={"group-8": "M008", "group-9": "M009"},
        token_by_name={},
        facts={},
    )
    summary_by_moment = {card["moment_id"]: card["summary"] for card in _RUN_SHAPED_REJECTED_CARDS}
    cards = tuple(
        {
            "moment_id": moment_id,
            "summary": summary_by_moment[moment_id],
            "reason": None,
            "asset_ids": [row.alias for row in offers if row.moment_id == moment_id],
        }
        for moment_id in dict.fromkeys(row.moment_id for row in offers)
    )
    return offers, cards


def test_the_run_record_shape_offers_its_unkept_place_rows_to_the_review() -> None:
    offers, cards = _run_shaped_place_offer()

    assert [(row.alias, row.moment_id) for row in offers] == [("X001", "M008"), ("X002", "M009")]
    assert all(row.proposed_from_rejected for row in offers)
    assert [card["asset_ids"] for card in cards] == [["X001"], ["X002"]]


def test_a_grounding_finding_offers_the_best_corroborated_moment_not_the_first_of_it() -> (
    None
):
    # The v31 year offered one tile for the whole memory: a kitchen sponge, whose card named
    # one incidental thing while a snow-covered hillside sat later in the same occasion. The
    # richer card wins; a sponge card naming nothing of the activity is never offered at all.
    offers, cards = _run_shaped_place_offer()
    pool = (_kept_face_row(1), _kept_face_row(2), *offers)

    findings = final_cut_contract.runtime_final_pool_findings(
        pool,
        current_aliases=("A001", "A002"),
        chapter_readings=(
            {"chapter_id": "C002", "label": "2007-02", "moment_ids": ["M001", "M008", "M009"]},
        ),
        rejected_moments=cards,
    )

    assert [row["focus_kind"] for row in findings] == ["occasion_without_grounding"]
    assert findings[0]["moment_ids"] == ["M009"]
    assert findings[0]["asset_ids"] == ["X002"]
    assert findings[0]["owner_evidence"]["qualifying_signals"] == [
        "people-hedged",
        "activity-context",
    ]
    assert findings[0]["owner_evidence"]["activity_context_words"] == [
        "hillside",
        "horizon",
        "snow-covered",
        "valley",
    ]
