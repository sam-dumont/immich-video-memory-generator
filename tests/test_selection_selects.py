"""Selects reduces repetition it can prove, and refuses the judgement it cannot."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from immich_memories.analysis.editorial_gateway import VisualEditorialGateway
from immich_memories.analysis.llm_query import LLMTransportAttempt
from immich_memories.analysis.selection_selects import run_selects
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.config_models_llm import LLMConfig
from tests.conftest import make_asset

WHEN = datetime(2024, 2, 3, 12, tzinfo=UTC)


def _jpeg(colour: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (24, 18), colour).save(output, "JPEG")
    return output.getvalue()


def run_selects_on(prepared):
    """In the live flow this is Cull's survivors; here every candidate reaches it."""
    return run_selects(prepared, prepared.candidates)


def _prepared(*assets):
    return prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: assets,
            preview_jpeg=lambda _asset: _jpeg("navy"),
        ),
    )


def test_two_cameras_on_one_instant_leave_one_survivor() -> None:
    """Two devices at the same instant are one moment seen twice, not two pictures.

    Measured on a real dense month: 558 of 1468 candidates share an exact capture
    instant. No model is needed to find them.
    """
    result = run_selects_on(
        _prepared(
            make_asset("left-camera", file_created_at=WHEN),
            make_asset("right-camera", file_created_at=WHEN),
        )
    )

    assert tuple(candidate.asset_id for candidate in result.survivors) == ("left-camera",)
    assert result.absorbed[0].asset_id == "right-camera"
    assert result.absorbed[0].kept_asset_id == "left-camera"


def test_a_second_apart_is_a_different_photograph() -> None:
    """The absorbing rule is exact instants only, and this is why.

    Measured on real pixels: two frames 7.6 seconds apart in one place, one
    subject, were two different pictures of a fast-moving event -- and the model
    said so. Any similarity or time-window rule merges them and destroys the
    sequence. Arithmetic is only allowed the part it can prove.
    """
    result = run_selects_on(
        _prepared(
            make_asset("first", file_created_at=WHEN),
            make_asset("a-second-later", file_created_at=WHEN + timedelta(seconds=1)),
        )
    )

    assert len(result.survivors) == 2
    assert result.absorbed == ()


def test_one_instant_in_two_places_is_two_threads_not_one_picture() -> None:
    """Two devices far apart at one time are parallel threads, which is the point.

    Measured on a real day: a racing circuit at 16:37 and a house 120km away at
    16:49. Absorbing by instant alone would fold two people's separate days into
    each other, so the rule has to live inside a moment, which is bounded by
    place as well as time.
    """
    here = make_asset("at-the-circuit", file_created_at=WHEN)
    here.exif_info.latitude, here.exif_info.longitude = 50.44, 5.97
    far = make_asset("at-the-house", file_created_at=WHEN)
    far.exif_info.latitude, far.exif_info.longitude = 51.21, 4.42

    result = run_selects_on(_prepared(here, far))

    assert len(result.survivors) == 2
    assert result.absorbed == ()


def test_the_favourite_survives_its_own_instant() -> None:
    """The star settles it here as it settles every other hard gate."""
    result = run_selects_on(
        _prepared(
            make_asset("plain", file_created_at=WHEN),
            make_asset("starred", file_created_at=WHEN, is_favorite=True),
        )
    )

    assert tuple(candidate.asset_id for candidate in result.survivors) == ("starred",)
    assert result.absorbed[0].asset_id == "plain"


def _pair_answer(same: bool, reason: str = "same subject, framed alike") -> str:
    return json.dumps({"schema_version": "pair-v1", "same": same, "reason": reason})


def _gateway(tmp_path: Path, trace):
    return VisualEditorialGateway(
        llm_config=LLMConfig(model="vision-test"),
        cache_path=tmp_path / "judgments.db",
        trace=trace,
    )


def test_a_pair_the_model_calls_the_same_picture_leaves_one_survivor(tmp_path: Path) -> None:
    """The one comparison this model can make, and the only one it is asked for.

    Measured: "which is the peak" follows tile position in 0 of 12 moments, but
    "are these two the same picture" agrees with itself under swap 27 times in
    30. So sameness is asked, and which of the two ships is not asked at all.
    """
    prepared = _prepared(
        make_asset("first-try", file_created_at=WHEN),
        make_asset("second-try", file_created_at=WHEN + timedelta(seconds=5)),
    )

    async def _answer(_prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return _pair_answer(same=True)

    # WHY: query_llm is the only external provider boundary; atlas, sheets, gateway and trace stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_selects(
            prepared,
            prepared.candidates,
            requester=_gateway(tmp_path, prepared.trace),
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=tmp_path / "frames",
        )

    assert tuple(candidate.asset_id for candidate in result.survivors) == ("first-try",)
    assert result.absorbed[0].asset_id == "second-try"


def test_two_arrangements_that_disagree_keep_both_frames(tmp_path: Path) -> None:
    """Disagreement is not noise to be broken by a tie-break; it means keep.

    Measured on real pixels: the pairs the swap disagrees about are the ones with
    no single right answer -- a wide frame of a room against a close portrait of
    the child in it is genuinely both one attempt and two pictures. Keeping both
    is the right answer there, and it matches the project's asymmetry: a wrong
    keep is fixed by a later pass a person can check, a wrong cut is permanent.
    """
    prepared = _prepared(
        make_asset("the-wide-frame", file_created_at=WHEN),
        make_asset("the-close-frame", file_created_at=WHEN + timedelta(seconds=10)),
    )
    orders: list[str] = []

    async def _answer(_prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        orders.append("asked")
        return _pair_answer(same=len(orders) == 1)

    # WHY: query_llm is the only external provider boundary; atlas, sheets, gateway and trace stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_selects(
            prepared,
            prepared.candidates,
            requester=_gateway(tmp_path, prepared.trace),
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=tmp_path / "frames",
        )

    assert len(orders) == 2, "both arrangements are asked before anything is absorbed"
    assert tuple(candidate.asset_id for candidate in result.survivors) == (
        "the-wide-frame",
        "the-close-frame",
    )
    assert result.absorbed == ()


def test_agreeing_neighbours_chain_into_one_run(tmp_path: Path) -> None:
    """Three frames, two pair questions, one situation -- and no partition was asked for.

    Asked to partition a moment directly the model scored pair Jaccard 0.15 and
    usually returned all-singletons. Chained neighbours rebuild the same answer
    from a question it does hold. Hurn's unit is the situation, and a burst is
    "one attempt at one picture" -- so a run is what this pass is looking for.
    """
    prepared = _prepared(
        make_asset("frame-a", file_created_at=WHEN),
        make_asset("frame-b", file_created_at=WHEN + timedelta(seconds=2)),
        make_asset("frame-c", file_created_at=WHEN + timedelta(seconds=4)),
    )

    async def _answer(_prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return _pair_answer(same=True)

    # WHY: query_llm is the only external provider boundary; atlas, sheets, gateway and trace stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_selects(
            prepared,
            prepared.candidates,
            requester=_gateway(tmp_path, prepared.trace),
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=tmp_path / "frames",
        )

    assert tuple(candidate.asset_id for candidate in result.survivors) == ("frame-a",)
    assert {item.kept_asset_id for item in result.absorbed} == {"frame-a"}


def test_every_favourite_in_a_run_survives_it(tmp_path: Path) -> None:
    """The star settles it here too, and two stars in one run both stay.

    Structure decides among them with the whole cut visible; this pass has only
    the pair in front of it and is not entitled to choose between owner marks.
    """
    prepared = _prepared(
        make_asset("plain", file_created_at=WHEN),
        make_asset("starred-one", file_created_at=WHEN + timedelta(seconds=2), is_favorite=True),
        make_asset("starred-two", file_created_at=WHEN + timedelta(seconds=4), is_favorite=True),
    )

    async def _answer(_prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return _pair_answer(same=True)

    # WHY: query_llm is the only external provider boundary; atlas, sheets, gateway and trace stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_selects(
            prepared,
            prepared.candidates,
            requester=_gateway(tmp_path, prepared.trace),
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=tmp_path / "frames",
        )

    assert tuple(candidate.asset_id for candidate in result.survivors) == (
        "starred-one",
        "starred-two",
    )
    assert tuple(item.asset_id for item in result.absorbed) == ("plain",)


def test_cutting_below_the_craft_floor_warns_and_changes_nothing(tmp_path: Path) -> None:
    """Selects is not an aggressive pass, and the trace has to say when it was.

    Eisenhardt on documentary selects: "you get selects that come down to 50
    percent -- 25 percent in this case, thank God -- of all the material", and
    the big cuts happen later, at structure, not at the item filter. Below that
    floor this pass is cutting on something it cannot justify, so it says so.

    The denominator is what ENTERED Selects, not the eligible corpus: Cull is
    mechanical removal of non-candidates, so a month full of junk would
    otherwise credit this pass with rejections Cull made.
    """
    prepared = _prepared(
        *(
            make_asset(f"frame-{number:02d}", file_created_at=WHEN + timedelta(seconds=2 * number))
            for number in range(10)
        )
    )

    async def _answer(_prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return _pair_answer(same=True)

    # WHY: query_llm is the only external provider boundary; atlas, sheets, gateway and trace stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_selects(
            prepared,
            prepared.candidates,
            requester=_gateway(tmp_path, prepared.trace),
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=tmp_path / "frames",
        )

    assert len(result.survivors) == 1
    assert [warning for warning in result.warnings if "10%" in warning], result.warnings
    assert any("25%" in warning for warning in result.warnings)


def test_a_refused_pair_keeps_both_frames_and_says_so(tmp_path: Path) -> None:
    """Rejection is fail-open here as everywhere: no answer cuts nothing.

    Every failed question shape measured in this project parsed cleanly and
    carried a fluent reason, so the one thing the runtime can still detect --
    an answer that does not arrive at all -- must never be read as "different"
    by accident or as "same" by convenience.
    """
    prepared = _prepared(
        make_asset("kept-one", file_created_at=WHEN),
        make_asset("kept-two", file_created_at=WHEN + timedelta(seconds=3)),
    )

    async def _answer(_prompt, _config, **kwargs):
        raise TimeoutError("generated timeout")

    # WHY: query_llm is the only external provider boundary; atlas, sheets, gateway and trace stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_selects(
            prepared,
            prepared.candidates,
            requester=_gateway(tmp_path, prepared.trace),
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=tmp_path / "frames",
        )

    assert len(result.survivors) == 2
    assert result.absorbed == ()
    assert any(warning.startswith("!!") for warning in result.warnings)
    assert any(warning.startswith("!!") for warning in prepared.trace.warnings)


def test_an_answer_missing_its_verdict_cuts_nothing(tmp_path: Path) -> None:
    """A parsed answer is not a working one -- every failed shape parsed."""
    prepared = _prepared(
        make_asset("kept-one", file_created_at=WHEN),
        make_asset("kept-two", file_created_at=WHEN + timedelta(seconds=3)),
    )

    async def _answer(_prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return json.dumps({"schema_version": "pair-v1", "reason": "fluent and useless"})

    # WHY: query_llm is the only external provider boundary; atlas, sheets, gateway and trace stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_selects(
            prepared,
            prepared.candidates,
            requester=_gateway(tmp_path, prepared.trace),
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=tmp_path / "frames",
        )

    assert len(result.survivors) == 2
    assert result.absorbed == ()
    assert any(warning.startswith("!!") for warning in result.warnings)


def test_the_pass_provenance_admits_when_a_model_was_asked(tmp_path: Path) -> None:
    """A pass that asked cannot be traced as one that did not.

    Stage A is arithmetic and says so. Once Stage B runs, the same PassTrace
    carries a wire contract and a model identity, because that is what decides
    whether a banked answer may be reused against these pixels.
    """
    prepared = _prepared(
        make_asset("frame-a", file_created_at=WHEN),
        make_asset("frame-b", file_created_at=WHEN + timedelta(seconds=3)),
    )

    async def _answer(_prompt, _config, **kwargs):
        kwargs["transport_observer"](LLMTransportAttempt(1, "response", 200))
        return _pair_answer(same=False)

    # WHY: query_llm is the only external provider boundary; atlas, sheets, gateway and trace stay real.
    with patch("immich_memories.analysis.editorial_gateway.query_llm", new=_answer):
        result = run_selects(
            prepared,
            prepared.candidates,
            requester=_gateway(tmp_path, prepared.trace),
            sheet_output_dir=tmp_path / "sheets",
            frame_cache_dir=tmp_path / "frames",
        )

    assert result.trace.provenance.schema_version == "pair-v1"
    assert result.trace.provenance.model_identity == "vision-test"


def test_arithmetic_alone_is_traced_as_asking_nothing() -> None:
    """Without a gateway the pass is Stage A only, and the trace must not imply more."""
    result = run_selects_on(
        _prepared(
            make_asset("left-camera", file_created_at=WHEN),
            make_asset("right-camera", file_created_at=WHEN),
        )
    )

    assert result.trace.provenance.schema_version == "none - this stage asks no model"
    assert result.trace.provenance.model_identity == ""
