"""Editorial pass records are safe to hand from one pass to the next."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from immich_memories.analysis.editorial_contracts import (
    ConservationCheck,
    DecisionProvenance,
    PassTrace,
    RequestTrace,
    TraceDecision,
)


def test_editorial_contracts_cannot_rewrite_a_previous_pass() -> None:
    """A later pass cannot mutate the inputs, fate, or request it inherited."""
    provenance = DecisionProvenance(
        pass_name="cull",  # noqa: S106 - test-only pass identity
        pass_version="1",  # noqa: S106 - test-only pass identity
        schema_version="1",
        model_identity="test-model",
        input_ids=("first", "second"),
        sheet_hashes=("sheet-hash",),
        request_key="request-key",
        cache_hit=False,
    )
    decision = TraceDecision("second", "unusable exposure")
    pass_trace = PassTrace(
        name="cull",
        input_ids=("first", "second"),
        kept_ids=("first",),
        rejected=(decision,),
        unresolved=(),
        duration_before=8.0,
        duration_after=4.0,
        provenance=provenance,
    )
    request = RequestTrace(provenance=provenance, attached_sheet_hashes=("sheet-hash",))
    conservation = ConservationCheck(valid=True, missing_ids=(), duplicate_ids=())

    with pytest.raises(FrozenInstanceError):
        provenance.input_ids = ("replacement",)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decision.reason = "replacement"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        pass_trace.kept_ids = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        request.attached_sheet_hashes = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        conservation.valid = False  # type: ignore[misc]
