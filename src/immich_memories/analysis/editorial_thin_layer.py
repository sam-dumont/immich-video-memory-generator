"""Polish a rules draft with the model instead of re-planning the film around it.

A library that has been catalogued already holds an account of the period and a neutral list of
its stories, and the no-model reader can build a whole film out of them. What that film has not
had is a reader looking at the finished cut and saying which of its shots add nothing. This
layer asks exactly that, once, and changes only what the answer and the gates leave open.

It is not a planner. There are no caps, no reserve, no depth, no re-allocation and no coverage
floor here: the gated draft is the film. A gate can refuse a picture; a vote can only replace
one after its candidate passes every check.
"""

from __future__ import annotations

import calendar
import logging
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import chain
from operator import itemgetter
from pathlib import Path
from typing import Any

from immich_memories.analysis.editorial_block_votes import (
    BLOCK_SIZE,
    balanced_groups,
    load_vote_bank,
    save_vote_bank,
)
from immich_memories.analysis.editorial_shot_kinds import KindOf, kind_mix
from immich_memories.analysis.editorial_story_replies import close_family_on
from immich_memories.analysis.editorial_thin_catalogue import (
    BankedCatalogue,
    ThinCatalogue,
    banked_catalogue,
)
from immich_memories.analysis.editorial_thin_gates import GateRefusal, ThinGates
from immich_memories.analysis.editorial_thin_refill import (
    REMOVALS,
    ThinRefill,
    ThinSlot,
    newcomer_slots,
    openable_slots,
    plan_slots,
)
from immich_memories.analysis.editorial_thin_short import (
    ShortReads,
    episodes_to_read,
    seats_for,
    short_budget,
    with_records,
)
from immich_memories.analysis.editorial_thin_vote import (
    CloseFamily,
    classify_fit,
    is_protected,
    keep_every_voice,
    sole_era_shots,
    sole_family_shots,
    sole_texture_shots,
    vote_thesis_fit,
)

logger = logging.getLogger(__name__)

THIN_VERSION = "thin-polish-v2"


class PeriodUnread(RuntimeError):
    """The account of the film's period could not be read, twice; the polish cannot run."""


def catalogued_period(ranges: Sequence[Any]) -> str:
    """The library node a film of these dates could hold an account of, or nothing.

    Cataloguing writes its account per calendar month and per year. A film whose dates are one
    of those asks for it by name. Any other single window, a season, a trip, a fortnight or a
    person film from a birth date to today, is its own node ("2024-03-01..2024-05-31"), told
    from one account per year it touches. A film over several windows (the same day across
    years) has no single account to read, and the model plans it whole.
    """
    if len(ranges) != 1:
        return ""
    first, last = ranges[0].start, ranges[0].end
    whole_months = first.day == 1 and last.day == calendar.monthrange(last.year, last.month)[1]
    if first.year == last.year and whole_months and first.month == last.month:
        return f"{first.year:04d}-{first.month:02d}"
    if first.year == last.year and whole_months and (first.month, last.month) == (1, 12):
        return f"{first.year:04d}"
    return f"{_day(first)}..{_day(last)}"


def _why_unpolished(catalogue: ThinCatalogue | None, unread: str) -> str:
    if unread:
        return unread
    return "no catalogued account of this period" if catalogue is None else "no draft"


def _unpolished(carriers, reason: str, record) -> list[dict[str, Any]]:
    """The rules draft as it stands, said once in the log and once in the record."""
    logger.warning("The model polish did not run (%s); the film is the rules draft", reason)
    record("thin-polish", {"version": THIN_VERSION, "ran": False, "reason": reason})
    return list(carriers)


def _day(when: Any) -> str:
    return f"{when.year:04d}-{when.month:02d}-{when.day:02d}"


@dataclass(frozen=True)
class ThinPolish:
    """The model polish of a rules draft, over an account of the period it is a cut of."""

    bank_dir: Path
    # The account of the period, and what its readings named as worth a record, given the
    # shots this draft chose, by story. Both arrive together, because both come from the same
    # readings and neither is worth paying for before the draft exists.
    read_period: Callable[[Mapping[str, Sequence[str]]], tuple[str, Mapping[str, str]]] = (
        lambda _stories: ("", {})
    )
    # What a film left short may still read; None on a route that reads nothing more.
    short: ShortReads | None = None

    def catalogue_of(
        self,
        story,
        moment_assets: Mapping[str, Sequence[str]],
        records: Mapping[str, str] | None = None,
        *,
        drafted: Sequence[Mapping[str, Any]],
    ) -> BankedCatalogue | None:
        """The catalogued period behind this run's own story reading, or None for no account.

        Every story of the period is listed, because the layer needs to know what the period
        holds. Only the draft's own shots are READ, by the episode each one sits in: the
        account is the thesis of THIS cut. A story is not the unit here, because one story can
        hold most of a month: on one measured month the longest story spanned 10 episodes to
        give the film 5 shots, and reading every story the draft touched read 44 of the
        month's 50 episodes. The rest of the period is filled in later, by `prepare
        --overviews` or by another cut.
        """
        asset_ids_of = _story_asset_ids(story.episodes, story.stories, moment_assets)
        account, banked_records = self.read_period(_drafted_shots(asset_ids_of, drafted))
        return banked_catalogue(
            account=account,
            story_rows=story.stories,
            hints=story.audit.get("hints") or {},
            asset_ids_of=asset_ids_of,
            records=banked_records if records is None else records,
        )

    def polish(
        self,
        carriers: Sequence[dict[str, Any]],
        *,
        judge,
        gates: ThinGates,
        catalogue: ThinCatalogue | None,
        contract: str,
        unread: str = "",
        line_of: Callable[[str], str],
        record: Callable[[str, Mapping[str, Any]], None],
        candidates_of: Callable[[str], Sequence[Mapping[str, Any]]] = lambda _key: (),
        content_cap: float = 0.0,
        protected: Sequence[str] = (),
        subject: str = "",
        close_family: CloseFamily = close_family_on,
        era_of: Callable[[str], str | None] | None = None,
        kind_of: KindOf | None = None,
        vouched: Callable[[Mapping[str, Any]], bool] = lambda _row: True,
    ) -> list[dict[str, Any]]:
        """The cut this period's gates, one closed vote and one refill leave standing.

        A period the library has no account of is not polished at all: the film is the rules
        draft, and one log line and the record say why (`unread` when the read itself failed).
        `subject` is who the film is about, which the vote reads beside the account, and
        `close_family` who on a line is close family in this film: the owner's, and in a film
        about people the subject's own as well. `era_of` maps a capture time to the partition a
        film promises a voice to (a year of a lifetime film), None when it promises none.
        `kind_of` says whether a shot is a portrait or texture, for the film's variety: the vote
        keeps each story's only texture shot, and a refill leads with the kind its story lacks,
        among the rows the library `vouched` for.
        """
        if catalogue is None or not carriers:
            return _unpolished(carriers, _why_unpolished(catalogue, unread), record)
        carriers = _with_records(carriers, catalogue)
        first_call = len(judge.calls)
        tier_of = {story.key: story.tier for story in catalogue.stories}
        admitted, refused = gates.admit(carriers, tier_of=tier_of, protected=protected)
        fit = _FitQuestion(
            judge, catalogue, contract, line_of, subject, close_family, era_of, kind_of
        )
        kept, verdicts, rounds = self._voted(admitted, fit)
        slots = plan_slots(
            kept,
            catalogue=catalogue,
            verdicts=verdicts,
            refused=refused,
            candidates_of=lambda key: _with_records(candidates_of(key), catalogue),
            seen={c["asset_id"] for c in carriers},
            content_cap=content_cap,
            removed=_removed(carriers, kept),
            kind_of=kind_of,
            vouched=vouched,
        )
        refill = ThinRefill(
            judge=judge,
            gates=gates,
            contract=contract,
            line_of=line_of,
            record=record,
            tier_of=tier_of,
            title_of={story.key: story.title for story in catalogue.stories},
            content_cap=content_cap,
        )
        filled, outcomes = refill.fill(kept, slots)
        partition = balanced_groups([c["asset_id"] for c in admitted])
        final, revoked = self._checked(filled, kept, outcomes, partition, fit)
        final, outcomes, late, retried = self._again(
            final, outcomes, revoked, refill, partition, fit
        )
        revoked |= late
        seen = {c["asset_id"] for c in carriers} | {c["asset_id"] for c in final}
        topped, short_slots, short = self._short_reads(
            final,
            catalogue=catalogue,
            offers=lambda key: [dict(u) for u in candidates_of(key) if u["asset_id"] not in seen],
            refill=refill,
            content_cap=content_cap,
            fit=fit,
        )
        if short_slots:
            final, late = self._checked(topped, final, short_slots, partition, fit)
            revoked |= late
        record(
            "thin-polish",
            {
                "version": THIN_VERSION,
                "ran": True,
                "stories": len(catalogue.stories),
                "draft_shots": len(carriers),
                "refused_by_the_gates": [_refusal_row(row) for row in refused],
                "voted": len(verdicts),
                "rounds": rounds,
                "verdicts": verdicts,
                **_vote_outcomes(verdicts, final),
                "slots": [
                    slot.row(revoked=slot.filled_by in revoked)
                    for slot in (*outcomes, *short_slots)
                ],
                "short": short,
                "revoked_by_the_fit_check": sorted(revoked),
                "shots": len(final),
                "shot_kinds": _shot_kinds(carriers, final, kind_of),
                "planned_seconds": round(sum(c["seconds"] for c in final), 3),
                "content_cap": content_cap,
                "calls": _spent(
                    len(judge.calls) - first_call,
                    thin_budget(len(carriers), len(outcomes) + retried)
                    + short_budget(short.get("episodes_read", 0), len(short_slots)),
                ),
            },
        )
        return final

    def _short_reads(
        self,
        cut: list[dict[str, Any]],
        *,
        catalogue: ThinCatalogue,
        offers: Callable[[str], list[dict[str, Any]]],
        refill: ThinRefill,
        content_cap: float,
        fit: _FitQuestion,
    ) -> tuple[list[dict[str, Any]], list[ThinSlot], dict[str, Any]]:
        """The cut after a short film read a few unread episodes and seated what they recorded.

        Only a story whose reading recorded something is offered a seat, so the film stays short
        when nothing did. See `editorial_thin_short`.
        """
        missing = content_cap - sum(row["seconds"] for row in cut)
        seats = seats_for(missing)
        if self.short is None or not seats:
            return cut, [], {}
        episodes = episodes_to_read(
            cut,
            catalogue=catalogue,
            offers=offers,
            reads=self.short,
            line_of=fit.line_of,
            limit=2 * seats,
            close_family=fit.close_family,
        )
        wanted = list(chain.from_iterable(episodes))
        records = dict(self.short.records(wanted)) if wanted else {}
        richer = with_records(catalogue, records)
        slots = newcomer_slots(
            cut,
            richer,
            lambda key: _with_records(offers(key), richer),
            min(seats, openable_slots(content_cap, content_cap - missing)),
        )
        filled, outcomes = refill.fill(cut, slots)
        return (
            filled,
            outcomes,
            {
                "short_by": round(missing, 3),
                "episodes_read": len(episodes),
                "records": len(records),
                "seats": len(slots),
            },
        )

    def _again(
        self,
        cut: list[dict[str, Any]],
        outcomes: Sequence[ThinSlot],
        revoked: set[str],
        refill: ThinRefill,
        partition: Sequence[Sequence[str]],
        fit: _FitQuestion,
    ) -> tuple[list[dict[str, Any]], list[ThinSlot], set[str], int]:
        """A removal's seat whose newcomer the re-check revoked picks once more from what is
        left of its page, and that pick is re-checked the same way. Returns the cut, every
        seat's outcome, what the second check revoked, and how many seats picked again."""
        again = [
            replace(
                slot,
                filled_by="",
                outcome="",
                page=tuple(unit for unit in slot.page if unit["asset_id"] not in revoked),
                fallback=tuple(u for u in slot.fallback if u["asset_id"] not in revoked),
            )
            for slot in outcomes
            if slot.kind in REMOVALS and slot.filled_by in revoked
        ]
        if not again:
            return cut, list(outcomes), set(), 0
        filled, refilled = refill.fill(cut, again)
        final, late = self._checked(filled, cut, refilled, partition, fit)
        stayed = [s for s in outcomes if not (s.kind in REMOVALS and s.filled_by in revoked)]
        return final, [*stayed, *refilled], late, len(again)

    def _checked(
        self,
        filled: list[dict[str, Any]],
        before: Sequence[Mapping[str, Any]],
        outcomes: Sequence[ThinSlot],
        partition: Sequence[Sequence[str]],
        fit: _FitQuestion,
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Every newcomer, judged again in the company of the block it joined.

        The first vote's blocks are kept: a swap takes its shot's place and an appended newcomer
        joins the block its capture time falls in, so a few newcomers re-ask a few blocks and
        not the whole cut. Only the newcomers' verdicts are read. Banked by the block, never by
        the row, so a second run replays it, and a newcomer is never the only row left to ask.
        A revoked newcomer does not take the shot it replaced with it: that shot comes back and
        the film is where it started.
        """
        held = {row["asset_id"] for row in before}
        fresh = [row for row in filled if row["asset_id"] not in held]
        if not fresh:
            return filled, set()
        newcomers = {row["asset_id"] for row in fresh}
        by_asset = {row["asset_id"]: row for row in filled}
        family, eras, textures = fit.held(filled)
        votes: dict[str, tuple[int, str]] = {}
        for group in rejoined_blocks(partition, filled, newcomers, outcomes):
            block = [by_asset[asset] for asset in group]
            block_votes, _rounds = self._ask(block, fit, family | eras | textures, moving=newcomers)
            votes.update(block_votes)
        verdicts = classify_fit(fresh, votes, family, eras, textures)
        revoked = {row["asset_id"] for row in fresh if verdicts[row["asset_id"]]["state"] == "bad"}
        if not revoked:
            return filled, set()
        snapshot = {row["asset_id"]: dict(row) for row in before}
        restored = [
            snapshot[slot.replacing]
            for slot in outcomes
            if slot.filled_by in revoked
            and slot.replacing
            and slot.replacing not in {row["asset_id"] for row in filled}
        ]
        kept = [row for row in filled if row["asset_id"] not in revoked]
        return sorted([*kept, *restored], key=itemgetter("taken", "asset_id")), revoked

    def _voted(
        self, carriers: list[dict[str, Any]], fit: _FitQuestion
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict]]:
        family, eras, textures = fit.held(carriers)
        votes, rounds = self._ask(carriers, fit, family | eras | textures)
        verdicts = keep_every_voice(
            carriers, classify_fit(carriers, votes, family, eras, textures), fit.era_of
        )
        # A vote proposes a replacement; only a candidate that passes can take the seat.
        return carriers, verdicts, rounds

    def _bank(self) -> dict:
        """Both votes read and write one file, so neither is paid for twice."""
        return load_vote_bank(self._bank_path())

    def _bank_path(self) -> Path:
        return self.bank_dir / "thesis-fit.private.json"

    def _ask(self, carriers, fit: _FitQuestion, held: Mapping[str, str], moving=None):
        """The vote over these shots; with `moving`, only those shots' answers are read.

        A shot the vote may not move (a star, a record, a close family member's or a year's only
        shot) is not asked about in a second order, and a block of nothing else is not asked at
        all.
        """
        bank = self._bank()
        story_of = {
            asset: story.key for story in fit.catalogue.stories for asset in story.asset_ids
        }.get
        return vote_thesis_fit(
            fit.judge,
            pictures=[c["asset_id"] for c in carriers],
            protected=[
                c["asset_id"]
                for c in carriers
                if is_protected(c)
                or c["asset_id"] in held
                or (moving is not None and c["asset_id"] not in moving)
            ],
            line_of=fit.line_of,
            thesis=fit.catalogue.thesis,
            contract=fit.contract,
            subject=fit.subject,
            close_family=fit.close_family,
            story_of=lambda asset: story_of(asset, "") or "",
            bank=bank,
            save=lambda: save_vote_bank(self._bank_path(), bank),
        )


@dataclass(frozen=True)
class _FitQuestion:
    """What every thesis-fit question of one polish reads: the same judge, account and lines."""

    judge: Any
    catalogue: ThinCatalogue
    contract: str
    line_of: Callable[[str], str]
    subject: str
    close_family: CloseFamily
    era_of: Callable[[str], str | None] | None = None
    kind_of: KindOf | None = None

    def held(self, cut: Sequence[Mapping[str, Any]]) -> tuple[dict, dict, dict]:
        """What the vote may not move in this cut: family's, partitions' and texture's only."""
        return (
            sole_family_shots(cut, self.line_of, self.close_family),
            sole_era_shots(cut, self.era_of),
            sole_texture_shots(cut, self.kind_of),
        )


def _vote_outcomes(verdicts, final) -> dict[str, list[str]]:
    kept = {row["asset_id"] for row in final}
    return {
        "removed_by_the_vote": [
            asset
            for asset, verdict in verdicts.items()
            if verdict["state"] == "bad" and asset not in kept
        ],
        "retained_without_replacement": [
            asset
            for asset, verdict in verdicts.items()
            if verdict["state"] in {"bad", "weak"} and asset in kept
        ],
        "held_by_the_owner": [asset for asset, verdict in verdicts.items() if verdict["held_by"]],
    }


def _shot_kinds(draft, final, kind_of: KindOf | None) -> dict[str, dict[str, int]]:
    """The portrait and texture mix of the draft and of the polished cut."""
    if kind_of is None:
        return {"draft": {}, "polished": {}}
    return {
        "draft": kind_mix((row["asset_id"] for row in draft), kind_of),
        "polished": kind_mix((row["asset_id"] for row in final), kind_of),
    }


def thin_budget(draft: int, seats: int) -> int:
    """The calls a polish may spend: four questions per twelve draft shots (the fit vote and the
    audience question, each in its two orders: one look at the draft) and four per seat it opens.
    Owner's budget, 09-23. Standing is read from facts and asks nothing."""
    return 4 * math.ceil(draft / BLOCK_SIZE) + 4 * seats


def _spent(asked: int, budget: int) -> dict[str, int]:
    if asked > budget:
        logger.warning("The thin layer asked %d questions (budget %d)", asked, budget)
    return {"asked": asked, "budget": budget}


def rejoined_blocks(
    partition: Sequence[Sequence[str]],
    cut: Sequence[Mapping[str, Any]],
    newcomers: set[str],
    outcomes: Sequence[ThinSlot],
) -> list[list[str]]:
    """The first vote's blocks as the filled cut holds them, only the ones a newcomer joined.

    A swap takes the place of the shot it replaced; an appended newcomer joins the first block
    whose last shot was taken at or after it, or the last block. A block grown past twelve is
    split evenly and only its parts holding a newcomer are returned.
    """
    taken = {row["asset_id"]: (str(row["taken"]), row["asset_id"]) for row in cut}
    members = [[asset for asset in block if asset in taken] for block in partition] or [[]]
    home = {asset: index for index, block in enumerate(partition) for asset in block}
    home.update(
        {slot.filled_by: home[slot.replacing] for slot in outcomes if slot.replacing in home}
    )
    ends = [taken[block[-1]] if block else None for block in members]
    for asset in sorted(newcomers & set(taken), key=taken.__getitem__):
        index = home.get(asset)
        members[_time_block(ends, taken[asset]) if index is None else index].append(asset)
    groups = (
        group
        for block in members
        for group in balanced_groups(sorted(block, key=taken.__getitem__))
    )
    return [group for group in groups if newcomers & set(group)]


def _time_block(ends: Sequence[tuple[str, str] | None], when: tuple[str, str]) -> int:
    """The first block whose last shot was taken at or after `when`, or the last block."""
    return next(
        (index for index, end in enumerate(ends) if end is not None and end >= when),
        len(ends) - 1,
    )


def _with_records(
    rows: Sequence[Mapping[str, Any]], catalogue: ThinCatalogue
) -> list[dict[str, Any]]:
    """Each shot with what the catalogue records it as, which is what protects it from a vote.

    The vote and the standing gate read `notable_record` off the shot itself, so a record the
    catalogue holds but the shot does not carry protects nothing.
    """
    marked = []
    for row in rows:
        record = catalogue.notable_record_of(row["asset_id"])
        marked.append(dict(row) | {"notable_record": record} if record else dict(row))
    return marked


def _removed(carriers: Sequence[Mapping[str, Any]], kept: Sequence[Mapping[str, Any]]):
    """The draft rows the gates or the vote took out, by asset."""
    staying = {row["asset_id"] for row in kept}
    return {row["asset_id"]: row for row in carriers if row["asset_id"] not in staying}


def _drafted_shots(
    asset_ids_of: Mapping[str, Sequence[str]], drafted: Sequence[Mapping[str, Any]]
) -> dict[str, list[str]]:
    """The draft's shots, by the story each one belongs to. No draft reads nothing."""
    shots = {row["asset_id"] for row in drafted}
    chosen = {key: [a for a in assets if a in shots] for key, assets in asset_ids_of.items()}
    return {key: assets for key, assets in chosen.items() if assets}


def _refusal_row(refusal: GateRefusal) -> dict[str, str]:
    return {
        "asset_id": refusal.asset_id,
        "story": refusal.story,
        "rule": refusal.rule,
        "detail": refusal.detail,
    }


def _story_asset_ids(
    episodes: Sequence[Any],
    stories: Sequence[Mapping[str, Any]],
    moment_assets: Mapping[str, Sequence[str]],
) -> dict[str, list[str]]:
    """Which pictures of the period each catalogued story is about."""
    moments_of = {episode.key: tuple(episode.moments) for episode in episodes}
    result = {}
    for story in stories:
        assets: list[str] = []
        for key in story.get("episodes") or ():
            for moment in moments_of.get(key, ()):
                assets.extend(a for a in moment_assets.get(moment, ()) if a not in assets)
        result[str(story["key"])] = assets
    return result
