"""Final duplicate review catches re-imported pictures across moment boundaries."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
from immich_memories.analysis.llm_query import LLMTransportAttempt
from immich_memories.analysis.selection_final_duplicates import review_final_duplicates
from immich_memories.analysis.selection_selects import (
    SELECTS_MAX_CORROBORATION,
    SamePicturePairDecision,
    confirm_same_picture_pairs,
)
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.analysis.visual_atlas import AtlasTile, VisualAtlas, build_visual_atlas
from immich_memories.api.models import AssetType
from immich_memories.config_models_llm import LLMConfig
from tests.conftest import make_asset

WHEN = datetime(2024, 1, 2, 12, tzinfo=UTC)


def _jpeg(colour: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (24, 18), colour).save(output, "JPEG")
    return output.getvalue()


def _prepared(*assets, pixels: dict[str, bytes] | None = None):
    frames = pixels or {}
    return prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda asset: frames.get(asset.id) or _jpeg("navy"),
        ),
    )


def _photo_asset(
    asset_id: str,
    *,
    when: datetime = WHEN,
    favourite: bool = False,
    checksum: str | None = None,
):
    return make_asset(
        asset_id,
        file_created_at=when,
        is_favorite=favourite,
        original_file_name=f"{asset_id}.jpg",
        duration=None,
    ).model_copy(
        update={
            "type": AssetType.IMAGE,
            "duration_seconds": None,
            "checksum": checksum,
        }
    )


def _gateway(tmp_path: Path, trace):
    return VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=trace,
    )


def _pair_answer(same: bool) -> str:
    return json.dumps(
        {
            "schema_version": "pair-v3",
            "same": same,
            "reason": "the same subject and framing",
        }
    )


def test_public_pair_confirmation_crosses_moments_only_after_two_orders_agree(
    tmp_path: Path,
) -> None:
    prepared = _prepared(
        _photo_asset("old-copy"),
        _photo_asset("later-copy", when=WHEN + timedelta(days=400)),
        pixels={"old-copy": _jpeg("navy"), "later-copy": _jpeg("blue")},
    )
    atlas = build_visual_atlas(prepared.visual_sources, frame_cache_dir=tmp_path / "frames")
    asks = 0

    async def _answer(_prompt, _config, **kwargs):
        nonlocal asks
        asks += 1
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return _pair_answer(True)

    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        decisions = confirm_same_picture_pairs(
            ((prepared.candidates[0], prepared.candidates[1]),),
            atlas=atlas,
            requester=_gateway(tmp_path, prepared.trace),
            sheet_output_dir=tmp_path / "sheets",
        )

    assert asks == 2
    assert decisions == (
        SamePicturePairDecision(
            earlier_asset_id="old-copy",
            later_asset_id="later-copy",
            same=True,
        ),
    )


def test_public_pair_confirmation_fails_open_when_the_orders_disagree(tmp_path: Path) -> None:
    prepared = _prepared(
        _photo_asset("first"),
        _photo_asset("second", when=WHEN + timedelta(days=400)),
        pixels={"first": _jpeg("navy"), "second": _jpeg("blue")},
    )
    atlas = build_visual_atlas(prepared.visual_sources, frame_cache_dir=tmp_path / "frames")
    answers = iter((_pair_answer(True), _pair_answer(False)))

    async def _answer(_prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return next(answers)

    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        decisions = confirm_same_picture_pairs(
            ((prepared.candidates[0], prepared.candidates[1]),),
            atlas=atlas,
            requester=_gateway(tmp_path, prepared.trace),
            sheet_output_dir=tmp_path / "sheets",
        )

    assert decisions[0].same is False
    assert decisions[0].warning is None


def test_close_pixels_without_description_overlap_do_not_reach_the_model(tmp_path: Path) -> None:
    prepared = _prepared(
        _photo_asset("cat"),
        _photo_asset("birthday", when=WHEN + timedelta(days=400)),
    )
    atlas = VisualAtlas(
        (
            AtlasTile("cat", "photo", b"cat-pixels", "cat-sha", 1),
            AtlasTile("birthday", "photo", b"birthday-pixels", "birthday-sha", 1),
        )
    )

    with (
        patch(
            "immich_memories.analysis.selection_final_duplicates.compute_thumbnail_hash",
            return_value="0" * 16,
        ),
        patch(
            "immich_memories.analysis.selection_final_duplicates.confirm_same_picture_pairs",
            side_effect=AssertionError("dissimilar descriptions must not buy a model call"),
        ),
    ):
        result = review_final_duplicates(
            prepared.candidates,
            descriptions={
                "cat": "A tabby cat sleeping alone on a sofa.",
                "birthday": "Friends celebrating together around a birthday cake.",
            },
            atlas=atlas,
            requester=object(),
            sheet_output_dir=tmp_path / "sheets",
        )

    assert result.nominations == ()
    assert result.survivors == prepared.candidates


def test_close_pixels_and_matching_descriptions_route_one_pair_without_cutting(
    tmp_path: Path,
) -> None:
    prepared = _prepared(
        _photo_asset("first-copy"),
        _photo_asset("second-copy", when=WHEN + timedelta(days=400)),
    )
    atlas = VisualAtlas(
        (
            AtlasTile("first-copy", "photo", b"first-pixels", "first-sha", 1),
            AtlasTile("second-copy", "photo", b"second-pixels", "second-sha", 1),
        )
    )
    compared: list[tuple[str, str]] = []

    def _different(pairs, **_kwargs):
        compared.extend((left.asset_id, right.asset_id) for left, right in pairs)
        return (
            SamePicturePairDecision(
                earlier_asset_id="first-copy",
                later_asset_id="second-copy",
                same=False,
            ),
        )

    with (
        patch(
            "immich_memories.analysis.selection_final_duplicates.compute_thumbnail_hash",
            return_value="0" * 16,
        ),
        patch(
            "immich_memories.analysis.selection_final_duplicates.confirm_same_picture_pairs",
            new=_different,
        ),
    ):
        result = review_final_duplicates(
            prepared.candidates,
            descriptions={
                "first-copy": "A child standing in the same hallway wearing a red shirt.",
                "second-copy": "A child standing in the same hallway wearing a red shirt.",
            },
            atlas=atlas,
            requester=object(),
            sheet_output_dir=tmp_path / "sheets",
        )

    assert compared == [("first-copy", "second-copy")]
    assert result.nominations[0].signals == ("perceptual-description",)
    assert result.survivors == prepared.candidates


def test_close_final_duplicate_uses_pixels_as_the_second_vote(tmp_path: Path) -> None:
    prepared = _prepared(
        _photo_asset("first-copy"),
        _photo_asset("second-copy", when=WHEN + timedelta(days=400)),
    )
    atlas = build_visual_atlas(prepared.visual_sources, frame_cache_dir=tmp_path / "frames")
    asks = 0

    async def _answer(_prompt, _config, **kwargs):
        nonlocal asks
        asks += 1
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return _pair_answer(True)

    with (
        patch(
            "immich_memories.analysis.selection_final_duplicates.compute_thumbnail_hash",
            return_value="0" * 16,
        ),
        patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer),
    ):
        result = review_final_duplicates(
            prepared.candidates,
            descriptions={
                "first-copy": "A child standing in a hallway wearing a red shirt.",
                "second-copy": "A child standing in a hallway wearing a red shirt.",
            },
            atlas=atlas,
            requester=_gateway(tmp_path, prepared.trace),
            sheet_output_dir=tmp_path / "sheets",
        )

    assert asks == 1
    assert result.nominations[0].perceptual_distance == 0
    assert result.decisions == (SamePicturePairDecision("first-copy", "second-copy", True),)
    assert tuple(candidate.asset_id for candidate in result.survivors) == ("first-copy",)


def test_missing_duplicate_tile_keeps_both_without_a_model_call(tmp_path: Path) -> None:
    prepared = _prepared(
        _photo_asset("visible"),
        _photo_asset("missing", when=WHEN + timedelta(days=400)),
    )
    atlas = VisualAtlas((AtlasTile("visible", "photo", b"visible", "visible-sha", 1),))

    with patch(
        "immich_memories.analysis.selection_final_duplicates.confirm_same_picture_pairs",
        side_effect=AssertionError("missing evidence must not buy a model call"),
    ):
        result = review_final_duplicates(
            prepared.candidates,
            descriptions={"visible": "A room.", "missing": "A room."},
            atlas=atlas,
            requester=object(),
            sheet_output_dir=tmp_path / "sheets",
        )

    assert result.nominations == ()
    assert result.survivors == prepared.candidates


def test_exact_checksum_collapses_even_when_a_duplicate_tile_is_missing(tmp_path: Path) -> None:
    prepared = _prepared(
        _photo_asset("visible", checksum="same-source"),
        _photo_asset("missing", when=WHEN + timedelta(days=400), checksum="same-source"),
    )
    atlas = VisualAtlas((AtlasTile("visible", "photo", b"visible", "visible-sha", 1),))

    with patch(
        "immich_memories.analysis.selection_final_duplicates.confirm_same_picture_pairs",
        side_effect=AssertionError("an exact checksum needs no model call"),
    ):
        result = review_final_duplicates(
            prepared.candidates,
            descriptions={},
            atlas=atlas,
            requester=object(),
            sheet_output_dir=tmp_path / "sheets",
        )

    assert result.decisions == (SamePicturePairDecision("visible", "missing", True),)
    assert tuple(candidate.asset_id for candidate in result.survivors) == ("visible",)


def test_public_pair_confirmation_fails_open_when_a_tile_is_missing(tmp_path: Path) -> None:
    prepared = _prepared(
        _photo_asset("visible"),
        _photo_asset("missing", when=WHEN + timedelta(days=400)),
    )
    atlas = VisualAtlas((AtlasTile("visible", "photo", b"visible", "visible-sha", 1),))

    decisions = confirm_same_picture_pairs(
        ((prepared.candidates[0], prepared.candidates[1]),),
        atlas=atlas,
        requester=object(),
        sheet_output_dir=tmp_path / "sheets",
    )

    assert decisions[0].same is False
    assert decisions[0].warning is not None
    assert "both kept" in decisions[0].warning


def test_confirmed_duplicate_keeps_required_file_and_propagates_the_favourite(
    tmp_path: Path,
) -> None:
    required = _photo_asset("required-copy").model_copy(update={"width": 1200, "height": 800})
    starred = _photo_asset(
        "starred-original",
        when=WHEN + timedelta(days=400),
        favourite=True,
    ).model_copy(update={"width": 4000, "height": 3000})
    prepared = _prepared(required, starred)
    atlas = VisualAtlas(
        (
            AtlasTile("required-copy", "photo", b"required", "same-tile", 1),
            AtlasTile("starred-original", "photo", b"starred", "same-tile", 1),
        )
    )

    def _same(_pairs, **_kwargs):
        return (
            SamePicturePairDecision(
                earlier_asset_id="required-copy",
                later_asset_id="starred-original",
                same=True,
            ),
        )

    with patch(
        "immich_memories.analysis.selection_final_duplicates.confirm_same_picture_pairs",
        new=_same,
    ):
        result = review_final_duplicates(
            prepared.candidates,
            descriptions={},
            atlas=atlas,
            requester=object(),
            sheet_output_dir=tmp_path / "sheets",
            required_asset_ids=("required-copy",),
        )

    assert tuple(candidate.asset_id for candidate in result.survivors) == ("required-copy",)
    assert result.survivors[0].favourite is True
    assert result.absorbed[0].asset_id == "starred-original"
    assert result.absorbed[0].kept_asset_id == "required-copy"


def test_confirmed_optional_duplicate_keeps_the_earliest_editorial_occurrence(
    tmp_path: Path,
) -> None:
    smaller = _photo_asset("small-copy").model_copy(update={"width": 1200, "height": 800})
    larger = _photo_asset(
        "large-original",
        when=WHEN + timedelta(days=400),
        favourite=True,
    ).model_copy(update={"width": 4000, "height": 3000})
    prepared = _prepared(smaller, larger)
    atlas = VisualAtlas(
        (
            AtlasTile("small-copy", "photo", b"small", "same-tile", 1),
            AtlasTile("large-original", "photo", b"large", "same-tile", 1),
        )
    )

    with patch(
        "immich_memories.analysis.selection_final_duplicates.confirm_same_picture_pairs",
        return_value=(
            SamePicturePairDecision(
                earlier_asset_id="small-copy",
                later_asset_id="large-original",
                same=True,
            ),
        ),
    ):
        result = review_final_duplicates(
            prepared.candidates,
            descriptions={},
            atlas=atlas,
            requester=object(),
            sheet_output_dir=tmp_path / "sheets",
        )

    assert tuple(candidate.asset_id for candidate in result.survivors) == ("small-copy",)
    assert result.survivors[0].favourite is True


def test_confirmed_optional_duplicate_prefers_the_higher_media_priority(
    tmp_path: Path,
) -> None:
    static_favourite = _photo_asset("static-copy", favourite=True)
    meaningful_motion = _photo_asset(
        "motion-copy",
        when=WHEN + timedelta(seconds=1),
    )
    prepared = _prepared(static_favourite, meaningful_motion)
    atlas = VisualAtlas(
        (
            AtlasTile("static-copy", "photo", b"static", "same-tile", 1),
            AtlasTile("motion-copy", "photo", b"motion", "same-tile", 1),
        )
    )

    with patch(
        "immich_memories.analysis.selection_final_duplicates.confirm_same_picture_pairs",
        return_value=(SamePicturePairDecision("static-copy", "motion-copy", True),),
    ):
        result = review_final_duplicates(
            prepared.candidates,
            descriptions={},
            atlas=atlas,
            requester=object(),
            sheet_output_dir=tmp_path / "sheets",
            media_priorities={"static-copy": 0, "motion-copy": 1},
        )

    assert tuple(candidate.asset_id for candidate in result.survivors) == ("motion-copy",)
    assert result.survivors[0].favourite is True
    assert result.absorbed[0].asset_id == "static-copy"
    assert result.absorbed[0].kept_asset_id == "motion-copy"


def test_duplicate_groups_require_direct_positive_agreement_between_every_member(
    tmp_path: Path,
) -> None:
    prepared = _prepared(
        _photo_asset("a"),
        _photo_asset("b", when=WHEN + timedelta(days=100)),
        _photo_asset("c", when=WHEN + timedelta(days=200)),
    )
    atlas = VisualAtlas(
        (
            AtlasTile("a", "photo", b"a", "same-tile", 1),
            AtlasTile("b", "photo", b"b", "same-tile", 1),
            AtlasTile("c", "photo", b"c", "same-tile", 1),
        )
    )

    with patch(
        "immich_memories.analysis.selection_final_duplicates.confirm_same_picture_pairs",
        return_value=(
            SamePicturePairDecision("a", "b", True),
            SamePicturePairDecision("a", "c", False),
            SamePicturePairDecision("b", "c", True),
        ),
    ):
        result = review_final_duplicates(
            prepared.candidates,
            descriptions={},
            atlas=atlas,
            requester=object(),
            sheet_output_dir=tmp_path / "sheets",
        )

    assert tuple(candidate.asset_id for candidate in result.survivors) == ("a", "c")
    assert tuple((row.asset_id, row.kept_asset_id) for row in result.absorbed) == (("b", "a"),)


def test_two_required_duplicates_stay_visible_and_raise_a_warning(tmp_path: Path) -> None:
    prepared = _prepared(
        _photo_asset("required-a"),
        _photo_asset("required-b", when=WHEN + timedelta(days=400)),
    )
    atlas = VisualAtlas(
        (
            AtlasTile("required-a", "photo", b"a", "same-tile", 1),
            AtlasTile("required-b", "photo", b"b", "same-tile", 1),
        )
    )

    with patch(
        "immich_memories.analysis.selection_final_duplicates.confirm_same_picture_pairs",
        return_value=(
            SamePicturePairDecision(
                earlier_asset_id="required-a",
                later_asset_id="required-b",
                same=True,
            ),
        ),
    ):
        result = review_final_duplicates(
            prepared.candidates,
            descriptions={},
            atlas=atlas,
            requester=object(),
            sheet_output_dir=tmp_path / "sheets",
            required_asset_ids=("required-a", "required-b"),
        )

    assert result.survivors == prepared.candidates
    assert result.absorbed == ()
    assert any("required duplicates" in warning for warning in result.warnings)


def test_mixed_media_with_the_same_preview_is_not_a_perceptual_duplicate(
    tmp_path: Path,
) -> None:
    photo = _photo_asset("photo")
    video = make_asset("video", file_created_at=WHEN + timedelta(days=400))
    prepared = _prepared(photo, video)
    atlas = VisualAtlas(
        (
            AtlasTile("photo", "photo", b"same-preview", "same-tile", 1),
            AtlasTile("video", "filmstrip", b"same-preview", "same-tile", 3),
        )
    )

    with patch(
        "immich_memories.analysis.selection_final_duplicates.confirm_same_picture_pairs",
        side_effect=AssertionError("mixed media must not buy a same-picture call"),
    ):
        result = review_final_duplicates(
            prepared.candidates,
            descriptions={"photo": "The scene.", "video": "The scene."},
            atlas=atlas,
            requester=object(),
            sheet_output_dir=tmp_path / "sheets",
        )

    assert result.nominations == ()
    assert result.survivors == prepared.candidates


def test_identical_source_checksum_collapses_without_a_model_call(tmp_path: Path) -> None:
    prepared = _prepared(
        _photo_asset("first", checksum="same-source"),
        _photo_asset(
            "second",
            when=WHEN + timedelta(days=400),
            checksum="same-source",
        ),
    )
    atlas = VisualAtlas(
        (
            AtlasTile("first", "photo", b"one", "one-tile", 1),
            AtlasTile("second", "photo", b"two", "two-tile", 1),
        )
    )

    with patch(
        "immich_memories.analysis.selection_final_duplicates.confirm_same_picture_pairs",
        side_effect=AssertionError("a byte-identical source needs no model opinion"),
    ):
        result = review_final_duplicates(
            prepared.candidates,
            descriptions={},
            atlas=atlas,
            requester=object(),
            sheet_output_dir=tmp_path / "sheets",
        )

    assert result.nominations[0].signals == ("exact-checksum",)
    assert result.decisions == (SamePicturePairDecision("first", "second", True),)
    assert tuple(candidate.asset_id for candidate in result.survivors) == ("first",)


def _split_jpeg(*, vertical: bool) -> bytes:
    """A half-black/half-white frame; the two orientations are far apart in aHash."""
    image = Image.new("RGB", (24, 24), "white")
    for x in range(24):
        for y in range(24):
            dark = x < 12 if vertical else y < 12
            if dark:
                image.putpixel((x, y), (0, 0, 0))
    output = BytesIO()
    image.save(output, "JPEG")
    return output.getvalue()


def _stub_copy_embedder(vectors: dict[bytes, tuple[float, ...]]):
    # WHY: stands in for the SSCD torchscript model, the one external boundary
    # in this path; every other input here is real pixels and real hashes.
    def _embed(jpeg_bytes: bytes) -> tuple[float, ...]:
        return vectors[jpeg_bytes]

    return _embed


def test_hash_distant_copy_is_nominated_by_the_copy_embedding(tmp_path: Path) -> None:
    prepared = _prepared(
        _photo_asset("newspaper-page"),
        _photo_asset("printed-photo", when=WHEN + timedelta(days=400)),
        pixels={
            "newspaper-page": _split_jpeg(vertical=True),
            "printed-photo": _split_jpeg(vertical=False),
        },
    )
    atlas = build_visual_atlas(prepared.visual_sources, frame_cache_dir=tmp_path / "frames")
    embedder = _stub_copy_embedder(
        {
            atlas.tile_for("newspaper-page").jpeg_bytes: (1.0, 0.0),
            atlas.tile_for("printed-photo").jpeg_bytes: (0.654, 0.7565),
        }
    )

    with patch(
        "immich_memories.analysis.selection_final_duplicates.confirm_same_picture_pairs",
        return_value=(SamePicturePairDecision("newspaper-page", "printed-photo", False),),
    ):
        result = review_final_duplicates(
            prepared.candidates,
            descriptions={
                "newspaper-page": "A newspaper front page held up to the camera.",
                "printed-photo": "Two runners crossing a finish line under a banner.",
            },
            atlas=atlas,
            requester=object(),
            sheet_output_dir=tmp_path / "sheets",
            copy_embedder=embedder,
        )

    assert len(result.nominations) == 1
    nomination = result.nominations[0]
    assert nomination.nomination_source == "sscd"
    assert nomination.signals == ("sscd-cosine",)
    assert nomination.copy_similarity is not None
    assert round(nomination.copy_similarity, 3) == 0.654
    assert nomination.perceptual_distance is not None
    assert nomination.perceptual_distance > SELECTS_MAX_CORROBORATION


def test_copy_embedding_below_the_floor_buys_no_model_call(tmp_path: Path) -> None:
    prepared = _prepared(
        _photo_asset("street"),
        _photo_asset("kitchen", when=WHEN + timedelta(days=400)),
        pixels={
            "street": _split_jpeg(vertical=True),
            "kitchen": _split_jpeg(vertical=False),
        },
    )
    atlas = build_visual_atlas(prepared.visual_sources, frame_cache_dir=tmp_path / "frames")
    embedder = _stub_copy_embedder(
        {
            atlas.tile_for("street").jpeg_bytes: (1.0, 0.0),
            atlas.tile_for("kitchen").jpeg_bytes: (0.59, 0.8075),
        }
    )

    with patch(
        "immich_memories.analysis.selection_final_duplicates.confirm_same_picture_pairs",
        side_effect=AssertionError("a copy score under the floor must not buy a model call"),
    ):
        result = review_final_duplicates(
            prepared.candidates,
            descriptions={"street": "A busy street.", "kitchen": "A kitchen counter."},
            atlas=atlas,
            requester=object(),
            sheet_output_dir=tmp_path / "sheets",
            copy_embedder=embedder,
        )

    assert result.nominations == ()
    assert result.survivors == prepared.candidates


def test_copy_embedder_failure_disables_nomination_and_records_a_warning(tmp_path: Path) -> None:
    prepared = _prepared(
        _photo_asset("left"),
        _photo_asset("right", when=WHEN + timedelta(days=400)),
        pixels={"left": _split_jpeg(vertical=True), "right": _split_jpeg(vertical=False)},
    )
    atlas = build_visual_atlas(prepared.visual_sources, frame_cache_dir=tmp_path / "frames")
    calls = 0

    # WHY: stands in for a torchscript checkpoint that will not load.
    def _broken(_jpeg_bytes: bytes) -> tuple[float, ...]:
        nonlocal calls
        calls += 1
        raise RuntimeError("no such file: sscd_disc_mixup.torchscript.pt")

    result = review_final_duplicates(
        prepared.candidates,
        descriptions={"left": "A street.", "right": "A kitchen."},
        atlas=atlas,
        requester=object(),
        sheet_output_dir=tmp_path / "sheets",
        copy_embedder=_broken,
    )

    assert calls == 1
    assert result.nominations == ()
    assert result.survivors == prepared.candidates
    assert any("Copy-detection embeddings unavailable" in warning for warning in result.warnings)


def test_copy_embedder_failure_is_reported_next_to_the_hash_verdicts(tmp_path: Path) -> None:
    prepared = _prepared(
        _photo_asset("first", checksum="same-source"),
        _photo_asset("second", when=WHEN + timedelta(days=400), checksum="same-source"),
        _photo_asset("other", when=WHEN + timedelta(days=800)),
        pixels={"other": _split_jpeg(vertical=True)},
    )
    atlas = build_visual_atlas(prepared.visual_sources, frame_cache_dir=tmp_path / "frames")

    # WHY: stands in for a torchscript checkpoint that will not load.
    def _broken(_jpeg_bytes: bytes) -> tuple[float, ...]:
        raise RuntimeError("no such file: sscd_disc_mixup.torchscript.pt")

    result = review_final_duplicates(
        prepared.candidates,
        descriptions={},
        atlas=atlas,
        requester=object(),
        sheet_output_dir=tmp_path / "sheets",
        copy_embedder=_broken,
    )

    assert tuple(candidate.asset_id for candidate in result.survivors) == ("first", "other")
    assert any("Copy-detection embeddings unavailable" in warning for warning in result.warnings)


def test_confirmed_copy_keeps_the_member_that_is_not_a_document(tmp_path: Path) -> None:
    prepared = _prepared(
        _photo_asset("page-scan"),
        _photo_asset("the-picture", when=WHEN + timedelta(days=400)),
    )
    atlas = VisualAtlas(
        (
            AtlasTile("page-scan", "photo", b"page", "same-tile", 1),
            AtlasTile("the-picture", "photo", b"picture", "same-tile", 1),
        )
    )

    with patch(
        "immich_memories.analysis.selection_final_duplicates.confirm_same_picture_pairs",
        return_value=(SamePicturePairDecision("page-scan", "the-picture", True),),
    ):
        result = review_final_duplicates(
            prepared.candidates,
            descriptions={
                "page-scan": "A newspaper front page with a photograph of two runners.",
                "the-picture": "Two runners crossing a finish line under a banner.",
            },
            atlas=atlas,
            requester=object(),
            sheet_output_dir=tmp_path / "sheets",
        )

    assert tuple(candidate.asset_id for candidate in result.survivors) == ("the-picture",)
    assert result.absorbed[0].asset_id == "page-scan"
    assert "non-document" in result.absorbed[0].reason


def test_final_duplicate_pair_carries_its_real_distance_into_the_shortcut(tmp_path: Path) -> None:
    same_pixels = _split_jpeg(vertical=True)
    prepared = _prepared(
        _photo_asset("re-import"),
        _photo_asset("original", when=WHEN + timedelta(days=400)),
        pixels={"re-import": same_pixels, "original": same_pixels},
    )
    atlas = build_visual_atlas(prepared.visual_sources, frame_cache_dir=tmp_path / "frames")
    asks = 0

    async def _answer(_prompt, _config, **kwargs):
        nonlocal asks
        asks += 1
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return _pair_answer(True)

    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = review_final_duplicates(
            prepared.candidates,
            descriptions={},
            atlas=atlas,
            requester=_gateway(tmp_path, prepared.trace),
            sheet_output_dir=tmp_path / "sheets",
        )

    assert result.nominations[0].signals == ("exact-atlas-tile",)
    assert result.nominations[0].perceptual_distance == 0
    assert asks == 1
    assert tuple(candidate.asset_id for candidate in result.survivors) == ("re-import",)
