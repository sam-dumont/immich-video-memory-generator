"""Durable literal descriptions for the non-actuating novelty experiment."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
from immich_memories.analysis.llm_query import LLMTransportAttempt
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.config_models_llm import LLMConfig
from tests.conftest import make_asset


def _jpeg(colour: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (800, 600), colour).save(output, "JPEG")
    return output.getvalue()


def _prepared(*assets):
    palette = ("navy", "teal", "maroon", "olive", "purple", "brown")
    colours = {asset.id: palette[index % len(palette)] for index, asset in enumerate(assets)}
    return prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda asset: _jpeg(colours[asset.id]),
        ),
    )


def _gateway(tmp_path: Path, trace):
    return VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=trace,
    )


def test_an_asset_description_survives_into_a_wider_memory_scope(tmp_path: Path) -> None:
    """The same picture is described once even when its neighbours change."""
    from immich_memories.analysis.selection_descriptions import describe_editorial_assets

    when = datetime(2024, 2, 3, 12, tzinfo=UTC)
    target = make_asset("target", file_created_at=when)
    first_scope = _prepared(target)
    wider_scope = _prepared(
        target,
        make_asset("new-neighbour", file_created_at=when + timedelta(seconds=2)),
    )
    asks = 0

    async def _answer(_prompt, _config, **kwargs):
        nonlocal asks
        asks += 1
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return json.dumps(
            {
                "schema_version": "asset-description-v1",
                "description": f"literal description {asks}",
            }
        )

    # Both production source preparations, rendering, gateway and persistent cache stay real.
    # WHY: query_llm is the sole external boundary.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        first = describe_editorial_assets(
            first_scope,
            requester=_gateway(tmp_path, first_scope.trace),
            output_dir=tmp_path / "first",
            frame_cache_dir=None,
        )
        wider = describe_editorial_assets(
            wider_scope,
            requester=_gateway(tmp_path, wider_scope.trace),
            output_dir=tmp_path / "wider",
            frame_cache_dir=None,
        )

    assert asks == 2, "the target is reused; only its new neighbour needs a description"
    first_by_id = {item.asset_id: item for item in first.descriptions}
    wider_by_id = {item.asset_id: item for item in wider.descriptions}
    assert first_by_id["target"].text == "literal description 1"
    assert wider_by_id["target"].text == "literal description 1"
    assert wider_by_id["target"].provenance.cache_hit is True


def test_an_asset_description_is_grounded_in_a_400px_visual(tmp_path: Path) -> None:
    """Object identity gets the fidelity where the measured answer stabilised."""
    from immich_memories.analysis.selection_descriptions import describe_editorial_assets

    prepared = _prepared(make_asset("object", file_created_at=datetime(2024, 2, 3, 12, tzinfo=UTC)))
    attached: list[bytes] = []

    async def _answer(_prompt, _config, **kwargs):
        attached.extend(kwargs["images"])
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return json.dumps(
            {
                "schema_version": "asset-description-v1",
                "description": "a small object on a patterned background",
            }
        )

    # The production renderer and exact bytes attached by the gateway stay real.
    # WHY: query_llm is the sole external boundary.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        describe_editorial_assets(
            prepared,
            requester=_gateway(tmp_path, prepared.trace),
            output_dir=tmp_path / "descriptions",
            frame_cache_dir=None,
        )

    assert len(attached) == 1
    with Image.open(BytesIO(attached[0])) as page:
        assert page.size == (400, 400)


def test_a_filmstrip_description_classifies_motion_in_the_same_call(tmp_path: Path) -> None:
    """Motion value is banked with the literal description, not bought later."""
    from immich_memories.analysis.selection_descriptions import describe_editorial_assets

    asset = make_asset("motion", file_created_at=datetime(2024, 2, 3, 12, tzinfo=UTC))
    asset.live_photo_video_id = "motion-component"
    prepared = _prepared(asset)
    filmstrip = SimpleNamespace(
        entity_id="motion",
        kind="filmstrip",
        jpeg_bytes=_jpeg("purple"),
        sha256="filmstrip-hash",
        frame_count=4,
        unavailable_reason=None,
    )
    atlas = SimpleNamespace(tile_for=lambda _asset_id: filmstrip)
    asks = 0
    prompts: list[str] = []

    async def _answer(prompt, _config, **kwargs):
        nonlocal asks
        asks += 1
        prompts.append(prompt)
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return json.dumps(
            {
                "schema_version": "asset-motion-description-v1",
                "description": "A child runs into the water and splashes.",
                "motion_contribution": "meaningful",
                "motion_reason": "The sequence shows the run ending in the splash.",
            }
        )

    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = describe_editorial_assets(
            prepared,
            requester=_gateway(tmp_path, prepared.trace),
            output_dir=tmp_path / "motion-descriptions",
            frame_cache_dir=None,
            atlas=atlas,
        )

    assert asks == 1
    assert len(result.descriptions) == 1
    assert result.descriptions[0].motion_contribution == "meaningful"
    assert result.descriptions[0].motion_reason == (
        "The sequence shows the run ending in the splash."
    )
    assert "camera movement" in prompts[0]
    assert "still_sufficient" in prompts[0]


def test_a_video_filmstrip_classifies_whether_motion_adds_value(tmp_path: Path) -> None:
    """A video earns its medium preference from observed change, just like a Live Photo."""
    from immich_memories.analysis.selection_descriptions import describe_editorial_assets

    asset = make_asset("video-motion", file_created_at=datetime(2024, 2, 3, 12, tzinfo=UTC))
    prepared = _prepared(asset)
    filmstrip = SimpleNamespace(
        entity_id=asset.id,
        kind="filmstrip",
        jpeg_bytes=_jpeg("orange"),
        sha256="video-filmstrip-hash",
        frame_count=4,
        unavailable_reason=None,
    )
    prompts: list[str] = []

    async def _answer(prompt, _config, **kwargs):
        prompts.append(prompt)
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return json.dumps(
            {
                "schema_version": "asset-motion-description-v1",
                "description": "A hiker crosses a stream from one bank to the other.",
                "motion_contribution": "meaningful",
                "motion_reason": "The crossing action and route unfold across the frames.",
            }
        )

    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = describe_editorial_assets(
            prepared,
            requester=_gateway(tmp_path, prepared.trace),
            output_dir=tmp_path / "video-motion-descriptions",
            frame_cache_dir=None,
            atlas=SimpleNamespace(tile_for=lambda _asset_id: filmstrip),
        )

    assert len(prompts) == 1
    assert "still_sufficient" in prompts[0]
    assert result.descriptions[0].motion_contribution == "meaningful"


def test_motion_description_survives_when_only_the_explanation_is_missing(
    tmp_path: Path,
) -> None:
    """An optional explanation cannot void the observed motion verdict."""
    from immich_memories.analysis.selection_descriptions import describe_editorial_assets

    asset = make_asset(
        "motion-without-reason", file_created_at=datetime(2024, 2, 3, 12, tzinfo=UTC)
    )
    asset.live_photo_video_id = "motion-component"
    prepared = _prepared(asset)
    filmstrip = SimpleNamespace(
        entity_id=asset.id,
        kind="filmstrip",
        jpeg_bytes=_jpeg("purple"),
        sha256="filmstrip-without-reason",
        frame_count=4,
        unavailable_reason=None,
    )

    async def _answer(_prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return json.dumps(
            {
                "schema_version": "asset-motion-description-v1",
                "description": "A child runs through a sprinkler.",
                "motion_contribution": "meaningful",
            }
        )

    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = describe_editorial_assets(
            prepared,
            requester=_gateway(tmp_path, prepared.trace),
            output_dir=tmp_path / "motion-without-reason",
            frame_cache_dir=None,
            atlas=SimpleNamespace(tile_for=lambda _asset_id: filmstrip),
        )

    assert len(result.descriptions) == 1
    assert result.descriptions[0].text == "A child runs through a sprinkler."
    assert result.descriptions[0].motion_contribution == "meaningful"
    assert result.descriptions[0].motion_reason is None
    assert result.warnings == ()


def test_a_new_filmstrip_cannot_reuse_a_still_only_description(tmp_path: Path) -> None:
    from immich_memories.analysis.selection_descriptions import describe_editorial_assets

    asset = make_asset("changing-evidence", file_created_at=datetime(2024, 2, 3, 12, tzinfo=UTC))
    asset.live_photo_video_id = "motion-component"
    prepared = _prepared(asset)
    photo_tile = SimpleNamespace(
        entity_id=asset.id,
        kind="photo",
        jpeg_bytes=_jpeg("navy"),
        sha256="photo-hash",
        frame_count=1,
        unavailable_reason=None,
    )
    filmstrip_tile = SimpleNamespace(
        entity_id=asset.id,
        kind="filmstrip",
        jpeg_bytes=_jpeg("teal"),
        sha256="filmstrip-hash",
        frame_count=4,
        unavailable_reason=None,
    )
    asks = 0

    async def _answer(prompt, _config, **kwargs):
        nonlocal asks
        asks += 1
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if "still_sufficient" in prompt:
            return json.dumps(
                {
                    "schema_version": "asset-motion-description-v1",
                    "description": "A person turns and smiles.",
                    "motion_contribution": "meaningful",
                    "motion_reason": "The turn reveals the smile.",
                }
            )
        return json.dumps(
            {
                "schema_version": "asset-description-v1",
                "description": "A person facing sideways.",
            }
        )

    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        still = describe_editorial_assets(
            prepared,
            requester=_gateway(tmp_path, prepared.trace),
            output_dir=tmp_path / "still",
            frame_cache_dir=None,
            atlas=SimpleNamespace(tile_for=lambda _asset_id: photo_tile),
        )
        moving = describe_editorial_assets(
            prepared,
            requester=_gateway(tmp_path, prepared.trace),
            output_dir=tmp_path / "moving",
            frame_cache_dir=None,
            atlas=SimpleNamespace(tile_for=lambda _asset_id: filmstrip_tile),
        )
        replay = describe_editorial_assets(
            prepared,
            requester=_gateway(tmp_path, prepared.trace),
            output_dir=tmp_path / "moving-replay",
            frame_cache_dir=None,
            atlas=SimpleNamespace(tile_for=lambda _asset_id: filmstrip_tile),
        )

    assert asks == 2
    assert still.descriptions[0].motion_contribution == "not_observed"
    assert moving.descriptions[0].motion_contribution == "meaningful"
    assert replay.descriptions[0].provenance.cache_hit is True


def test_an_unreadable_description_never_changes_membership(tmp_path: Path) -> None:
    """The experiment fails loudly and leaves the candidate corpus untouched."""
    from immich_memories.analysis.selection_descriptions import describe_editorial_assets

    prepared = _prepared(
        make_asset("still-a-candidate", file_created_at=datetime(2024, 2, 3, 12, tzinfo=UTC))
    )

    async def _refuse(_prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return "I cannot describe that."

    # Parsing, warning ownership and the unchanged production source corpus stay real.
    # WHY: query_llm is the sole external boundary.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_refuse):
        result = describe_editorial_assets(
            prepared,
            requester=_gateway(tmp_path, prepared.trace),
            output_dir=tmp_path / "descriptions",
            frame_cache_dir=None,
        )

    assert result.descriptions == ()
    assert prepared.candidate_ids == ("still-a-candidate",)
    assert result.warnings == tuple(prepared.trace.warnings)
    assert result.warnings[0].startswith("!! asset description unreadable")


def test_five_cold_assets_cost_one_packed_call_and_one_solo_call(tmp_path: Path) -> None:
    """Pack-four batches cold assets four at a time; a remainder of one stays solo."""
    from immich_memories.analysis.selection_descriptions import describe_editorial_assets

    when = datetime(2024, 2, 3, 12, tzinfo=UTC)
    assets = [
        make_asset(f"asset-{index}", file_created_at=when + timedelta(seconds=index))
        for index in range(5)
    ]
    prepared = _prepared(*assets)
    request_sizes: list[int] = []

    async def _answer(_prompt, _config, **kwargs):
        images = kwargs["images"]
        request_sizes.append(len(images))
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if len(images) == 1:
            return json.dumps(
                {"schema_version": "asset-description-v1", "description": "literal 4"}
            )
        return json.dumps(
            {
                "schema_version": "asset-description-packed-v1",
                "assets": [
                    {"asset_id": str(index + 1), "description": f"literal {index}"}
                    for index in range(len(images))
                ],
            }
        )

    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = describe_editorial_assets(
            prepared,
            requester=_gateway(tmp_path, prepared.trace),
            output_dir=tmp_path / "descriptions",
            frame_cache_dir=None,
        )

    assert sorted(request_sizes) == [1, 4]
    assert [item.asset_id for item in result.descriptions] == [asset.id for asset in assets]
    assert [item.text for item in result.descriptions] == [
        "literal 0",
        "literal 1",
        "literal 2",
        "literal 3",
        "literal 4",
    ]


def test_a_malformed_packed_member_is_retried_alone(tmp_path: Path) -> None:
    """One bad row in a packed answer falls back to a solo request for just that asset."""
    from immich_memories.analysis.selection_descriptions import describe_editorial_assets

    when = datetime(2024, 2, 3, 12, tzinfo=UTC)
    assets = [
        make_asset(f"asset-{index}", file_created_at=when + timedelta(seconds=index))
        for index in range(4)
    ]
    prepared = _prepared(*assets)
    request_sizes: list[int] = []

    async def _answer(_prompt, _config, **kwargs):
        images = kwargs["images"]
        request_sizes.append(len(images))
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        if len(images) == 1:
            return json.dumps(
                {"schema_version": "asset-description-v1", "description": "solo retry for 2"}
            )
        return json.dumps(
            {
                "schema_version": "asset-description-packed-v1",
                "assets": [
                    {"asset_id": "1", "description": "literal 0"},
                    {"asset_id": "2", "description": "literal 1"},
                    # Alias "3" (asset-2) is missing motion_contribution-style content
                    # here it is simply missing the row entirely: a malformed member.
                    {"asset_id": "4", "description": "literal 3"},
                ],
            }
        )

    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = describe_editorial_assets(
            prepared,
            requester=_gateway(tmp_path, prepared.trace),
            output_dir=tmp_path / "descriptions",
            frame_cache_dir=None,
        )

    assert sorted(request_sizes) == [1, 4]
    by_id = {item.asset_id: item.text for item in result.descriptions}
    assert by_id["asset-0"] == "literal 0"
    assert by_id["asset-1"] == "literal 1"
    assert by_id["asset-2"] == "solo retry for 2"
    assert by_id["asset-3"] == "literal 3"


def test_a_packed_extract_survives_into_a_later_solo_lookup(tmp_path: Path) -> None:
    """Pack membership is not stable across runs; the per-asset answer must still warm."""
    from immich_memories.analysis.selection_descriptions import describe_editorial_assets

    when = datetime(2024, 2, 3, 12, tzinfo=UTC)
    assets = [
        make_asset(f"packed-{index}", file_created_at=when + timedelta(seconds=index))
        for index in range(4)
    ]
    prepared = _prepared(*assets)
    asks = 0

    async def _answer(_prompt, _config, **kwargs):
        nonlocal asks
        asks += 1
        images = kwargs["images"]
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return json.dumps(
            {
                "schema_version": "asset-description-packed-v1",
                "assets": [
                    {"asset_id": str(index + 1), "description": f"packed literal {index}"}
                    for index in range(len(images))
                ],
            }
        )

    gateway = _gateway(tmp_path, prepared.trace)
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        packed = describe_editorial_assets(
            prepared,
            requester=gateway,
            output_dir=tmp_path / "packed",
            frame_cache_dir=None,
        )
        # A later run where this asset is alone -- a different regrouping than the
        # pack of four above -- must still find it warm from the packed extract.
        solo = describe_editorial_assets(
            _prepared(assets[0]),
            requester=gateway,
            output_dir=tmp_path / "solo",
            frame_cache_dir=None,
        )

    assert asks == 1
    assert packed.descriptions[0].text == "packed literal 0"
    assert solo.descriptions[0].text == "packed literal 0"
    assert solo.descriptions[0].provenance.cache_hit is True


def test_independent_descriptions_overlap_without_reordering_results(tmp_path: Path) -> None:
    """oMLX concurrency changes wall time, never the source chronology.

    Eight cold candidates so pack-four forms two independent packed work
    items: with only one or two candidates they would fold into a single
    pack, leaving the concurrency knob nothing left to parallelize.
    """
    from immich_memories.analysis.selection_descriptions import describe_editorial_assets

    when = datetime(2024, 2, 3, 12, tzinfo=UTC)
    assets = [
        make_asset(f"asset-{index}", file_created_at=when + timedelta(seconds=index))
        for index in range(8)
    ]
    prepared = _prepared(*assets)
    lock = threading.Lock()
    active = 0
    peak = 0

    async def _answer(_prompt, _config, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.05)
        with lock:
            active -= 1
        images = kwargs["images"]
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return json.dumps(
            {
                "schema_version": "asset-description-packed-v1",
                "assets": [
                    {"asset_id": str(index + 1), "description": "one literal visual"}
                    for index in range(len(images))
                ],
            }
        )

    # WHY: the model is external; the real worker pool is the concurrency behavior under test.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = describe_editorial_assets(
            prepared,
            requester=_gateway(tmp_path, prepared.trace),
            output_dir=tmp_path / "descriptions",
            frame_cache_dir=None,
            concurrency=2,
        )

    assert peak == 2
    assert tuple(item.asset_id for item in result.descriptions) == tuple(
        asset.id for asset in assets
    )


def test_every_description_request_offers_a_hedged_setting_field(tmp_path: Path) -> None:
    """A place inside a people-photo needs its own slot, hedged so a plain scene can decline."""
    from immich_memories.analysis import selection_descriptions

    prepared = _prepared(make_asset("still", file_created_at=datetime(2024, 2, 3, 12, tzinfo=UTC)))
    prompts: list[str] = []

    async def _answer(prompt, _config, **kwargs):
        prompts.append(prompt)
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return json.dumps(
            {
                "schema_version": "asset-description-v1",
                "description": "a man in a red and black jacket",
                "setting": "a snow-covered ski station",
            }
        )

    # WHY: query_llm is the sole external boundary; rendering and the cache stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = selection_descriptions.describe_editorial_assets(
            prepared,
            requester=_gateway(tmp_path, prepared.trace),
            output_dir=tmp_path / "still",
            frame_cache_dir=None,
        )

    assert '"setting":"where this is, in a few words, or insufficient evidence"' in prompts[0]
    assert result.descriptions[0].setting == "a snow-covered ski station"
    versions = (
        selection_descriptions.ASSET_DESCRIPTION_PASS_VERSION,
        selection_descriptions.ASSET_DESCRIPTION_PROMPT_VERSION,
    )
    assert versions == ("asset-description-v2", "asset-description-prompt-v2")


def test_a_packed_setting_is_banked_for_the_later_solo_lookup(tmp_path: Path) -> None:
    """Pack membership churns between runs; the setting must survive the regrouping too."""
    from immich_memories.analysis.selection_descriptions import describe_editorial_assets

    when = datetime(2024, 2, 3, 12, tzinfo=UTC)
    assets = [
        make_asset(f"packed-{index}", file_created_at=when + timedelta(seconds=index))
        for index in range(4)
    ]
    prepared = _prepared(*assets)
    asks = 0

    async def _answer(_prompt, _config, **kwargs):
        nonlocal asks
        asks += 1
        images = kwargs["images"]
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return json.dumps(
            {
                "schema_version": "asset-description-packed-v1",
                "assets": [
                    {
                        "asset_id": str(index + 1),
                        "description": f"packed literal {index}",
                        "setting": "a snow-covered ski station",
                    }
                    for index in range(len(images))
                ],
            }
        )

    gateway = _gateway(tmp_path, prepared.trace)
    # WHY: query_llm is the sole external boundary; the persistent cache stays real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        packed = describe_editorial_assets(
            prepared,
            requester=gateway,
            output_dir=tmp_path / "packed",
            frame_cache_dir=None,
        )
        solo = describe_editorial_assets(
            _prepared(assets[0]),
            requester=gateway,
            output_dir=tmp_path / "solo",
            frame_cache_dir=None,
        )

    assert asks == 1
    assert packed.descriptions[0].setting == "a snow-covered ski station"
    assert solo.descriptions[0].setting == "a snow-covered ski station"
    assert solo.descriptions[0].provenance.cache_hit is True


def test_a_filmstrip_setting_rides_beside_the_motion_verdict(tmp_path: Path) -> None:
    """Live photos are most of the corpus; a place they show must register there too."""
    from immich_memories.analysis.selection_descriptions import describe_editorial_assets

    asset = make_asset("clip", file_created_at=datetime(2024, 2, 3, 12, tzinfo=UTC))
    asset.live_photo_video_id = "clip-component"
    prepared = _prepared(asset)
    filmstrip = SimpleNamespace(
        entity_id="clip",
        kind="filmstrip",
        jpeg_bytes=_jpeg("purple"),
        sha256="clip-filmstrip-hash",
        frame_count=4,
        unavailable_reason=None,
    )

    async def _answer(_prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return json.dumps(
            {
                "schema_version": "asset-motion-description-v1",
                "description": "a child runs downhill",
                "motion_contribution": "meaningful",
                "motion_reason": "the run only exists across frames",
                "setting": "a snow-covered hillside",
            }
        )

    # WHY: query_llm is the sole external boundary; the frame extractor stays real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = describe_editorial_assets(
            prepared,
            requester=_gateway(tmp_path, prepared.trace),
            output_dir=tmp_path / "clip",
            frame_cache_dir=None,
            atlas=SimpleNamespace(tile_for=lambda _asset_id: filmstrip),
        )

    assert result.descriptions[0].setting == "a snow-covered hillside"
    assert result.descriptions[0].motion_contribution == "meaningful"
