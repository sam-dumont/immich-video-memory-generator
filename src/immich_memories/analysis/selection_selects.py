"""Pass 2. Reduce repetition without asking the model to rank.

The pass this replaces asked which frame in a moment was its peak. Measured on
the real library at temperature 0, that question follows tile POSITION rather
than the picture in 0 of 12 cases, across widths 3-8 and fidelities 150-700px,
while returning answers that parse and carry fluent specific reasons. See
`docs/implementation-plans/2026-08-26-what-the-model-can-be-asked.md`.

So the work is split by what can actually be established:

- Arithmetic absorbs frames sharing an EXACT capture instant. Two devices on one
  moment are one moment seen twice; 558 of a dense month's 1468 candidates, at
  no model cost. Which twin ships is not an editorial question -- Thein's set
  test is "at a glance, one should not be mistaken for another", and two frames
  of one instant are not distinguishable at a glance.

- Everything else waits for a question with a referent outside the comparison.

The absorbing rule is EXACT instants and nothing wider. Two frames 7.6 seconds
apart, one place, one subject, were measured to be two different pictures of a
fast-moving event; a "within N seconds" rule merges them and the sequence is
gone. Arithmetic gets only the part it can prove.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.contact_sheets import build_contact_sheets
from immich_memories.analysis.duplicate_hashing import compute_thumbnail_hash, hamming_distance
from immich_memories.analysis.editorial_contracts import (
    DecisionProvenance,
    EditorialCandidate,
    PassTrace,
    TraceDecision,
)
from immich_memories.analysis.selection_source import PreparedEditorialSource
from immich_memories.analysis.strict_json import final_json_object
from immich_memories.analysis.visual_atlas import build_visual_atlas
from immich_memories.analysis.visual_request_planner import VisionRequestLimits

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_gateway import EditorialGateway

SELECTS_PASS_NAME = "pass-2-selects"  # noqa: S105 - public editorial pass identity
SELECTS_PASS_VERSION = "pass-2-selects-v1"  # noqa: S105 - editorial pass identity
PAIR_PROMPT_VERSION = "pair-prompt-v3"  # noqa: S105 - wire contract identity
PAIR_SCHEMA_VERSION = "pair-v3"  # noqa: S105 - wire contract identity

# The answer moved between 150px and 400px in 4 of 4 moments and stopped moving
# above it, so this pass states the fidelity its own question needs rather than
# inheriting a 120-tile page's compromise.
SELECTS_TILE_PX = 400

# Eisenhardt, on documentary selects: "you get selects that come down to 50
# percent -- 25 percent in this case, thank God -- of all the material", and
# "the big cuts happen later, at structure, not at the item filter". Only the
# floor raises `!!`: keeping too much is this pass working weakly, which the
# next passes can still fix, while cutting past the floor is unjustifiable here.
SELECTS_SURVIVAL_FLOOR = 0.25

# Perceptual distance is the SECOND VOTE, never the only one. Thein keeps what
# two independent passes both chose; one of those passes is free wherever the
# pixels already say what the second arrangement would.
#
# Measured on 653 real pairs: the model contradicted itself on 39, and only 4 of
# those were pixel-close -- its uncertainty lives on pixel-DISTANT pairs. At a
# corroboration distance of 10 the adaptive rule reproduced every one of 653
# decisions exactly while removing 30% of the calls. The first changed decision
# appears at 12.
#
# This is a CAP, not a tuned threshold. Each run derives its own from a
# calibration sample of its own library and may only lower it, so a library
# unlike the one this was measured on saves less rather than cutting more.
SELECTS_MAX_CORROBORATION = 10
SELECTS_CALIBRATION_PAIRS = 60
SELECTS_CORROBORATION_MARGIN = 2

_PAIR_SHAPE = json.dumps(
    {"schema_version": PAIR_SCHEMA_VERSION, "same": False, "reason": "what makes them one or two"},
    separators=(",", ":"),
)
# The question is the probe's, word for word. `same: false` is shown because
# false is the safe default -- a copied example merges nothing. More prose
# measurably makes this model worse, so none is added.
#
# The written reason is asked for even though the parser reads only `same`, and
# that is a QUALITY decision that reverses an earlier cost one. Dropping it
# halved the call, and a 30-pair sample said the verdict agreed 29 times in 30.
# Across the 650 pairs judged under both contracts it agrees 79% of the time,
# and the disagreement is one-directional: 126 pairs went from "same" to
# "different" and 10 the other way. Looked at, those pairs are the same picture
# -- one is a woman holding a newborn in the same chair in the same pose, twice.
# Writing the reason is not overhead on the answer; on this model it is part of
# how the answer is arrived at, and 30 pairs was too small a sample to see it.


_PAIR_PROMPT = (
    "Two numbered visuals. Are they two attempts at the same picture -- the same "
    "subject, framed the same way, moments apart -- or are they two different pictures? "
    "Return only one complete JSON object, using exactly these keys and no others:\n" + _PAIR_SHAPE
)


@dataclass
class _Corroboration:
    """How far apart two frames may be before the second arrangement is worth buying.

    Consulted per PAIR, not per moment: a single burst of eighty frames is one
    moment, and a check that only ran at moment boundaries would never finish
    calibrating on it.
    """

    observations: list[tuple[int, bool, bool]] = field(default_factory=list)
    distance: int = SELECTS_MAX_CORROBORATION
    calibrating: bool = True

    def settle(self) -> str | None:
        """Fix the distance once this library has said enough about itself."""
        if not self.calibrating or len(self.observations) < SELECTS_CALIBRATION_PAIRS:
            return None
        self.distance = _corroboration_from(self.observations)
        self.calibrating = False
        return (
            f"pass-2 corroboration distance {self.distance}, calibrated on "
            f"{len(self.observations)} pairs of this library"
        )


@dataclass(frozen=True)
class AbsorbedFrame:
    """One frame folded into another that shares its exact capture instant."""

    asset_id: str
    kept_asset_id: str
    reason: str


@dataclass(frozen=True)
class SelectsPassResult:
    """Chronological Pass 2 membership after provable repetition is removed."""

    survivors: tuple[EditorialCandidate, ...]
    absorbed: tuple[AbsorbedFrame, ...]
    trace: PassTrace
    warnings: tuple[str, ...] = ()


def run_selects(
    prepared: PreparedEditorialSource,
    admitted: Sequence[EditorialCandidate],
    *,
    requester: EditorialGateway | None = None,
    sheet_output_dir: Path | None = None,
    frame_cache_dir: Path | None = None,
    limits: VisionRequestLimits | None = None,
) -> SelectsPassResult:
    """Absorb repetition in two stages, and ask the model only what it can answer.

    `admitted` is what reached this pass -- Cull's survivors in the live flow --
    while the moment structure comes from the prepared source, so a frame Cull
    removed cannot absorb one it kept.

    Stage A is arithmetic: frames sharing an exact capture instant collapse to
    one. Stage B asks the model, for each pair of neighbours still standing,
    whether they are two attempts at the same picture -- the one comparison this
    model makes reliably. Without a `requester` only Stage A runs, which is the
    honest fail-open: no model, no model stage.

    Both stages absorb INSIDE a moment, never across the corpus. A moment is
    bounded by place as well as time, and two devices far apart at one instant
    are two people's parallel days -- measured on a real one, a racing circuit
    and a house 120km away within the same few minutes. Folding those together
    by clock alone invents a day neither of them had.
    """
    still_here = {candidate.asset_id for candidate in admitted}
    survivors, absorbed = _absorb_exact_instants(prepared, still_here)
    warnings: tuple[str, ...] = ()
    asked = requester is not None and sheet_output_dir is not None
    if requester is not None and sheet_output_dir is not None:
        survivors, folded, warnings = _absorb_the_same_picture(
            prepared,
            survivors,
            requester=requester,
            sheet_output_dir=sheet_output_dir,
            frame_cache_dir=frame_cache_dir,
            limits=limits or VisionRequestLimits(),
        )
        absorbed = [*absorbed, *folded]
    survivors.sort(key=lambda candidate: (candidate.taken_at, candidate.asset_id))
    chosen = tuple(survivors)
    folded_all = tuple(absorbed)
    warnings = (*warnings, *_survival_warning(admitted, chosen))
    prepared.trace.warnings.extend(
        warning for warning in warnings if warning not in prepared.trace.warnings
    )
    return SelectsPassResult(
        survivors=chosen,
        absorbed=folded_all,
        trace=_record_trace(prepared, admitted, chosen, folded_all, asked=asked),
        warnings=warnings,
    )


def _absorb_exact_instants(
    prepared: PreparedEditorialSource,
    still_here: set[str],
) -> tuple[list[EditorialCandidate], list[AbsorbedFrame]]:
    """Stage A. Two frames of one instant are not distinguishable at a glance."""
    survivors: list[EditorialCandidate] = []
    absorbed: list[AbsorbedFrame] = []
    for moment in prepared.moment_groups:
        by_instant: dict[datetime, list[EditorialCandidate]] = {}
        for candidate in moment.candidates:
            if candidate.asset_id in still_here:
                by_instant.setdefault(candidate.taken_at, []).append(candidate)
        for instant in sorted(by_instant):
            together = by_instant[instant]
            kept = min(together, key=_keeping_order)
            if not kept.favourite and any(frame.favourite for frame in together):
                kept = replace(kept, favourite=True)
            survivors.append(kept)
            absorbed.extend(
                AbsorbedFrame(
                    asset_id=candidate.asset_id,
                    kept_asset_id=kept.asset_id,
                    reason="shares an exact capture instant with the frame that was kept",
                )
                for candidate in together
                if candidate.asset_id != kept.asset_id
            )
    return survivors, absorbed


def _absorb_the_same_picture(
    prepared: PreparedEditorialSource,
    standing: list[EditorialCandidate],
    *,
    requester: EditorialGateway,
    sheet_output_dir: Path,
    frame_cache_dir: Path | None,
    limits: VisionRequestLimits,
) -> tuple[list[EditorialCandidate], list[AbsorbedFrame], tuple[str, ...]]:
    """Stage B. Chain neighbours the model calls one picture, then keep one of each run."""
    atlas = build_visual_atlas(prepared.visual_sources, frame_cache_dir=frame_cache_dir)
    alive = {candidate.asset_id for candidate in standing}
    survivors: list[EditorialCandidate] = []
    absorbed: list[AbsorbedFrame] = []
    warnings: list[str] = []
    # The first pairs buy both arrangements unconditionally, so this library says
    # for itself how far apart two frames can be before the model starts
    # contradicting itself. Nothing is carried over from another one.
    corroboration = _Corroboration()
    for moment in prepared.moment_groups:
        members = [candidate for candidate in moment.candidates if candidate.asset_id in alive]
        if not members:
            continue
        runs, moment_warnings = _runs_of_one_picture(
            moment.group_id,
            members,
            atlas=atlas,
            requester=requester,
            sheet_output_dir=sheet_output_dir,
            limits=limits,
            corroboration=corroboration,
        )
        warnings.extend(moment_warnings)
        for run in runs:
            kept, folded = _keep_from_run(run)
            survivors.extend(kept)
            absorbed.extend(folded)
    return survivors, absorbed, tuple(warnings)


def _runs_of_one_picture(
    group_id: str,
    members: list[EditorialCandidate],
    *,
    atlas: object,
    requester: EditorialGateway,
    sheet_output_dir: Path,
    limits: VisionRequestLimits,
    corroboration: _Corroboration,
) -> tuple[list[list[EditorialCandidate]], tuple[str, ...]]:
    """Ask each adjacent pair, and chain the agreeing ones into runs.

    The partition is never asked for directly -- that question measured at pair
    Jaccard 0.15. Chaining a symmetric two-tile question rebuilds it, which is
    also how the craft does it: Gilden marks on a linear sweep from frame 1 to
    36 and only then looks at what he marked.
    """
    runs: list[list[EditorialCandidate]] = [[members[0]]]
    warnings: list[str] = []
    for index in range(len(members) - 1):
        same, warning = _one_picture_in_both_orders(
            f"{group_id}-{index}",
            members[index],
            members[index + 1],
            atlas=atlas,
            requester=requester,
            sheet_output_dir=sheet_output_dir,
            limits=limits,
            corroboration=corroboration,
        )
        settled = corroboration.settle()
        if settled:
            warnings.append(settled)
        if warning:
            warnings.append(warning)
        if same:
            runs[-1].append(members[index + 1])
        else:
            runs.append([members[index + 1]])
    return runs, tuple(warnings)


def _one_picture_in_both_orders(
    scope_id: str,
    earlier: EditorialCandidate,
    later: EditorialCandidate,
    *,
    atlas: object,
    requester: EditorialGateway,
    sheet_output_dir: Path,
    limits: VisionRequestLimits,
    corroboration: _Corroboration,
) -> tuple[bool, str | None]:
    """Only two arrangements agreeing counts as one picture.

    Thein keeps "those that overlap" between two independent passes. His two are
    separated in time, to defeat the memory of shooting; these two are separated
    in order, to defeat the positional habit that ruined every other question
    tried. Disagreement means keep both -- a wrong keep is fixed by a later pass
    a person can check, a wrong cut is permanent and invisible.
    """
    forward = _ask_one_pair(
        scope_id, "ab", (earlier, later), atlas, requester, sheet_output_dir, limits
    )
    if forward is None:
        return False, f"!! Pass 2 unreadable pair answer, both kept: {scope_id}"
    if not forward:
        # `forward and backward` cannot become true now, so the second
        # arrangement changes no outcome. An exact saving, not an estimate:
        # measured, it removes 121 of 1312 calls on a real dense month.
        return False, None
    distance = _pixel_distance(atlas, earlier, later)
    if (
        not corroboration.calibrating
        and distance is not None
        and distance <= corroboration.distance
    ):
        # The pixels are the second vote here. Below the corroboration distance
        # the second arrangement only ever confirmed the first.
        return True, None
    backward = _ask_one_pair(
        scope_id, "ba", (later, earlier), atlas, requester, sheet_output_dir, limits
    )
    if distance is not None and backward is not None:
        corroboration.observations.append((distance, forward, backward))
    if backward is None:
        return False, f"!! Pass 2 unreadable pair answer, both kept: {scope_id}"
    return backward, None


def _ask_one_pair(
    scope_id: str,
    arrangement: str,
    pair: tuple[EditorialCandidate, EditorialCandidate],
    atlas: object,
    requester: EditorialGateway,
    sheet_output_dir: Path,
    limits: VisionRequestLimits,
) -> bool | None:
    """One arrangement of one pair. `None` means no usable answer, never "different"."""
    from immich_memories.analysis.editorial_gateway import VisualEditorialRequest

    tiles = tuple(atlas.tile_for(candidate.asset_id) for candidate in pair)  # type: ignore[attr-defined]
    if any(getattr(tile, "kind", "") == "unavailable" for tile in tiles):
        return None
    page = build_contact_sheets(
        tiles,
        scope_id=f"{scope_id}-{arrangement}",
        output_dir=sheet_output_dir,
        tile_px=SELECTS_TILE_PX,
    )[0]
    try:
        answer = requester.ask(
            VisualEditorialRequest(
                pass_name=SELECTS_PASS_NAME,
                pass_version=SELECTS_PASS_VERSION,
                prompt=_PAIR_PROMPT,
                prompt_version=PAIR_PROMPT_VERSION,
                schema_version=PAIR_SCHEMA_VERSION,
                pages=(page,),
                ordered_input_ids=tuple(candidate.asset_id for candidate in pair),
                # Pair sameness belongs to these two pictures forever. The
                # moment id is only where this memory happened to present them;
                # including it strands the verdict when another scope adds or
                # removes a neighbouring frame.
                ordered_group_ids=(),
                grounded_annotations=(),
                upstream_material=(),
                render_version=f"selects/pair/{arrangement}",
                limits=limits,
                image_detail="high",
            )
        )
    except Exception:  # noqa: BLE001 - one failed pair keeps both frames, it never cuts
        return None
    payload = final_json_object(answer.raw_text) or {}
    # Cache identity already stops a stale BANK being replayed against new
    # pixels. It cannot stop a live model answering the previous contract, and
    # answering the wrong question fluently is this model's documented failure.
    if payload.get("schema_version") != PAIR_SCHEMA_VERSION:
        return None
    same = payload.get("same")
    return same if isinstance(same, bool) else None


def _pixel_distance(
    atlas: object, earlier: EditorialCandidate, later: EditorialCandidate
) -> int | None:
    """Hamming distance between two atlas tiles, or None when either has no pixels."""
    try:
        tiles = [atlas.tile_for(c.asset_id) for c in (earlier, later)]  # type: ignore[attr-defined]
        blobs = [t.jpeg_bytes for t in tiles]
        if any(b is None for b in blobs):
            return None
        return hamming_distance(*(compute_thumbnail_hash(b) for b in blobs))
    except Exception:  # noqa: BLE001 - an unreadable tile just buys the second call
        return None


def _corroboration_from(observations: list[tuple[int, bool, bool]]) -> int:
    """The largest distance this library never contradicted itself below, capped.

    The sample can only LOWER the cap. A library unlike the one the cap was
    measured on therefore saves fewer calls; it never cuts more.
    """
    contradicted = [d for d, ab, ba in observations if ab != ba]
    if not contradicted:
        return SELECTS_MAX_CORROBORATION
    return max(0, min(SELECTS_MAX_CORROBORATION, min(contradicted) - SELECTS_CORROBORATION_MARGIN))


def _keep_from_run(
    run: list[EditorialCandidate],
) -> tuple[list[EditorialCandidate], list[AbsorbedFrame]]:
    """Every favourite in a run survives; a run with none keeps one by the stated rule.

    Selects marks rather than reduces to one per MOMENT -- a moment holding three
    runs keeps three. Inside a single run the craft's own set test applies:
    Thein asks that "at a glance, one should not be mistaken for another", so
    once the model has said these are one picture, which of them ships is not an
    editorial question. It is deliberately not asked, because the measurement
    says the answer would follow tile position.
    """
    starred = [candidate for candidate in run if candidate.favourite]
    kept = starred or [min(run, key=_keeping_order)]
    keeper = kept[0].asset_id
    folded = [
        AbsorbedFrame(
            asset_id=candidate.asset_id,
            kept_asset_id=keeper,
            reason="two arrangements agreed this is another attempt at the picture kept",
        )
        for candidate in run
        if candidate not in kept
    ]
    return kept, folded


def _survival_warning(
    admitted: Sequence[EditorialCandidate],
    survivors: tuple[EditorialCandidate, ...],
) -> tuple[str, ...]:
    """The share is measured against what REACHED this pass, never the whole corpus.

    Cull is mechanical removal of non-candidates, so a month carrying a lot of
    junk would otherwise credit Selects with rejections Cull made and hide a
    pass that had barely run.
    """
    if not admitted:
        return ()
    share = len(survivors) / len(admitted)
    if share >= SELECTS_SURVIVAL_FLOOR:
        return ()
    return (
        f"!! Pass 2 kept {share:.0%} of the {len(admitted)} frames that reached it, "
        f"under the 25% floor the craft expects of selects",
    )


def _record_trace(
    prepared: PreparedEditorialSource,
    admitted: Sequence[EditorialCandidate],
    survivors: tuple[EditorialCandidate, ...],
    absorbed: tuple[AbsorbedFrame, ...],
    *,
    asked: bool,
) -> PassTrace:
    """Every absorbed frame names the frame it was folded into, and why.

    A bare verdict cannot be re-examined when the question changes, and this one
    is a rule rather than a judgement, so the rule has to be legible from the
    trace alone.
    """
    prepared.trace.record_editorial_pass(
        PassTrace(
            name=SELECTS_PASS_NAME,
            input_ids=tuple(candidate.asset_id for candidate in admitted),
            kept_ids=tuple(candidate.asset_id for candidate in survivors),
            rejected=tuple(
                TraceDecision(item.asset_id, f"{item.reason}: {item.kept_asset_id}")
                for item in absorbed
            ),
            unresolved=(),
            duration_before=sum(item.shippable_duration for item in admitted),
            duration_after=sum(item.shippable_duration for item in survivors),
            provenance=DecisionProvenance(
                pass_name=SELECTS_PASS_NAME,
                pass_version=SELECTS_PASS_VERSION,
                schema_version=PAIR_SCHEMA_VERSION if asked else "none - this stage asks no model",
                model_identity=_model_asked(prepared) if asked else "",
                input_ids=tuple(candidate.asset_id for candidate in admitted),
                sheet_hashes=(),
                request_key="",
                cache_hit=False,
            ),
        )
    )
    return prepared.trace.editorial_passes[-1]


def _model_asked(prepared: PreparedEditorialSource) -> str:
    """The model identity this pass's own requests actually reached."""
    for request in reversed(prepared.trace.requests):
        if request.provenance.pass_name == SELECTS_PASS_NAME:
            return request.model
    return ""


def _keeping_order(candidate: EditorialCandidate) -> tuple[object, ...]:
    """The stated rule, in order: a favourite, then the larger file, then the ID.

    Task 7 asks for source evidence between the two, and `EditorialCandidate`
    does not carry any -- `SourceEvidence` exists in the contracts but reaches
    no candidate -- so claiming it here would describe a tie-break that never
    runs.

    Pixel count is not that evidence and is not an editorial judgement. It only
    settles which FILE to keep once the pictures have been found identical, and
    it reads metadata the source already fetched. It matters because a shot
    imported twice -- once full size, once downscaled by a shared album or a
    messaging app -- reaches the corpus as two assets sharing one exact instant,
    and Immich holds both because it only rejects byte-identical uploads.
    Measured on a real library: 533 of 2,847 exact-instant groups, where ID
    order kept 0.26x the pixels of the best available at the median.

    It sorts AHEAD of the star because a star belongs to the picture rather than
    to the file it was set on, and the two can be set on different assets: a
    shared album holds no originals, so it is both where stars get set and where
    the small copies come from. `_absorb_exact_instants` moves the star onto
    whichever file is kept, so nothing is lost by letting pixels decide first.
    The star stays in the order only to settle files of equal size.

    Unknown dimensions are zero, so a file of known size is preferred over one
    of unknown size, and two unknowns fall through to the star and the ID
    exactly as before.

    Written down rather than reasoned about, because this is deliberately not an
    editorial judgement. The ID last is only there to make the answer the same
    on every run.
    """
    source = candidate.source
    return (-(source.width * source.height), not candidate.favourite, candidate.asset_id)
