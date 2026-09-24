"""The promises a finished cut keeps, checked once, after every pass.

Each pass that edits a cut keeps its own promise when it runs: the family seat gives a close
relative a shot, the era floor gives each year of a person's film one, the motion rule lets a
moving Live Photo play. A later pass can undo any of them without knowing it did (#1252,
#1253, #1254). This reads the finished cut against every promise and names the pass that last
touched what broke. It changes nothing: it is a check, not a pass.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from itertools import chain
from pathlib import Path
from typing import Any

from immich_memories.analysis import editorial_shareability as _share
from immich_memories.analysis.editorial_clip_frames import clips_miss_subject
from immich_memories.analysis.editorial_family_seat import (
    FamilySeatPolicy,
    film_close_family,
    film_refusal,
)
from immich_memories.analysis.editorial_rule_banked_facts import withheld_by_bank
from immich_memories.analysis.editorial_structure_budget import RESIDUAL_MIN

logger = logging.getLogger(__name__)

CUT_INVARIANTS_VERSION = "cut-invariants-v1"
RECORD_NAME = "cut-invariants"
DRAFT = "draft"


@dataclass(frozen=True)
class Violation:
    """One broken promise: which, about what, and the last pass known to have touched it."""

    invariant: str
    subject: str
    detail: str
    last_pass: str
    person: str = ""


def _nothing(_asset: str) -> None:
    return None


def _always(_asset: str) -> bool:
    return True


def _never(_asset: str) -> bool:
    return False


def _nobody(_asset: str) -> Mapping[str, str]:
    return {}


@dataclass(frozen=True)
class FinishedCut:
    """The finished cut and what the passes that built it knew.

    `units` are the film's playable units by asset id. `removed_by` and `added_by` name the
    pass that took a picture out of the cut or put one in after the draft. `verdict_of` is the
    audience gate's recorded verdict, never a new question. `showable` is the film's own
    carrier refusal (carrier rules, banked refusals, a shared film's exposure hold).
    `era_of(taken)` is the partition a film gives a voice to, None for a film that promises
    none; `era_pictures` are each partition's showable pictures in stories inside it.
    `live_clip_of` maps a Live Photo still to its clip, `residuals` its measured motion.
    """

    carriers: Sequence[Mapping[str, Any]]
    units: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    drafted: frozenset[str] = frozenset()
    removed_by: Mapping[str, str] = field(default_factory=dict)
    added_by: Mapping[str, str] = field(default_factory=dict)
    audience: str = "family"
    verdict_of: Callable[[str], str | None] = _nothing
    refused_by_rule: Mapping[str, str] = field(default_factory=dict)
    never_auto: frozenset[str] = frozenset()
    owner_required: frozenset[str] = frozenset()
    showable: Callable[[str], bool] = _always
    scope: Sequence[str] = ()
    close_family_of: Callable[[str], Mapping[str, str]] = _nobody
    seat_policy: FamilySeatPolicy = FamilySeatPolicy()
    seat_gave_up: frozenset[str] = frozenset()
    era_of: Callable[[str], str | None] | None = None
    era_pictures: Mapping[str, Sequence[str]] = field(default_factory=dict)
    standing_refused: frozenset[str] = frozenset()
    live_motion: bool = True
    live_clip_of: Mapping[str, str] = field(default_factory=dict)
    residuals: Mapping[str, float] = field(default_factory=dict)
    clip_misses_subject: Callable[[str], bool] = _never

    def origin(self, asset: str) -> str:
        """The pass that put a picture in the cut, as far as the record knows."""
        if asset in self.added_by:
            return self.added_by[asset]
        return DRAFT if asset in self.drafted else "a pass after the draft"

    def shows(self) -> set[str]:
        return {a for c in self.carriers for a in (c["asset_id"], *(c.get("members") or ()))}

    def may_carry(self, asset: str) -> bool:
        verdict = self.verdict_of(asset)
        return self.showable(asset) and (verdict is None or _share.allowed(verdict, self.audience))


def cut_violations(cut: FinishedCut) -> list[Violation]:
    """Every promise the finished cut breaks; an empty list is every promise kept."""
    return [
        *_family_without_a_shot(cut),
        *_favourites_passed_over(cut),
        *_eras_without_a_shot(cut),
        *_live_motion_left_still(cut),
        *_refused_pictures_kept(cut),
        *_out_of_order(cut),
    ]


def _last_removal(cut: FinishedCut, assets: Sequence[str]) -> str | None:
    wanted = set(assets)
    return next((p for a, p in reversed(list(cut.removed_by.items())) if a in wanted), None)


def _family_without_a_shot(cut: FinishedCut) -> list[Violation]:
    everyone = {a: cut.close_family_of(a) for a in dict.fromkeys(cut.scope)}
    on = {a: people for a, people in everyone.items() if cut.showable(a)}
    pictures = Counter(chain.from_iterable(on.values()))
    shots = Counter(chain.from_iterable(cut.close_family_of(c["asset_id"]) for c in cut.carriers))
    relation = {name: rel for people in everyone.values() for name, rel in people.items()}
    out = []
    for name, count in pictures.most_common():
        owed = cut.seat_policy.owed(count, len(on))
        if not owed or shots[name] or relation[name] in cut.seat_gave_up:
            continue
        theirs = [a for a, people in everyone.items() if name in people]
        out.append(
            Violation(
                "family_seat",
                relation[name],
                f"on {count} showable pictures of the period and in no shot",
                _last_removal(cut, theirs) or "draft and family seat",
                person=name,
            )
        )
    return out


def _favourites_passed_over(cut: FinishedCut) -> list[Violation]:
    by_moment: dict[Any, list[Mapping[str, Any]]] = {}
    for unit in cut.units.values():
        by_moment.setdefault(unit.get("moment"), []).append(unit)
    shown = cut.shows()
    out = []
    for c in cut.carriers:
        if c.get("favourite") or c["asset_id"] in cut.owner_required or c.get("moment") is None:
            continue
        seated = set(cut.close_family_of(c["asset_id"])) if c.get("family_seat") else set()
        waiting = [
            u["asset_id"]
            for u in by_moment.get(c["moment"], ())
            if u.get("favourite")
            and not {u["asset_id"], *(u.get("members") or ())} & shown
            and cut.may_carry(u["asset_id"])
            # A family seat carries a person: only a favourite that shows them could have won.
            and (not seated or seated & set(cut.close_family_of(u["asset_id"])))
        ]
        if waiting:
            out.append(
                Violation(
                    "favourite_wins_its_moment",
                    c["asset_id"],
                    f"moment {c['moment']} ships a non-favourite; favourite {waiting[0]} is out",
                    _last_removal(cut, waiting) or cut.origin(c["asset_id"]),
                )
            )
    return out


def _eras_without_a_shot(cut: FinishedCut) -> list[Violation]:
    if cut.era_of is None:
        return []
    voiced = {cut.era_of(c["taken"]) for c in cut.carriers}
    out = []
    for era, assets in cut.era_pictures.items():
        if era in voiced or not assets:
            continue
        lost = _last_removal(cut, assets)
        if lost is None and cut.standing_refused & set(assets):
            continue
        out.append(
            Violation(
                "every_year_has_a_voice",
                era,
                f"{len(assets)} showable pictures in its own stories and no shot",
                lost or "allocation",
            )
        )
    return out


def _live_motion_left_still(cut: FinishedCut) -> list[Violation]:
    if not cut.live_motion:
        return []
    out = []
    for c in cut.carriers:
        members = [c["asset_id"], *(c.get("members") or ())]
        clips = list(c.get("video_ids") or ()) or [
            cut.live_clip_of[m] for m in dict.fromkeys(members) if m in cut.live_clip_of
        ]
        # A clip that may not play (a join shorter than its still, or one missing its subject)
        # is a still by the motion rule itself.
        if (
            c.get("kind") in ("live-motion", "video")
            or not clips
            or c.get("motion_candidate") is False
        ):
            continue
        measured = [cut.residuals[m] for m in members if m in cut.residuals]
        residual = (
            c.get("residual") if c.get("residual") is not None else max(measured, default=None)
        )
        if residual is None or residual < RESIDUAL_MIN or any(map(cut.clip_misses_subject, clips)):
            continue
        out.append(
            Violation(
                "live_motion_plays",
                c["asset_id"],
                f"residual {residual:.2f} with its subject in frame, shipped as {c.get('kind')}",
                "motion resolution",
            )
        )
    return out


def _refused_pictures_kept(cut: FinishedCut) -> list[Violation]:
    out = []
    for c in cut.carriers:
        asset = c["asset_id"]
        ids = [asset, *(c.get("members") or ())]
        rule = next((cut.refused_by_rule[a] for a in ids if a in cut.refused_by_rule), None)
        verdict = cut.verdict_of(asset)
        if asset in cut.owner_required:
            rule = None
        if rule is None and cut.never_auto & set(ids):
            rule = "never_auto"
        if rule is not None:
            detail = f"refused as a carrier ({rule})"
        elif verdict is None:
            detail = "never judged by the audience gate"
        elif not _share.allowed(verdict, cut.audience):
            detail = f"held by the audience gate ({verdict} for a {cut.audience} film)"
        else:
            continue
        out.append(Violation("nothing_refused_ships", asset, detail, cut.origin(asset)))
    return out


def _out_of_order(cut: FinishedCut) -> list[Violation]:
    return [
        Violation(
            "chronological",
            after["asset_id"],
            f"taken {after['taken']}, placed after {before['taken']}",
            cut.origin(after["asset_id"]),
        )
        for before, after in zip(cut.carriers, cut.carriers[1:], strict=False)
        if datetime.fromisoformat(after["taken"]) < datetime.fromisoformat(before["taken"])
    ]


def report_violations(violations: Sequence[Violation], record) -> None:
    """One log line per broken promise, and the run's private record of all of them."""
    # A person's subject is their relation; their name stays in the private record.
    for v in violations:
        logger.warning(
            "Cut invariant %s broken: %s: %s (last pass: %s)",
            v.invariant,
            v.subject,
            v.detail,
            v.last_pass,
        )
    record(
        RECORD_NAME,
        {"version": CUT_INVARIANTS_VERSION, "violations": [asdict(v) for v in violations]},
    )


def broken_promises(attempt_dir: Path | None) -> int | None:
    """How many promises a run's finished cut broke; None for a run that recorded no check."""
    if attempt_dir is None:
        return None
    path = Path(attempt_dir) / "derived-decisions" / f"{RECORD_NAME}.private.json"
    try:
        return len(json.loads(path.read_text())["violations"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def check_finished_cut(source, selection, material, run, gate, banked, share_log, record):
    """Check the finished cut against every promise and report what it breaks.

    Called once, after the last pass. It reads what the passes recorded and asks nothing: the
    audience gate's verdicts are the ones it already gave.
    """
    violations = cut_violations(
        _finished_cut(source, selection, material, run, gate, banked, share_log)
    )
    report_violations(violations, record)
    return violations


def _finished_cut(source, selection, material, run, gate, banked, share_log) -> FinishedCut:
    units = {u["asset_id"]: u for rows in material.units.values() for u in rows}
    refused = film_refusal(
        source,
        material.document_sources,
        withheld_by_bank(banked, favourite=lambda a: bool(units.get(a, {}).get("favourite"))),
    )
    close_family = film_close_family(source)
    people = source.config.editorial.people
    decisions = source.artifact_dir / "derived-decisions"
    live = {
        a: asset.live_photo_video_id
        for a, asset in source.assets.items()
        if asset.live_photo_video_id and asset.type.value.lower() != "video"
    }
    cut = FinishedCut(
        carriers=run.carriers,
        units=units,
        drafted=frozenset(c["asset_id"] for c in selection.carriers),
        removed_by=_removed_by(run, share_log),
        added_by=_added_by(run, share_log, source.owner_required_asset_ids),
        audience=source.audience,
        verdict_of=lambda a: (gate.verdicts.get(a) or {}).get("verdict"),
        refused_by_rule=material.document_sources,
        never_auto=_share.never_auto_ids(source.shareability_flags),
        owner_required=frozenset(source.owner_required_asset_ids),
        showable=lambda a: not refused(a),
        scope=list(chain.from_iterable(source.moment_asset_ids.values())),
        close_family_of=lambda a: close_family(selection.lines.get(a, "")),
        seat_policy=FamilySeatPolicy(people.seat_min_pictures, people.seat_min_share),
        seat_gave_up=_seat_gave_up(decisions),
        live_motion=source.allow_live_motion,
        live_clip_of=live,
        residuals={
            a: float(row["residual"])
            for a, row in source.motion_residuals.items()
            if row.get("residual") is not None
        },
        clip_misses_subject=lambda clip: clips_miss_subject(source.clip_frames, [clip]),
    )
    if not source.intent.voice_per_partition:
        return cut
    era_of = _era_of(source.intent)
    return replace(
        cut,
        era_of=era_of,
        era_pictures=_era_pictures(selection, units, era_of, cut.may_carry),
        standing_refused=_standing_refused(decisions),
    )


def _removed_by(run, share_log: Mapping[str, Any]) -> dict[str, str]:
    """Each picture a pass took out of the cut, in the order the passes ran."""
    removed = {
        str(row.get("asset_id")): "audience-gate"
        for row in (*share_log.get("tightened", ()), *share_log.get("dropped", ()))
    }
    for row in run.cut_carriers:
        removed.pop(row["asset_id"], None)
        removed[row["asset_id"]] = row.get("review_stage") or "a pass after the draft"
    return removed


def _added_by(run, share_log: Mapping[str, Any], owner_required: Sequence[str]) -> dict[str, str]:
    """Each picture a pass put in the cut after the draft, where the pass recorded it."""
    added = {str(row.get("to")): "audience-gate" for row in share_log.get("substituted", ())}
    added |= {
        row["replacement"]: "final-duplicates"
        for row in run.final_duplicates.get("removals", ())
        if row.get("replacement")
    }
    added |= {c["asset_id"]: "family-seat" for c in run.carriers if c.get("family_seat")}
    return added | dict.fromkeys(owner_required, "owner-required")


def _read_decision(decisions: Path, name: str) -> Mapping[str, Any]:
    try:
        return json.loads((decisions / f"{name}.private.json").read_text())
    except (OSError, ValueError):
        return {}


def _seat_gave_up(decisions: Path) -> frozenset[str]:
    """The relations the last family seat recorded it could not place, and why."""
    seat = _read_decision(decisions, "family-seat-after-review") or _read_decision(
        decisions, "family-seat"
    )
    return frozenset(row["relation"] for row in seat.get("seats", ()) if row.get("placed") is None)


def _standing_refused(decisions: Path) -> frozenset[str]:
    """The pictures the draft recorded as failing the standing or context bar."""
    record = _read_decision(decisions, "story-selection")
    return frozenset(
        [
            *record.get("failed_standing", ()),
            *(row["asset_id"] for row in record.get("context_rejected", ())),
        ]
    )


def _era_of(intent) -> Callable[[str], str | None]:
    def era_of(taken: str) -> str | None:
        part = intent.partition_for(datetime.fromisoformat(taken).date())
        return part.key if part is not None else None

    return era_of


def _era_pictures(selection, units, era_of, may_carry) -> dict[str, list[str]]:
    """Each era's pictures that could carry it, from the stories that lie inside that era.

    A story spanning two eras floors neither, as in the allocation (`PartitionedSlots._eras`).
    """
    by_moment: dict[Any, list[Mapping[str, Any]]] = {}
    for unit in units.values():
        by_moment.setdefault(unit.get("moment"), []).append(unit)
    moments_of = {e.key: e.moments for e in selection.story.episodes}
    pictures: dict[str, list[str]] = {}
    for story in selection.story.stories:
        if story.get("weight") == "none":
            continue
        rows = [
            u
            for key in story.get("episodes", ())
            for m in moments_of.get(key, ())
            for u in by_moment.get(m, ())
        ]
        eras = {era_of(u["taken"]) for u in rows}
        if len(eras) != 1 or (era := eras.pop()) is None:
            continue
        pictures.setdefault(era, []).extend(u["asset_id"] for u in rows if may_carry(u["asset_id"]))
    return pictures
