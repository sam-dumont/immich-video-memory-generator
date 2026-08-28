"""Durable literal descriptions for the non-actuating novelty experiment."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
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
    palette = ("navy", "teal")
    colours = {asset.id: palette[index] for index, asset in enumerate(assets)}
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


def test_independent_descriptions_overlap_without_reordering_results(tmp_path: Path) -> None:
    """oMLX concurrency changes wall time, never the source chronology."""
    from immich_memories.analysis.selection_descriptions import describe_editorial_assets

    when = datetime(2024, 2, 3, 12, tzinfo=UTC)
    prepared = _prepared(
        make_asset("first", file_created_at=when),
        make_asset("second", file_created_at=when + timedelta(seconds=2)),
    )
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
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return json.dumps(
            {
                "schema_version": "asset-description-v1",
                "description": "one literal visual",
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
    assert tuple(item.asset_id for item in result.descriptions) == ("first", "second")
