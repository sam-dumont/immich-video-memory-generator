"""A recurring activity is one thread, in the context of the film.

The grouping keeps a story's days consecutive (`_broken_spans`): a reader that folds gapped days
together usually folds unrelated ones (a week of "cycling" around a pregnancy test). The cost
was the opposite case, the same activity at the same place on separate days, which came back as
four one-day swimming stories and four frames of one pool.

After the weighing, stories at the same place and inside one era of the film are nominated as a
thread when the reader's own words link them. Near home that takes an activity: the same activity
phrase, or an activity word the film uses at that place more than anywhere else. Away from home
the name the reader gave them (one title before the consecutive-day rule split it, or the same
title) is enough. The film's home place never holds a thread, a place alone never links two days,
and neither does a person's name. One banked question per nominated group then asks the reader,
with the film's span and contract, which of them are one recurring activity and which are steps
worth showing apart. A confirmed thread is one story with the weight of its heaviest member.

A film longer than `ERA_THRESHOLD_DAYS` is read in calendar years, as the product contract reads
it, and keeps one thread per year so a child's progress at the pool still shows.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date
from functools import partial
from operator import itemgetter
from typing import Any

from immich_memories.analysis.editorial_intent import ERA_THRESHOLD_DAYS
from immich_memories.analysis.editorial_page_recovery import read_page_answer
from immich_memories.analysis.editorial_story_replies import (
    STORY_VERSION,
    WEIGHT_ROLE,
    WEIGHTS,
    _lenient_object,
)
from immich_memories.analysis.editorial_story_weighing import story_priorities

THREAD_QUESTION_VERSION = "recurring-activity-v3"
_WORD = re.compile(r"[^\W\d_]{4,}")
_NAMES = re.compile(r"\| with ([^|]+)")
# Not the activity: grammar, company, when and where, and the container nouns a title uses to say
# that something happened without saying what ("moments", "life", "stay"). The reader writes in
# English. "Early home life" is a period of the film, not something done again and again.
_FUNCTION_WORDS = frozenset(
    {
        "about",
        "above",
        "after",
        "again",
        "along",
        "also",
        "among",
        "around",
        "before",
        "behind",
        "below",
        "beside",
        "between",
        "both",
        "came",
        "come",
        "down",
        "during",
        "each",
        "even",
        "from",
        "have",
        "here",
        "into",
        "just",
        "like",
        "more",
        "most",
        "much",
        "near",
        "next",
        "only",
        "onto",
        "other",
        "over",
        "same",
        "some",
        "such",
        "than",
        "that",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "under",
        "until",
        "upon",
        "very",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "with",
        "within",
        "without",
        "your",
        "baby",
        "babies",
        "child",
        "children",
        "family",
        "families",
        "friend",
        "friends",
        "kids",
        "people",
        "together",
        "activity",
        "activities",
        "interaction",
        "interactions",
        "moment",
        "moments",
        "scene",
        "scenes",
        "session",
        "sessions",
        "time",
        "times",
        "daily",
        "early",
        "evening",
        "extended",
        "home",
        "house",
        "indoor",
        "indoors",
        "life",
        "morning",
        "night",
        "outdoor",
        "outdoors",
        "routine",
        "routines",
        "stay",
        "weekend",
        "days",
        "week",
        "weeks",
        "month",
        "months",
        "year",
        "years",
    }
)

_KINSHIP = re.compile(
    r"(?:grand|step)?(?:mother|father|parent|mom|mum|dad|mama|papa)s?"
    r"|(?:sibling|brother|sister|uncle|aunt|niece|nephew|cousin|daughter|partner|spouse"
    r"|husband|wife|wives)s?"
)


def _words(text: str) -> set[str]:
    return {
        word
        for word in (w.lower() for w in _WORD.findall(text))
        if word not in _FUNCTION_WORDS and not _KINSHIP.fullmatch(word)
    }


def _outside_parentheses(text: str) -> str:
    depth, kept = 0, []
    for character in text:
        depth += {"(": 1, ")": -1}.get(character, 0)
        if depth == 0 and character != ")":
            kept.append(character)
    return "".join(kept)


def person_words(lines: Iterable[str]) -> set[str]:
    """The words of every known person's name on the annotation lines: who, never what."""
    words: set[str] = set()
    for line in lines:
        match = _NAMES.search(line)
        if match:
            words |= _words(_outside_parentheses(match.group(1)))
    return words


def era_of(span: tuple[date, date] | None) -> Callable[[str], str]:
    """One era for a film up to ERA_THRESHOLD_DAYS long, a calendar year each beyond it."""
    if span is None or (span[1] - span[0]).days <= ERA_THRESHOLD_DAYS:
        return lambda _day: ""
    return itemgetter(slice(4))


class _Story:
    """What the nomination reads of one weighed story."""

    def __init__(self, story, *, days, place, words, phrase, near_home) -> None:
        self.story = story
        self.key = story["key"]
        self.days = days
        self.place = place
        self.words = words
        self.phrase = phrase
        self.near_home = near_home
        self.name = story.get("split_from") or story["title"].strip().lower()


class _Links:
    """Which stories of one place and era the reader's own words connect.

    Near home only an activity links two days. Away from home, the name the reader gave them
    (one title before the consecutive-day rule split it, or the same title) links them too.
    """

    def __init__(self, members: Sequence[_Story], specific: set[str], *, away: bool) -> None:
        self.members = members
        self._specific = specific
        self._away = away

    def linked(self, a: _Story, b: _Story) -> bool:
        if set(a.days) & set(b.days):
            return False
        if self._away and a.name == b.name:
            return True
        if a.phrase and a.phrase == b.phrase:
            return True
        return bool(a.words & b.words & self._specific)

    def components(self, members: Sequence[_Story] | None = None) -> list[list[_Story]]:
        pending = list(self.members if members is None else members)
        groups: list[list[_Story]] = []
        while pending:
            group = [pending.pop(0)]
            grown = True
            while grown:
                grown = False
                for other in pending.copy():
                    if any(self.linked(other, member) for member in group):
                        group.append(other)
                        pending.remove(other)
                        grown = True
            if len(group) > 1:
                groups.append(group)
        return groups


def _describe(story, episode_of, hints, excluded, near_home) -> _Story:
    days = sorted({str((hints.get(k) or {}).get("day") or "") for k in story["episodes"]} - {""})
    places = Counter(
        str((hints.get(k) or {}).get("place") or "").split(":", 1)[-1].strip()
        for k in story["episodes"]
    )
    places.pop("", None)
    titles = [story["title"], *(episode_of[k].title for k in story["episodes"] if k in episode_of)]
    phrase = " ".join(sorted(_words(story["title"]) - excluded))
    return _Story(
        story,
        days=days,
        place=places.most_common(1)[0][0] if places else "",
        words=set().union(*map(_words, titles)) - excluded,
        phrase=phrase,
        near_home=near_home(
            [m for k in story["episodes"] if k in episode_of for m in episode_of[k].moments]
        ),
    )


def _question(contract: str, span: str, rows: list[str]) -> str:
    return f"""Recurring activities in this memory. {STORY_VERSION}. {THREAD_QUESTION_VERSION}.
{contract}

The film covers {span}. The stories below happened at the same place on different days, and the
reader named them alike. A recurring activity is one specific thing done there again and again: a
swimming lesson, a sports practice, repeated visits to the same garden or playground. A period of
life, a stay, or ordinary days that only share the place are not one activity. In THIS film, are
some of these the same recurring activity, so that one picture can stand for all of them? Or are
they steps worth showing apart: a first time, a change, progress across the film? Name each
recurring activity once, as one group holding every one of its days, however each day is titled.
Leave out every story that should stay apart; a story belongs to at most one group.

Return JSON only: {{"same": [["K01", "K02"]]}} with story keys from the rows below, or {{"same": []}}.

STORIES (key | days | weight | title | day titles)
{chr(10).join(rows)}
"""


def _read_groups(raw: str, *, offered: set[str]) -> list[list[str]]:
    """The groups the reader called one recurring activity each; a flat list is one group."""
    obj = _lenient_object(raw)
    named = obj.get("same")
    if not isinstance(named, list):
        raise ValueError('"same" must be a list of groups of story keys')
    if all(isinstance(item, str) for item in named):
        named = [named]
    seen: set[str] = set()
    groups = []
    for group in named:
        keys = [k for k in group if isinstance(k, str) and k in offered and k not in seen]
        seen.update(keys)
        if len(keys) > 1:
            groups.append(keys)
    return groups


def _fold(stories: list[dict], members: list[_Story], era: str) -> dict:
    """One story in place of the thread's members, at the first member's position."""
    heaviest = min((m.story["weight"] for m in members), key=WEIGHTS.index)
    days = sorted(day for m in members for day in m.days)
    first = members[0].story
    thread = {
        "place": members[0].place,
        "era": era,
        "days": days,
        "members": [m.story["title"] for m in members],
    }
    purpose = (
        f"One recurring activity on {len(days)} separate days ({days[0]} to {days[-1]}): one "
        "picture stands for it, a further one only if it shows something new."
        + (f" {first.get('purpose')}" if first.get("purpose") else "")
    )[:300]
    folded = {
        "key": first["key"],
        "title": first.get("split_from") or first["title"],
        "episodes": [k for m in sorted(members, key=lambda m: m.days) for k in m.story["episodes"]],
        "weight": heaviest,
        "purpose": purpose,
        "thread": thread,
    }
    keys = {m.key for m in members}
    at = next(i for i, s in enumerate(stories) if s["key"] in keys)
    stories[:] = [s for s in stories if s["key"] not in keys]
    stories.insert(at, folded)
    return folded


def thread_scope(*, rules: bool, journey: bool, gapped: bool) -> str | None:
    """Why a film asks no thread question, or None when it does."""
    if rules:
        return "the rules reader asks no question"
    if journey:
        return "a trip film shows its days in order"
    if gapped:
        return "a subject memory already groups its stages across gaps"
    return None


def fold_threads(
    judge,
    story,
    *,
    contract: str,
    span: tuple[date, date] | None,
    lines: Iterable[str],
    record: Callable[[str, Mapping[str, Any]], None],
    near_home: Callable[[Sequence[str]], bool | None] = lambda _moments: None,
    skip: str | None = None,
) -> int:
    """Nominate, ask and fold the film's recurring activities; returns the questions asked.

    `near_home(moments)` says whether a story's pictures were taken near the home base (None
    when nothing says). The film's home place itself never holds a thread: its days are the film.
    """
    if skip is not None:
        record("story-threads", {"version": THREAD_QUESTION_VERSION, "status": skip})
        return 0
    hints = story.audit.get("hints") or {}
    episode_of = {e.key: e for e in story.episodes}
    places = {str(h.get("place") or "").split(":", 1)[-1] for h in hints.values()}
    excluded = person_words(lines) | set().union(*map(_words, places))
    described = [
        _describe(s, episode_of, hints, excluded, near_home)
        for s in story.stories
        if s.get("weight") not in ("", "none") and not s.get("trip") and not s.get("joined_into")
    ]
    specific = _place_specific(described)
    era = era_of(span)
    label = f"{span[0].isoformat()} to {span[1].isoformat()}" if span else "the requested period"
    home = _home_place(described)
    audit: dict[str, Any] = {
        "version": THREAD_QUESTION_VERSION,
        "home_place": home,
        "nominated": [],
        "threads": [],
    }
    asked = 0
    for (place, period), members in _by_place_and_era(described, era).items():
        if place == home:
            continue
        away = all(m.near_home is False for m in members)
        links = _Links(members, specific.get(place, set()), away=away)
        for group in links.components():
            asked += 1
            confirmed = _ask(judge, contract, label, group, episode_of, asked, audit, place, period)
            _fold_confirmed(story.stories, links, group, confirmed, period, audit)
    if audit["threads"]:
        _refresh(story)
    record("story-threads", audit)
    return asked


def _fold_confirmed(stories, links, group, confirmed, period, audit) -> None:
    """Each confirmed group, split again into the parts its own links connect, is one story."""
    by_key = {m.key: m for m in group}
    for keys in confirmed:
        for thread in links.components([by_key[k] for k in keys]):
            ordered = sorted(thread, key=lambda m: stories.index(m.story))
            folded = _fold(stories, ordered, period)
            audit["threads"].append({"key": folded["key"]} | folded["thread"])


def _home_place(described: Sequence[_Story]) -> str:
    """The place most stories near the home base happened at; without a home base, the place
    most stories happened at."""
    near = Counter(s.place for s in described if s.place and s.near_home)
    known = near or Counter(s.place for s in described if s.place)
    return known.most_common(1)[0][0] if known else ""


def _place_specific(described: Sequence[_Story]) -> dict[str, set[str]]:
    """Per place, the activity words the film uses there more than at any other place."""
    counts: dict[str, Counter[str]] = {}
    for s in described:
        for word in s.words:
            counts.setdefault(word, Counter())[s.place] += 1
    specific: dict[str, set[str]] = {}
    for word, where in counts.items():
        ranked = where.most_common(2)
        if ranked[0][0] and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
            specific.setdefault(ranked[0][0], set()).add(word)
    return specific


def _by_place_and_era(described, era) -> dict[tuple[str, str], list[_Story]]:
    grouped: dict[tuple[str, str], list[_Story]] = {}
    for s in described:
        if s.place and s.days:
            grouped.setdefault((s.place, era(s.days[0])), []).append(s)
    return {key: members for key, members in grouped.items() if len(members) > 1}


def _ask(
    judge, contract, label, group, episode_of, number, audit, place, period
) -> list[list[str]]:
    rows = [
        f"{m.key} | {', '.join(m.days)} | {m.story['weight']} | {m.story['title']} | "
        + "; ".join(
            dict.fromkeys(episode_of[k].title for k in m.story["episodes"] if k in episode_of)
        )
        for m in group
    ]
    nomination = {"place": place, "era": period, "stories": [m.key for m in group]}
    try:
        groups = read_page_answer(
            judge,
            stage=f"story-threads-{number}",
            prompt=_question(contract, label, rows),
            max_tokens=200 + 20 * len(rows),
            read=partial(_read_groups, offered={m.key for m in group}),
        )
    except ValueError as exc:
        audit["nominated"].append(nomination | {"status": "kept apart", "error": str(exc)})
        return []
    audit["nominated"].append(nomination | {"same": groups})
    return groups


def _refresh(story) -> None:
    """Roles and priorities follow the folded stories."""
    weight_of = {k: s["weight"] for s in story.stories for k in s["episodes"]}
    for episode in story.episodes:
        if episode.key in weight_of:
            episode.role = WEIGHT_ROLE[weight_of[episode.key]]
    story.priorities = story_priorities(story.stories)
