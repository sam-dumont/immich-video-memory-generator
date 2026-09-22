"""Real acquisition and fact stores must finish before the semantic editor starts."""

import json
import logging
from dataclasses import replace
from datetime import timedelta

import pytest

from immich_memories.analysis.editorial_description_contract import validate_envelope
from immich_memories.analysis.editorial_planner import EditorialPlan
from immich_memories.analysis.editorial_preparation import prepare_editorial_annotations
from immich_memories.analysis.editorial_preparation_captions import _remember_caption
from immich_memories.analysis.editorial_runtime import (
    EditorialInputsRequired,
    EditorialRunContext,
    build_editorial_planner,
)
from immich_memories.analysis.editorial_runtime_ports import EditorialRuntimePorts
from immich_memories.analysis.selection_trace import Trace
from immich_memories.api.immich import ImmichAPIError, ImmichNotFoundError
from immich_memories.config_loader import Config
from immich_memories.operations.cut_progress import read_stage_progress
from immich_memories.operations.editorial_attempt import read_editorial_attempt
from tests.test_editorial_preparation import preview, successful_ports
from tests.test_editorial_runtime import _window
from tests.test_editorial_source_route import photo


def build(tmp_path, *, providers, fetched, tier="full", sources=None, preview_port=None):
    window = _window(2020, 5, 2)
    if sources is None:
        sources = [
            photo("ordinary", at=window.start + timedelta(hours=9)),
            photo("display", at=window.start + timedelta(hours=10)),
            photo("later", at=window.start + timedelta(hours=11)),
        ]
    config = Config(
        llm={"model": "offline-editor"},
        cache={"directory": str(tmp_path / "cache")},
        analysis={"min_source_short_side": 0},
        # The optional picture reader opens a socket to a local address; these cases are
        # about the producers the tier itself demands.
        editorial={"preparation": {"tier": tier, "picture_facts": {"enabled": False}}},
    )
    acquisitions = []

    def acquire(_client, scope):
        acquisitions.append(scope)
        return sources

    def fetch(asset_id):
        fetched.append(asset_id)
        return preview() if preview_port is None else preview_port(asset_id)

    def prepare(**kwargs):
        return prepare_editorial_annotations(**kwargs, ports=providers)

    planner = build_editorial_planner(
        client=object(),
        config=config,
        thumbnail_cache=tmp_path / "previews",
        context=EditorialRunContext(
            "month", "A month", "monthly_highlights", (window,), 60, tmp_path / "runs"
        ),
        ports=EditorialRuntimePorts(
            load_people=lambda: {},
            fetch_full_source=acquire,
            fetch_preview=lambda _client, asset_id: fetch(asset_id),
            prepare_annotations=prepare,
        ),
    )
    return planner, sources, acquisitions


def test_cold_preparation_gates_before_reading_and_warm_reuses_every_producer(
    tmp_path, monkeypatch
):
    produced, fetched = [], []
    providers = successful_ports(produced)

    def captions(**kwargs):
        produced.append(("captions", tuple(kwargs["asset_ids"])))
        for asset_id in kwargs["asset_ids"]:
            description = (
                "A screenshot showing a dashboard."
                if asset_id == "display"
                else "People carry furniture together."
            )
            _remember_caption(
                kwargs["connection"],
                asset_id,
                validate_envelope({"description": description, "setting": "a room"}),
            )
        return {}

    planner, sources, acquired = build(
        tmp_path, providers=replace(providers, captions=captions), fetched=fetched
    )
    observed = []

    def editor(candidates, *, prepared, trace, **_):
        observed.append(prepared)
        assert prepared.candidate_ids == ("ordinary", "later")
        assert prepared.excluded_ids == ("display",)
        assert tuple(row.clip.asset.id for row in candidates) == prepared.candidate_ids
        passes = [p for p in trace.editorial_passes if p.name == "source-eligibility"]
        assert len(passes) == 1
        assert passes[0].rejected[0].reason == "screen-text"
        return EditorialPlan()

    # The native editor has its own full cold/warm integration test. Here its
    # input boundary proves that preparation and factual gating precede it.
    monkeypatch.setattr(planner._planner, "plan_prepared", editor)
    planner.plan_source(sources, trace=Trace())
    assert produced and fetched == [s.id for s in sources]
    gate = json.loads((planner.last_attempt_directory / "source-gate.private.json").read_text())
    assert gate["excluded"] == {"display": "screen-text"}
    produced.clear()
    fetched.clear()
    planner.plan_source(sources, trace=Trace())
    assert not produced and not fetched
    assert len(acquired) == 1 and len(observed) == 2


def test_missing_required_producer_blocks_all_editing_and_records_failed_attempt(
    tmp_path, monkeypatch
):
    providers = replace(successful_ports([]), heads=lambda **_: None)
    planner, sources, _ = build(tmp_path, providers=providers, fetched=[])

    def forbidden(*_, **__):
        pytest.fail("semantic editing began with incomplete source facts")

    monkeypatch.setattr(planner._planner, "plan_prepared", forbidden)
    with pytest.raises(EditorialInputsRequired, match="head:activity"):
        planner.plan_source(sources, trace=Trace())
    attempt = planner.last_attempt_directory
    assert read_editorial_attempt(attempt)["status"] == "failed"
    report = json.loads((attempt / "preparation.private.json").read_text())
    assert len(report["missing_by_producer"]) == 6
    assert all(len(ids) == 3 for ids in report["missing_by_producer"].values())


def test_a_refusing_producer_puts_its_own_reason_in_the_message_that_stops_the_run(
    tmp_path, monkeypatch
):
    """A count of missing facts is the symptom; the producer's sentence is the cause.

    A detector that could not load its model used to leave only
    `head:doc_docling@det-v1: 1440` behind, with the reason written to
    preparation.private.json and nowhere a reader would look.
    """
    reason = "doc_docling has no model: run `immich-memories models fetch`"
    providers = replace(successful_ports([]), detectors=lambda **_: {"doc_docling": reason})
    planner, sources, _ = build(tmp_path, providers=providers, fetched=[])
    monkeypatch.setattr(
        planner._planner,
        "plan_prepared",
        lambda *_, **__: pytest.fail("editing began with a refusing producer"),
    )

    with pytest.raises(EditorialInputsRequired, match="models fetch") as raised:
        planner.plan_source(sources, trace=Trace())

    assert "head:doc_docling@det-v2" in str(raised.value)


PREVIEW_404 = "preview unavailable at Immich (HTTP 404)"


def test_a_source_immich_cannot_preview_leaves_the_film_by_name(tmp_path, monkeypatch, caplog):
    """Six of 30,188 sources 404'd on their preview and cost a run 31 minutes of work.

    The server's answer about one source is not a producer outage. Those sources
    leave the cut with a reason, and the other 30,182 are edited.
    """
    window = _window(2020, 5, 2)
    sources = [
        photo(f"picture-{index}", at=window.start + timedelta(hours=9, minutes=index))
        for index in range(10)
    ]
    refused = {"picture-2", "picture-6"}

    def serve(asset_id):
        if asset_id in refused:
            # WHY: Immich is the external boundary. This is what its API answers
            # for an asset whose thumbnail the server never generated.
            raise ImmichNotFoundError("Resource not found", status_code=404)
        return preview()

    planner, _, _ = build(
        tmp_path,
        providers=successful_ports([]),
        fetched=[],
        sources=sources,
        preview_port=serve,
    )
    observed = []

    def editor(_candidates, *, prepared, trace, **_kwargs):
        observed.append((prepared, trace))
        return EditorialPlan()

    monkeypatch.setattr(planner._planner, "plan_prepared", editor)
    with caplog.at_level(logging.WARNING, logger="immich_memories.analysis.editorial_runtime"):
        planner.plan_source(sources, trace=Trace())

    prepared, trace = observed[0]
    assert set(prepared.candidate_ids) == {source.id for source in sources} - refused
    rejected = {
        decision.asset_id: decision.reason
        for editorial_pass in trace.editorial_passes
        if editorial_pass.name == "source-eligibility"
        for decision in editorial_pass.rejected
    }
    assert rejected == dict.fromkeys(refused, PREVIEW_404)
    assert [
        record.getMessage()
        for record in caplog.records
        if record.name == "immich_memories.analysis.editorial_runtime"
        and record.levelno == logging.WARNING
    ] == [f"2 of 10 sources leave the film: {PREVIEW_404}"]
    report = json.loads((planner.last_attempt_directory / "preparation.private.json").read_text())
    assert report["unservable_sources"] == dict.fromkeys(sorted(refused), PREVIEW_404)
    assert not report["missing_by_producer"] and not report["failures"]


def test_a_preview_that_timed_out_still_stops_the_run(tmp_path, monkeypatch):
    """A transport that gave up is unfinished work for the next run, not an answer."""

    def serve(asset_id):
        if asset_id == "later":
            # WHY: Immich is the external boundary. Past the client's retries a
            # timeout arrives as a transport failure carrying no HTTP status.
            raise ImmichAPIError("Request failed: timed out")
        return preview()

    planner, sources, _ = build(
        tmp_path, providers=successful_ports([]), fetched=[], preview_port=serve
    )
    monkeypatch.setattr(
        planner._planner,
        "plan_prepared",
        lambda *_, **__: pytest.fail("editing began without every preview"),
    )

    with pytest.raises(EditorialInputsRequired, match="preview: 1"):
        planner.plan_source(sources, trace=Trace())


def test_the_attempt_carries_live_numbers_and_recent_pictures_beside_its_stage(
    tmp_path, monkeypatch
):
    """The stage sentence keeps its wording; the bar and the strip read the numbers."""
    planner, sources, _ = build(tmp_path, providers=successful_ports([]), fetched=[])
    monkeypatch.setattr(planner._planner, "plan_prepared", lambda *_, **__: EditorialPlan())
    stages: list = []

    planner.plan_source(sources, trace=Trace(), on_stage=stages.append)

    progress = read_stage_progress(planner.last_attempt_directory)
    assert progress is not None
    assert progress.done == progress.total == len(sources)
    assert set(progress.recent_asset_ids) <= {s.id for s in sources}
    assert progress.fraction == 1.0
    # The label vocabulary the phase rows depend on is unchanged.
    assert "Reading dates, places and people" in [stage.stage_label for stage in stages]
    assert any(stage.stage_label.startswith("Preparing previews: ") for stage in stages)


def test_the_no_captions_tier_cuts_without_a_caption_server_and_says_so(
    tmp_path, monkeypatch, caplog
):
    """The NAS tier end to end: no caption request, no block, and the run names the tier."""

    def refuse(**_):
        pytest.fail("the no_captions tier asked a caption server for a description")

    providers = replace(successful_ports([]), captions=refuse)
    planner, sources, _ = build(tmp_path, providers=providers, fetched=[], tier="no_captions")
    monkeypatch.setattr(planner._planner, "plan_prepared", lambda *_, **__: EditorialPlan())

    with caplog.at_level("INFO", logger="immich_memories.analysis.editorial_runtime"):
        planner.plan_source(sources, trace=Trace())

    report = json.loads((planner.last_attempt_directory / "preparation.private.json").read_text())
    assert report["tier"] == "no_captions"
    assert not report["missing_by_producer"] and not report["failures"]
    assert report["seconds_per_picture"]["previews"] >= 0
    assert "preparation tier=no_captions" in caplog.text
