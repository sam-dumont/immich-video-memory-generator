"""The integrity checks a lifetime window passes through cost a pass over it, not its square.

A person film over 37 years admits ~66k pictures out of ~111k fetched. Two checks rebuilt a
set of the whole window once per picture: source-eligibility conservation (run on both source
passes and the cull) and the annotation batch's order check. On that window they took about
33 min and 7 min of a 44 min warm run (#1196).

The ids here count their own hashes, so the test counts the work instead of timing it.
"""

from __future__ import annotations

from immich_memories.analysis.annotation_lines import (
    AnnotationContract,
    AnnotationLineBatch,
    AssetAnnotationLine,
)
from immich_memories.analysis.editorial_contracts import (
    DecisionProvenance,
    PassTrace,
    TraceDecision,
)
from immich_memories.analysis.selection_trace import Trace

WINDOW = 2_000


class _CountedId(str):
    hashes = 0

    def __hash__(self) -> int:
        _CountedId.hashes += 1
        return str.__hash__(self)


def _window() -> tuple[str, ...]:
    _CountedId.hashes = 0
    return tuple(_CountedId(f"asset-{n:05}") for n in range(WINDOW))


def test_conservation_over_a_window_hashes_each_picture_a_bounded_number_of_times():
    ids = _window()
    trace = Trace()

    trace.record_editorial_pass(
        PassTrace(
            name="source-eligibility",
            input_ids=ids,
            kept_ids=ids[::2],
            rejected=tuple(TraceDecision(asset_id, "outside date scope") for asset_id in ids[1::2]),
            unresolved=(),
            duration_before=0.0,
            duration_after=0.0,
            provenance=DecisionProvenance(
                pass_name="source-eligibility",  # noqa: S106 - a pass name, not a secret
                pass_version="1",  # noqa: S106 - a pass version, not a secret
                schema_version="1",
                model_identity="",
                input_ids=ids,
                sheet_hashes=(),
                request_key="source-eligibility",
                cache_hit=False,
            ),
        )
    )

    assert trace.editorial_passes[0].conservation.valid
    assert _CountedId.hashes < 20 * WINDOW


def test_an_annotation_batch_over_a_window_hashes_each_picture_a_bounded_number_of_times():
    ids = _window()

    AnnotationLineBatch(
        requested_asset_ids=ids,
        lines=tuple(AssetAnnotationLine(asset_id=asset_id, text="a line") for asset_id in ids[1:]),
        missing_asset_ids=ids[:1],
        contract=AnnotationContract(renderer_version="v1", producer_versions=("pixel:v1",)),
    )

    assert _CountedId.hashes < 20 * WINDOW
