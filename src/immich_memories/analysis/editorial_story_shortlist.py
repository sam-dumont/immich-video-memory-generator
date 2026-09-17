"""The moments a story can show, the sample offered to the model, and the pick between them.

A depicted moment is what one picture of a story would show; the pictures that can carry it are
its members. Everything here works on those moments: the five-minute capture spacing, the
favourite/life/time sample that keeps a request small, the one competing nearby view per
representative, and the two-order comparison that decides which moments tell the story.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from immich_memories.analysis.editorial_story_pick_contract import ask_moment_pick
from immich_memories.analysis.editorial_story_pick_pages import page_shares, pick_pages

MIN_GAP_IN_CAPTURE_GROUP_SECONDS = 300


@dataclass
class DepictedChoice:
    """One depicted moment of a story and the pictures that can carry it."""

    key: str
    episode: str
    taken: str
    content: str
    primary: str
    alternatives: list[str] = field(default_factory=list)

    @property
    def members(self) -> list[str]:
        return [self.primary, *self.alternatives]


def _spread(items: Sequence[Any], count: int) -> list[Any]:
    """Evenly spaced picks across a chronological list: beginning, middle and end before repeats."""
    if count >= len(items):
        return list(items)
    if count <= 0:
        return []
    if count == 1:
        return [items[len(items) // 2]]
    step = (len(items) - 1) / (count - 1)
    picks, seen = [], set()
    for i in range(count):
        index = round(i * step)
        if index not in seen:
            seen.add(index)
            picks.append(items[index])
    return picks


def _company_relations(marker: str) -> frozenset[str]:
    people = marker.split("with: ", 1)[1] if "with: " in marker else ""
    return frozenset(relation.strip() for relation in people.split(",") if relation.strip())


def _nominate_new_relations(
    picked: list[DepictedChoice],
    choices: Sequence[DepictedChoice],
    companies: Mapping[str, frozenset[str]],
    *,
    protected: set[str],
    represented: frozenset[str],
) -> None:
    """Insert early relationship opportunities in place, without re-spacing the sample.

    Changing the sample's length or count moves unrelated stages throughout the story, so each
    nomination can replace only one nearby, non-favourite sample whose relationships stay covered.
    """
    positions = {c.key: index for index, c in enumerate(choices)}
    endpoints = {min(picked, key=lambda c: c.taken).key, max(picked, key=lambda c: c.taken).key}
    for candidate in choices:
        relations = companies[candidate.key]
        if candidate.key in protected or not relations - represented:
            continue
        if candidate not in picked:
            displaced = _replaceable_sample(
                picked,
                candidate,
                companies,
                protected=protected,
                endpoints=endpoints,
                positions=positions,
                relations=relations,
            )
            if displaced is None:
                continue
            picked[picked.index(displaced)] = candidate
        protected.add(candidate.key)
        represented |= relations


def _replaceable_sample(
    picked: Sequence[DepictedChoice],
    candidate: DepictedChoice,
    companies: Mapping[str, frozenset[str]],
    *,
    protected: set[str],
    endpoints: set[str],
    positions: Mapping[str, int],
    relations: frozenset[str],
) -> DepictedChoice | None:
    replaceable = [
        c
        for c in picked
        if c.key not in protected
        and companies[c.key]
        <= relations.union(*(companies[other.key] for other in picked if other != c))
    ]
    if not replaceable:
        return None
    return min(
        replaceable,
        key=lambda c: (
            c.key in endpoints,
            abs(positions[c.key] - positions[candidate.key]),
            positions[c.key],
        ),
    )


def shortlist_story_moments(
    choices: list[DepictedChoice],
    grant: int,
    *,
    starred: Callable[[DepictedChoice], bool],
    life: Callable[[str], bool],
    kind_of: Callable[[DepictedChoice], str] = lambda _c: "",
) -> list[DepictedChoice]:
    """Keep the favourite/life/time sample, inserting early relationship opportunities locally.

    The chronological input is capped at three times the grant (floor six). Relationship
    markers nominate opportunities; standing and audience admission still happen afterward.
    """
    limit = max(6, 3 * grant)
    if len(choices) <= limit:
        return choices
    stars = [c for c in choices if starred(c)]
    lively = [c for c in choices if c not in stars and any(life(a) for a in c.members)]
    rest = [c for c in choices if c not in stars and c not in lively]
    picked = stars[:limit]
    picked.extend(_spread(lively, limit - len(picked)))
    picked.extend(_spread(rest, limit - len(picked)))
    if len(stars) >= limit:
        return sorted(picked, key=lambda c: c.taken)

    companies = {c.key: _company_relations(kind_of(c)) for c in choices}
    _nominate_new_relations(
        picked,
        choices,
        companies,
        protected={c.key for c in stars},
        represented=frozenset().union(*(companies[c.key] for c in stars)),
    )
    return sorted(picked, key=lambda c: c.taken)


def _capture_group_moments(
    units: Sequence[dict],
    *,
    quality: Callable[[str], float],
    flagged: Callable[[str], bool] = lambda _a: False,
    life: Callable[[str], bool] = lambda _a: True,
) -> list[DepictedChoice]:
    """Without a model inventory a capture group is the moment; the favourite, else the best unflagged
    picture that shows life, carries it."""
    groups: dict[str, list[dict]] = {}
    for u in units:
        groups.setdefault(u.get("moment") or u["asset_id"], []).append(u)
    choices = []
    for moment, members in groups.items():
        ordered = sorted(
            members,
            key=lambda u: (
                not u.get("favourite"),
                flagged(u["asset_id"]),
                not life(u["asset_id"]),
                -quality(u["asset_id"]),
                u["taken"],
            ),
        )
        choices.append(
            DepictedChoice(
                key=f"{moment}:cg",
                episode="",
                taken=min(u["taken"] for u in members),
                content="capture group",
                primary=ordered[0]["asset_id"],
                alternatives=[u["asset_id"] for u in ordered[1:]],
            )
        )
    return sorted(choices, key=lambda c: c.taken)


def _instant(value):
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _spaced(
    chosen: Sequence[DepictedChoice],
    unit_by_asset: Mapping[str, Any],
    already: Sequence[Mapping[str, Any]] = (),
    *,
    among_choices: bool = True,
) -> list[DepictedChoice]:
    """Count or validate five-minute spacing, or filter against committed carriers only.

    Candidate comparison keeps nearby alternatives with ``among_choices=False``;
    physical capacity and the final cut still enforce their mutual exclusion.
    """
    occupied = [(u.get("moment"), _instant(u.get("taken"))) for u in already]
    kept: list[DepictedChoice] = []
    for c in sorted(chosen, key=lambda c: c.taken):
        unit = unit_by_asset[c.primary][1]
        group, t = unit.get("moment"), _instant(unit["taken"])
        if t is None:
            kept.append(c)
            continue
        close = any(
            g == group
            and kt is not None
            and abs((t - kt).total_seconds()) < MIN_GAP_IN_CAPTURE_GROUP_SECONDS
            for g, kt in occupied
        )
        if not close:
            kept.append(c)
            if among_choices:
                occupied.append((group, t))
    return kept


def nearby_picture_alternatives(
    primary_choices: Sequence[DepictedChoice],
    choices: Sequence[DepictedChoice],
    unit_by_asset: Mapping[str, Any],
    *,
    starred: Callable[[DepictedChoice], bool],
) -> list[DepictedChoice]:
    """One competing nearby picture per nonstarred representative, without deleting breadth."""
    used = {c.key for c in primary_choices}
    extras = []
    for primary in primary_choices:
        if starred(primary):
            continue  # the owner's chosen representative already leads this moment
        occupied = [unit_by_asset[primary.primary][1]]
        candidates = [
            c
            for c in choices
            if c.key not in used and not _spaced([c], unit_by_asset, already=occupied)
        ]
        if candidates:
            alternative = min(candidates, key=lambda c: (not starred(c), c.taken))
            used.add(alternative.key)
            extras.append(alternative)
    return extras


class _Chosen:
    """The keys picked so far, under a ceiling the grant sets and the larger vote settles."""

    def __init__(self, by_key: Mapping[str, DepictedChoice], compatible, limit: int) -> None:
        self.keys: list[str] = []
        self.limit = limit
        self._by_key = by_key
        self._compatible = compatible

    def __len__(self) -> int:
        return len(self.keys)

    def add(self, key: str) -> None:
        if (
            key not in self.keys
            and len(self.keys) < self.limit
            and self._compatible(self._by_key[key], [self._by_key[k] for k in self.keys])
        ):
            self.keys.append(key)


def _pick_rows(
    labels: Mapping[str, str],
    *,
    starred,
    kind_of,
    motions: Mapping[str, str],
) -> Callable[[DepictedChoice], str]:
    def row(c: DepictedChoice) -> str:
        star = " | favourite" if starred(c) else ""
        description = f"{labels[c.key]} | {c.taken[:16]} | {c.content[:140]} | {len(c.members)} picture(s){star}{kind_of(c)}"
        if motions.get(c.key):
            description += f"\n  Sampled sequence: {motions[c.key]}"
        return description

    return row


def _pick_prompt(
    contract: str,
    story: Mapping[str, Any],
    listing: str,
    *,
    count: int,
    allow_fewer: bool,
    sampled_motion: bool,
) -> str:
    # A single representative cannot have repetitive depth. Preserve that
    # established question exactly; only additional depth gets a ceiling.
    #
    # A hosted 30B answered with a whole row instead of its label (#908), so the
    # vocabulary is named where the rows are, not only in the format rule.
    shape = 'each row starts with its label (e.g. "M01")'
    # The story, its size and its rows are last. Everything above them depends only on the
    # contract and the two branch flags, so every story asked the same way shares that whole
    # preamble byte for byte (#981).
    task = "Keep only the moments" if allow_fewer else "Keep the moments"
    prompt = (
        f"{contract}\n\n"
        f"{task} that tell the story below: its stages across its whole span rather than one day, "
        "its favourites, what happened and who shared it; a second moment adds people or a place the "
        "first did not show. "
        "Judge the proposed picture, not just the importance of the event it describes. "
        "Clear personal participation and shared company can carry an outing, including a selfie in its activity and place. "
        "Recording length is available material, not importance. Dense overlays or distant subjects can weaken a picture. "
        "A different pose, framing, take, or date does not by itself make the same activity another contribution. "
        "This memory is a film: when a video or a playing Live Photo carries a moment, choose it over a still of the same moment. "
        "Leave slots unused when further views repeat what is already told or add only weak filler. "
    )
    if sampled_motion:
        prompt += (
            "\nFor action across time, use the sampled sequence where available. "
            "A cover's pose or inventory label cannot establish a separate activity "
            "when the sequence shows the same contribution."
        )
    introduction = (
        f"This story gets {count} picture(s) at most in the memory, one per contribution. Its candidate moments, {shape}:\n\n{listing}\n\n"
        if allow_fewer
        else f"This story gets {count} picture(s) in the memory, one per moment. Its distinct moments, {shape}:\n\n{listing}\n\n"
    )
    prompt += f"\n\nSTORY: {story['title']}. {story.get('purpose') or ''}\n{introduction}"
    prompt += (
        f'Return JSON only, with a "keep" array of at most {count} distinct labels from the rows above. '
        f'"unused_slots" is {count} minus the number kept, not the number of rejected '
        f'candidates; use 0 when keeping {count}. If fewer, explain "why_fewer" in one sentence. '
        "A complete explained shortfall is valid; an incomplete answer is not."
        if allow_fewer
        else f'Return JSON only, with a "keep" array of exactly {count} distinct labels from the rows above.'
    )
    return prompt


def _vote_both_orders(
    judge,
    *,
    story: Mapping[str, Any],
    rows: Sequence[str],
    by_label: Mapping[str, str],
    contract: str,
    count: int,
    allow_fewer: bool,
    sampled_motion: bool,
    vote_records: list,
    suffix: str = "",
) -> list[list[str]]:
    kept_by_order = []
    for order_name, order in (("source", list(rows)), ("reversed", list(reversed(rows)))):
        prompt = _pick_prompt(
            contract,
            story,
            "\n".join(order),
            count=count,
            allow_fewer=allow_fewer,
            sampled_motion=sampled_motion,
        )
        found = ask_moment_pick(
            judge,
            f"story-pick-{story['key']}{suffix}-{order_name}",
            prompt,
            labels=set(by_label),
            count=count,
            allow_fewer=allow_fewer,
            record=vote_records.append,
        )
        kept_by_order.append([by_label[m] for m in found])
    return kept_by_order


@dataclass
class _PickRequest:
    """What every page of one story's pick shares: its rendered rows and its question.

    ``rows``, ``labels`` and ``keys`` are parallel to the shortlist, so a page is a run of
    positions in it.
    """

    story: Mapping[str, Any]
    rows: Sequence[str]
    labels: Sequence[str]
    keys: Sequence[str]
    contract: str
    allow_fewer: bool
    sampled_motion: bool
    paged: bool


def _vote_every_page(
    judge,
    request: _PickRequest,
    groups: Sequence[Sequence[int]],
    shares: Sequence[int],
    *,
    vote_records: list,
) -> tuple[list[list[str]], list[dict]]:
    """Ask every page in chronological order, rolling the slots one leaves unused into the next.

    A page answers only for its own rows, so each request names a number the reader can
    count. The story's vote is the union of theirs, in each order.
    """
    kept_by_order: list[list[str]] = [[], []]
    audit: list[dict] = []
    carried = 0
    for number, (positions, share) in enumerate(zip(groups, shares, strict=True), 1):
        asked = min(share + carried, len(positions))
        votes: list[list[str]] = [[], []]
        if asked:
            votes = _vote_both_orders(
                judge,
                story=request.story,
                rows=[request.rows[at] for at in positions],
                by_label={request.labels[at]: request.keys[at] for at in positions},
                contract=request.contract,
                count=asked,
                allow_fewer=request.allow_fewer,
                sampled_motion=request.sampled_motion,
                vote_records=vote_records,
                suffix=f"-page-{number}" if request.paged else "",
            )
        kept_by_order[0].extend(votes[0])
        kept_by_order[1].extend(votes[1])
        kept = max(len(votes[0]), len(votes[1]))
        carried = asked - kept
        audit.append(
            {
                "page": number,
                "offered": len(positions),
                "share": share,
                "kept": kept,
                "unused_slots": carried,
            }
        )
    return kept_by_order, audit


def _reading_order(
    choices: Sequence[DepictedChoice], plays: Callable[[DepictedChoice], bool]
) -> list[DepictedChoice]:
    """Moments that play lead the rows, the rest keep their chronology: this is a video product."""
    moving = [c for c in choices if plays(c)]
    leading = {c.key for c in moving}
    return [*moving, *(c for c in choices if c.key not in leading)]


def _vote_the_shortlist(
    judge,
    *,
    story: Mapping[str, Any],
    choices: Sequence[DepictedChoice],
    count: int,
    labels: Mapping[str, str],
    motions: Mapping[str, str],
    starred: Callable[[DepictedChoice], bool],
    kind_of: Callable[[DepictedChoice], str],
    plays: Callable[[DepictedChoice], bool],
    contract: str,
    vote_records: list,
) -> tuple[list[list[str]], list[dict]]:
    """Ask the shortlist in as few requests as the budget carries, splitting the grant between them.

    One page is the question this stage has always asked, byte for byte. Several pages each
    name a number their own rows can answer, which is what a large grant was failing at.
    """
    allow_fewer = count > 1
    row = _pick_rows(labels, starred=starred, kind_of=kind_of, motions=motions)
    offered = _reading_order(choices, plays)
    rows = [row(c) for c in offered]
    sampled_motion = any(motions.values())
    overhead = len(
        _pick_prompt(
            contract, story, "", count=count, allow_fewer=allow_fewer, sampled_motion=sampled_motion
        )
    )
    groups = pick_pages(rows, overhead=overhead)
    shares = page_shares(
        [len(group) for group in groups],
        count,
        favourite_pages=[
            number
            for number, group in enumerate(groups)
            if any(starred(offered[position]) for position in group)
        ],
    )
    request = _PickRequest(
        story=story,
        rows=rows,
        labels=[labels[c.key] for c in offered],
        keys=[c.key for c in offered],
        contract=contract,
        allow_fewer=allow_fewer,
        sampled_motion=sampled_motion,
        paged=len(groups) > 1,
    )
    return _vote_every_page(judge, request, groups, shares, vote_records=vote_records)


def _fresh_relation(
    choices: Sequence[DepictedChoice],
    *,
    chosen_keys: Sequence[str],
    by_key: Mapping[str, DepictedChoice],
    companies: Mapping[str, frozenset[str]],
    represented: frozenset[str],
    replaced: str,
    compatible,
    replacement_allowed,
    rejected: set[str],
) -> str | None:
    for candidate in choices:
        if (
            candidate.key in chosen_keys
            or candidate.key in rejected
            or not companies[candidate.key] - represented
        ):
            continue
        if not compatible(candidate, [by_key[k] for k in chosen_keys if k != replaced]):
            continue
        if not replacement_allowed(candidate):
            rejected.add(candidate.key)
            continue
        return candidate.key
    return None


def _improve_company(
    chosen_keys: list[str],
    choices: Sequence[DepictedChoice],
    *,
    by_key: Mapping[str, DepictedChoice],
    companies: Mapping[str, frozenset[str]],
    starred,
    compatible,
    replacement_allowed,
) -> tuple[list[dict], set[str]]:
    """Depth adds a relationship not yet represented, not merely a new ordering or combination
    of the same relationships. Never lose existing coverage or displace a favourite for it."""
    replacements: list[dict] = []
    rejected: set[str] = set()
    for key in reversed(chosen_keys.copy()):
        if starred(by_key[key]) or not companies[key]:
            continue
        represented = frozenset().union(
            *(companies[other] for other in chosen_keys if other != key)
        )
        if not companies[key] <= represented:
            continue
        fresh = _fresh_relation(
            choices,
            chosen_keys=chosen_keys,
            by_key=by_key,
            companies=companies,
            represented=represented,
            replaced=key,
            compatible=compatible,
            replacement_allowed=replacement_allowed,
            rejected=rejected,
        )
        if fresh:
            chosen_keys[chosen_keys.index(key)] = fresh
            replacements.append(
                {
                    "removed": key,
                    "added": fresh,
                    "new_relations": sorted(companies[fresh] - represented),
                }
            )
    return replacements, rejected


def _uncontested_moments(
    choices: Sequence[DepictedChoice], *, starred, compatible
) -> list[DepictedChoice]:
    kept: list[DepictedChoice] = []
    for choice in sorted(choices, key=lambda c: not starred(c)):
        if not compatible(choice, kept):
            continue
        kept.append(choice)
    return sorted(kept, key=lambda c: c.taken)


def _pick_material(
    choices: Sequence[DepictedChoice], *, motion_of, is_video
) -> tuple[dict[str, str], dict[str, str]]:
    """Row labels and the sampled sequences shown beside them.

    Every offered video is sampled, whatever the grant: a story with one slot is still
    choosing between a video and a still, and that is the choice this product cares about.
    Pictures are read from the inventory the story already produced. Images are
    observed for the cut, at carrier admission, not for every offered choice.
    """
    labels = {c.key: f"M{i + 1:02d}" for i, c in enumerate(choices)}
    videos = [c for c in choices if is_video(c)]
    motions = {c.key: motion_of(c) for c in videos} if motion_of is not None else {}
    return labels, motions


def _fill_from_votes(
    chosen: _Chosen, choices: Sequence[DepictedChoice], *, agreed, kept_by_order, plays
) -> None:
    """What both orders named, then the rest of either order, then even spacing.

    A star buys no slot here: it leads the rows the pick reads, and it wins the frame of the
    moment the pick chooses, so a dense tail of favourites cannot hide a story's beginning.
    Where the orders split, a moment that plays goes before a still: the reader could not
    decide, and this is a video product. Otherwise the source order breaks the tie.
    """
    by_key = {c.key: c for c in choices}
    split = sorted((*kept_by_order[0], *kept_by_order[1]), key=lambda k: not plays(by_key[k]))
    for k in (*agreed, *split):
        if len(chosen) >= chosen.limit:
            break
        chosen.add(k)
    if len(chosen) < chosen.limit:
        rest = [c for c in choices if c.key not in chosen.keys]
        preferred = _spread(rest, chosen.limit - len(chosen))
        for c in (*preferred, *(c for c in rest if c not in preferred)):
            chosen.add(c.key)


def pick_story_moments(
    judge,
    *,
    story: Mapping[str, Any],
    choices: Sequence[DepictedChoice],
    count: int,
    starred: Callable[[DepictedChoice], bool],
    contract: str,
    record: Callable[[str, Mapping[str, Any]], None],
    kind_of: Callable[[DepictedChoice], str] = lambda _c: "",
    replacement_allowed: Callable[[DepictedChoice], bool] = lambda _c: True,
    compatible: Callable[[DepictedChoice, Sequence[DepictedChoice]], bool] = lambda _c, _others: (
        True
    ),
    motion_of: Callable[[DepictedChoice], str] | None = None,
    is_video: Callable[[DepictedChoice], bool] = lambda _c: False,
    plays: Callable[[DepictedChoice], bool] = lambda _c: False,
) -> list[DepictedChoice]:
    """Compare contributions within a ceiling; only one offered moment leaves nothing to ask.

    Both orders may explicitly decline repetitive depth. Their larger complete
    vote caps the result; disagreement about identity cannot manufacture depth.
    """
    # A grant that reaches the only moment offered has nothing to ask. Everything else is a
    # question: which moments tell the story, and whether a further view earns a slot at all.
    # Stars answer neither. The favourite wins the frame of its own moment, not its story's slot.
    if count >= len(choices) and len(choices) <= 1:
        return _uncontested_moments(choices, starred=starred, compatible=compatible)
    if count <= 0:
        return []
    count = min(count, len(choices))
    by_key = {c.key: c for c in choices}
    chosen = _Chosen(by_key, compatible, count)
    labels, motions = _pick_material(choices, motion_of=motion_of, is_video=is_video)
    vote_records: list = []
    kept_by_order, page_audit = _vote_the_shortlist(
        judge,
        story=story,
        choices=choices,
        count=count,
        labels=labels,
        motions=motions,
        starred=starred,
        kind_of=kind_of,
        plays=plays,
        contract=contract,
        vote_records=vote_records,
    )
    # Respect the larger complete vote, never more.
    # Disagreement over *which* one view wins cannot turn two one-view votes into two slots.
    chosen.limit = max(len(order) for order in kept_by_order)
    agreed = [k for k in kept_by_order[0] if k in set(kept_by_order[1])]
    _fill_from_votes(chosen, choices, agreed=agreed, kept_by_order=kept_by_order, plays=plays)
    chosen_keys = chosen.keys[: chosen.limit]
    companies = {key: _company_relations(kind_of(by_key[key])) for key in by_key}
    company_replacements, company_rejected = _improve_company(
        chosen_keys,
        choices,
        by_key=by_key,
        companies=companies,
        starred=starred,
        compatible=compatible,
        replacement_allowed=replacement_allowed,
    )
    record(
        f"story-pick-{story['key']}",
        {
            "count": count,
            "orders": kept_by_order,
            "agreed": agreed,
            "chosen": chosen_keys,
            "company_replacements": company_replacements,
            "company_rejected": sorted(company_rejected),
            "motion_choices": {k: v for k, v in motions.items() if v},
            "pages": page_audit,
            "editorial_limit": chosen.limit,
            "vote_records": vote_records,
        },
    )
    return sorted((by_key[k] for k in chosen_keys[:count]), key=lambda c: c.taken)
