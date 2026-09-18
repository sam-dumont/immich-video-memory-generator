"""A pick asks the model unless one moment is all a story offers; stars never settle it."""

import json
import re

import pytest

from immich_memories.analysis.editorial_story_pick_contract import (
    carries_motion,
    source_kind_marker,
)
from immich_memories.analysis.editorial_story_pick_pages import MAX_LABELS_PER_ASK, page_shares
from immich_memories.analysis.editorial_story_reading import PAGE_CHARS
from immich_memories.analysis.editorial_story_shortlist import (
    DepictedChoice,
    _capture_group_moments,
    _pick_prompt,
    pick_story_moments,
)


class PickJudge:
    # WHY: replay controlled text-model choices through the real mechanical pick rules;
    # these unit tests never open a model connection.
    def __init__(self, keep=("M01", "M02")):
        self.keep = keep
        self.calls = []

    def ask(self, stage, _prompt, **_kwargs):
        self.calls.append(stage)
        return json.dumps({"keep": self.keep})


def pick(
    judge,
    *,
    days=1,
    count=2,
    stars=(),
    company=None,
    choice_count=3,
    replacement_allowed=lambda _c: True,
):
    choices = [
        DepictedChoice(f"choice-{i}", "K01", f"2030-05-01T10:0{i}", f"View {i}", f"asset-{i}")
        for i in range(1, choice_count + 1)
    ]
    records = []
    selected = pick_story_moments(
        judge,
        story={"key": "K01", "title": "A visit", "seen": {"days": days}},
        choices=choices,
        count=count,
        starred=lambda choice: choice.key in stars,
        contract="Show the visit",
        record=lambda name, value: records.append((name, value)),
        kind_of=lambda choice: f" | with: {company[choice.key]}" if company else "",
        replacement_allowed=replacement_allowed,
    )
    return [choice.key for choice in selected], records


class GroupJudge:
    # WHY: the model boundary. A scripted reader keeps the rows whose text carries a named
    # marker, so the pick's own rules run over real capture groups without a connection.
    def __init__(self, prefer):
        self.prefer = prefer
        self.calls = []
        self.prompts = []

    def ask(self, stage, prompt, **_kwargs):
        self.calls.append(stage)
        self.prompts.append(prompt)
        count = int(re.search(r"gets (\d+) picture", prompt)[1])
        rows = re.findall(r"^(M\d{2}) \| (.*)$", prompt, re.MULTILINE)
        return json.dumps({"keep": [label for label, row in rows if self.prefer in row][:count]})


def occasion_units(*, star="quiet-p1", stars=(), groups=("quiet", "race", "walk")):
    """A long story's three capture groups: a quiet afternoon, the race itself, the walk home."""
    starred = {star, *stars}
    captured = [
        {"asset_id": "quiet-p0", "taken": "2030-05-03T15:00:00", "moment": "quiet"},
        {"asset_id": "quiet-p1", "taken": "2030-05-03T15:04:00", "moment": "quiet"},
        {"asset_id": "race-v", "taken": "2030-05-14T10:00:00", "moment": "race", "duration": 12},
        {"asset_id": "walk-p0", "taken": "2030-05-21T18:00:00", "moment": "walk"},
        {"asset_id": "walk-p1", "taken": "2030-05-21T18:03:00", "moment": "walk"},
    ]
    return [
        unit
        | {
            "kind": "video" if unit["asset_id"] == "race-v" else "photo",
            "favourite": unit["asset_id"] in starred,
        }
        for unit in captured
        if unit["moment"] in groups
    ]


def pick_occasion(judge, *, count=1, star="quiet-p1", stars=(), groups=("quiet", "race", "walk")):
    """The real capture-group moments of one 23-day story, picked with one slot by default."""
    units = occasion_units(star=star, stars=stars, groups=groups)
    by_asset = {u["asset_id"]: u for u in units}
    records = []
    selected = pick_story_moments(
        judge,
        story={"key": "K09", "title": "The spring race", "seen": {"days": 23}},
        choices=_capture_group_moments(units, quality=lambda _asset: 1.0),
        count=count,
        starred=lambda c: any(by_asset[a]["favourite"] for a in c.members),
        contract="Show the spring",
        record=lambda name, value: records.append((name, value)),
        kind_of=lambda c: source_kind_marker(by_asset[c.primary]),
        plays=lambda c: carries_motion(by_asset[c.primary]),
    )
    return selected, records


def test_a_star_on_a_quiet_moment_does_not_settle_a_story_with_more_moments_than_slots():
    judge = GroupJudge(prefer="video")
    selected, records = pick_occasion(judge)

    assert len(judge.calls) == 2
    assert [c.key for c in selected] == ["race:cg"]
    assert selected[0].primary == "race-v"
    assert records[0][1]["orders"] == [["race:cg"], ["race:cg"]]


def test_a_star_wins_the_frame_of_the_moment_the_pick_chooses():
    judge = GroupJudge(prefer="favourite")
    selected, _ = pick_occasion(judge)

    assert len(judge.calls) == 2
    marked = [row for row in judge.prompts[0].splitlines() if "| favourite" in row]
    assert len(marked) == 1 and marked[0].startswith("M01")  # the star leads its own row
    assert [c.key for c in selected] == ["quiet:cg"]
    # the star is the second picture of its capture group; it still carries the moment
    assert selected[0].primary == "quiet-p1"


@pytest.mark.parametrize("star", ["quiet-p1", ""])
def test_a_story_with_no_more_moments_than_slots_asks_nothing_star_or_not(star):
    """One capture group and three slots: there is no moment to choose and none to decline."""
    judge = GroupJudge(prefer="video")
    selected, records = pick_occasion(judge, count=3, star=star, groups=("quiet",))

    assert judge.calls == []
    assert records == []
    assert [c.key for c in selected] == ["quiet:cg"]
    assert selected[0].primary == ("quiet-p1" if star else "quiet-p0")


def test_a_choice_between_two_favourites_is_asked_rather_than_settled():
    """Every moment starred, one slot: an ambiguous preference, not a lost favourite."""
    judge = GroupJudge(prefer="video")
    selected, _ = pick_occasion(judge, stars=("race-v", "walk-p0"))

    assert len(judge.calls) == 2
    assert [c.key for c in selected] == ["race:cg"]


def test_the_vote_decides_the_slots_that_a_run_of_favourites_cannot_reserve():
    judge = PickJudge(keep=("M03", "M02"))
    selected, _ = pick(judge, days=5, stars=("choice-1", "choice-2"))
    assert selected == ["choice-2", "choice-3"]
    assert len(judge.calls) == 2


def test_second_picture_of_same_company_yields_to_a_new_relation():
    selected, _ = pick(
        PickJudge(),
        company={
            "choice-1": "mother",
            "choice-2": "mother",
            "choice-3": "grandfather",
        },
    )
    assert selected == ["choice-1", "choice-3"]


def test_company_rule_preserves_favourites_even_when_they_show_the_same_relation():
    selected, _ = pick(
        PickJudge(),
        stars=("choice-1", "choice-2"),
        company={
            "choice-1": "mother",
            "choice-2": "mother",
            "choice-3": "grandfather",
        },
    )
    assert selected == ["choice-1", "choice-2"]


@pytest.mark.parametrize("familiar_bundle", ["parent, child", "child, parent"])
def test_familiar_combination_is_skipped_for_a_relation_absent_from_the_selected_set(
    familiar_bundle,
):
    judge = PickJudge(keep=("M01", "M02", "M03"))
    selected, records = pick(
        judge,
        count=3,
        choice_count=5,
        company={
            "choice-1": "parent",
            "choice-2": "child",
            "choice-3": "parent",
            "choice-4": familiar_bundle,
            "choice-5": "grandparent",
        },
    )

    assert selected == ["choice-1", "choice-2", "choice-5"]
    assert records[0][1]["company_replacements"] == [
        {
            "removed": "choice-3",
            "added": "choice-5",
            "new_relations": ["grandparent"],
        }
    ]
    assert records[0][1]["count"] == 3
    assert judge.calls == ["story-pick-K01-source", "story-pick-K01-reversed"]


@pytest.mark.parametrize("repeated_relations", ["child", "child, parent"])
def test_subset_or_reordered_duplicate_yields_without_losing_existing_relations(repeated_relations):
    judge = PickJudge()
    selected, records = pick(
        judge,
        company={
            "choice-1": "parent, child",
            "choice-2": repeated_relations,
            "choice-3": "grandparent",
        },
    )

    assert selected == ["choice-1", "choice-3"]
    assert records[0][1]["company_replacements"] == [
        {
            "removed": "choice-2",
            "added": "choice-3",
            "new_relations": ["grandparent"],
        }
    ]
    assert len(judge.calls) == 2


def test_new_relation_does_not_displace_a_picture_with_unique_existing_coverage():
    judge = PickJudge()
    selected, records = pick(
        judge,
        company={
            "choice-1": "parent, child",
            "choice-2": "parent, sibling",
            "choice-3": "grandparent",
        },
    )

    assert selected == ["choice-1", "choice-2"]
    assert records[0][1]["company_replacements"] == []
    assert len(judge.calls) == 2


def test_company_replacement_preserves_starred_duplicate_after_both_model_orders():
    judge = PickJudge(keep=("M01", "M02", "M03"))
    selected, records = pick(
        judge,
        days=3,
        count=3,
        choice_count=4,
        stars=("choice-2",),
        company={
            "choice-1": "parent",
            "choice-2": "parent",
            "choice-3": "child",
            "choice-4": "grandparent",
        },
    )

    assert selected == ["choice-2", "choice-3", "choice-4"]
    assert records[0][1]["company_replacements"] == [
        {
            "removed": "choice-1",
            "added": "choice-4",
            "new_relations": ["grandparent"],
        }
    ]
    assert len(judge.calls) == 2


def test_later_nonstarred_repeats_yield_first_without_expanding_the_pick():
    judge = PickJudge(keep=("M01", "M02", "M03"))
    selected, records = pick(
        judge,
        count=3,
        choice_count=5,
        company={
            "choice-1": "parent",
            "choice-2": "parent",
            "choice-3": "parent",
            "choice-4": "grandparent",
            "choice-5": "sibling",
        },
    )

    assert selected == ["choice-1", "choice-4", "choice-5"]
    assert records[0][1]["company_replacements"] == [
        {"removed": "choice-3", "added": "choice-4", "new_relations": ["grandparent"]},
        {"removed": "choice-2", "added": "choice-5", "new_relations": ["sibling"]},
    ]
    assert judge.calls == ["story-pick-K01-source", "story-pick-K01-reversed"]


def test_held_company_improvement_keeps_original_choices_and_is_checked_only_once():
    checked = []

    def reject(choice):
        checked.append(choice.key)
        return False

    judge = PickJudge()
    selected, records = pick(
        judge,
        company={
            "choice-1": "parent",
            "choice-2": "parent",
            "choice-3": "grandparent",
        },
        replacement_allowed=reject,
    )

    assert selected == ["choice-1", "choice-2"]
    assert checked == ["choice-3"]
    assert records[0][1]["company_replacements"] == []
    assert records[0][1]["company_rejected"] == ["choice-3"]
    assert len(judge.calls) == 2


def test_held_company_improvement_tries_next_available_novel_moment():
    checked = []

    def admit(choice):
        checked.append(choice.key)
        return choice.key == "choice-5"

    selected, records = pick(
        PickJudge(),
        choice_count=5,
        company={
            "choice-1": "parent",
            "choice-2": "parent",
            "choice-3": "parent",
            "choice-4": "grandparent",
            "choice-5": "sibling",
        },
        replacement_allowed=admit,
    )

    assert selected == ["choice-1", "choice-5"]
    assert checked == ["choice-4", "choice-5"]
    assert records[0][1]["company_rejected"] == ["choice-4"]
    assert records[0][1]["company_replacements"] == [
        {
            "removed": "choice-2",
            "added": "choice-5",
            "new_relations": ["sibling"],
        }
    ]


@pytest.mark.parametrize(
    "offered,grant,expected",
    [
        ([10, 10], 4, [2, 2]),
        ([10, 5], 6, [4, 2]),
        ([7, 7, 7], 5, [2, 2, 1]),
        ([120], 40, [40]),
        ([40, 40, 40], 40, [14, 13, 13]),
        ([3, 3], 0, [0, 0]),
    ],
)
def test_page_shares_follow_choice_counts_and_never_exceed_the_grant(offered, grant, expected):
    shares = page_shares(offered, grant)
    assert shares == expected
    assert sum(shares) == min(grant, sum(offered))
    assert all(share <= count for share, count in zip(shares, offered, strict=True))


def test_a_page_holding_a_favourite_keeps_one_slot_when_the_grant_allows():
    assert page_shares([90, 2], 3, favourite_pages=[1]) == [2, 1]
    assert page_shares([90, 2], 1, favourite_pages=[1]) == [1, 0]


class PagingJudge:
    # WHY: the model boundary. A scripted reader answers every page by the grant named in
    # its own prompt, so the paging and share rules are exercised without a connection.
    def __init__(self, short_on=()):
        self.calls = []
        self.short_on = set(short_on)

    def ask(self, stage, prompt, **_kwargs):
        self.calls.append({"stage": stage, "prompt": prompt})
        grant = int(re.search(r"gets (\d+) picture", prompt)[1])
        page = re.search(r"-page-(\d+)-", stage)
        offered = sorted(re.findall(r"^(M\d+) \|", prompt, re.MULTILINE), key=lambda m: int(m[1:]))
        keep = offered[: grant - (2 if page and int(page[1]) in self.short_on else 0)]
        return json.dumps(
            {
                "keep": keep,
                "unused_slots": grant - len(keep),
                "why_fewer": "The remaining views repeat the same hour."
                if len(keep) < grant
                else "",
            }
        )


def _crowded_choices(count):
    return [
        DepictedChoice(
            f"choice-{i:03d}",
            "K01",
            f"2030-05-{i // 24 + 1:02d}T{i % 24:02d}:30:00",
            "A long account of an afternoon by the canal, told again. " * 4,
            f"asset-{i:03d}",
        )
        for i in range(count)
    ]


def _pick_crowded(judge, *, count=40, choice_count=120):
    records = {}
    selected = pick_story_moments(
        judge,
        story={"key": "K01", "title": "A long month", "seen": {"days": 30}},
        choices=_crowded_choices(choice_count),
        count=count,
        starred=lambda _c: False,
        contract="Remember the month",
        record=lambda name, value: records.__setitem__(name, value),
        kind_of=lambda _c: " | facts: a walk by the canal with a long list of observations, " * 4,
    )
    return selected, records["story-pick-K01"]


def test_a_large_grant_is_asked_in_pages_that_fit_the_request_budget():
    judge = PagingJudge(short_on={1})
    selected, record = _pick_crowded(judge)

    numbered = [re.search(r"-page-(\d+)-(source|reversed)$", call["stage"]) for call in judge.calls]
    assert all(numbered), [call["stage"] for call in judge.calls]
    assert len({match[1] for match in numbered}) > 1
    assert all(len(call["prompt"]) <= PAGE_CHARS for call in judge.calls)

    assert sum(page["share"] for page in record["pages"]) == 40
    assert sum(page["offered"] for page in record["pages"]) == 120
    assert record["pages"][0]["unused_slots"] == 2
    second = next(call for call in judge.calls if "-page-2-source" in call["stage"])
    assert int(re.search(r"gets (\d+) picture", second["prompt"])[1]) == (
        record["pages"][1]["share"] + 2
    )

    assert len(selected) <= 40
    assert [c.taken for c in selected] == sorted(c.taken for c in selected)


def test_a_small_pick_is_one_request_with_unchanged_bytes():
    story = {"key": "K01", "title": "A visit", "seen": {"days": 3}}
    choices = [
        DepictedChoice(f"choice-{i}", "K01", f"2030-05-01T10:0{i}", f"View {i}", f"asset-{i}")
        for i in range(1, 10)
    ]
    judge = PagingJudge()
    records = {}
    pick_story_moments(
        judge,
        story=story,
        choices=choices,
        count=3,
        starred=lambda _c: False,
        contract="Show the visit",
        record=lambda name, value: records.__setitem__(name, value),
    )

    rows = [f"M{i:02d} | 2030-05-01T10:0{i} | View {i} | 1 picture(s)" for i in range(1, 10)]
    listings = ["\n".join(rows), "\n".join(reversed(rows))]
    assert [call["stage"] for call in judge.calls] == [
        "story-pick-K01-source",
        "story-pick-K01-reversed",
    ]
    assert [call["prompt"] for call in judge.calls] == [
        _pick_prompt(
            "Show the visit", story, listing, count=3, allow_fewer=True, sampled_motion=False
        )
        for listing in listings
    ]
    assert all("page" not in call["prompt"] for call in judge.calls)
    assert records["story-pick-K01"]["pages"] == [
        {"page": 1, "offered": 9, "share": 3, "kept": 3, "unused_slots": 0}
    ]


def moving_units(kinds):
    """One unit per moment, the way the material builder writes them: a true video keeps its
    source length, a Live Photo is live-motion only once its residual passed the discriminant."""
    return {
        f"asset-{i}": {
            "kind": kind,
            "raw_seconds": 31.76 if kind != "still" else None,
            "favourite": False,
        }
        for i, kind in enumerate(kinds, 1)
    }


def pick_moving_story(judge, *, kinds, count=1):
    """A story whose second moment holds moving material, picked with one slot by default."""
    units = moving_units(kinds)
    choices = [
        DepictedChoice(f"choice-{i}", "K01", f"2030-05-0{i}T10:00", f"View {i}", f"asset-{i}")
        for i in range(1, len(kinds) + 1)
    ]
    read = []
    selected = pick_story_moments(
        judge,
        story={"key": "K01", "title": "A visit"},
        choices=choices,
        count=count,
        starred=lambda _c: False,
        contract="Show the visit",
        record=lambda _name, _value: None,
        kind_of=lambda c: source_kind_marker(units[c.primary]),
        plays=lambda c: carries_motion(units[c.primary]),
        motion_of=lambda c: read.append(c.primary) or "A toss, a catch and a bend.",
    )
    return [c.key for c in selected], read


def offered_rows(prompt):
    return [line for line in prompt.splitlines() if re.match(r"^M\d{2} \| ", line)]


def test_a_one_slot_story_reads_the_motion_of_its_video_and_leads_the_rows_with_it():
    judge = GroupJudge(prefer="video")
    selected, read = pick_moving_story(judge, kinds=("still", "video", "still"))

    assert read == ["asset-2"]  # the grant no longer decides whether motion is evidence
    assert "Motion: A toss, a catch and a bend." in judge.prompts[0]
    assert offered_rows(judge.prompts[0])[0].startswith("M02 | ")
    assert selected == ["choice-2"]


def test_the_pick_contract_chooses_the_video_over_a_still_of_the_same_moment():
    prompt = _pick_prompt(
        "Show the visit",
        {"key": "K01", "title": "A visit"},
        "M01 | a row",
        count=1,
        allow_fewer=False,
        sampled_motion=False,
    )

    assert (
        "when a video or a playing Live Photo carries a moment, choose it over a still of "
        "the same moment" in prompt
    )


@pytest.mark.parametrize(
    "kind,marker,leads",
    [
        ("live-motion", " | live photo, motion plays", True),
        ("live-still", " | live photo, shown as a still", False),
    ],
)
def test_a_live_photo_is_offered_as_motion_only_once_it_passed_the_discriminant(
    kind, marker, leads
):
    judge = GroupJudge(prefer="View 1")
    _selected, read = pick_moving_story(judge, kinds=("still", kind, "still"))

    rows = offered_rows(judge.prompts[0])
    assert [row for row in rows if row.startswith("M02 | ")][0].endswith(marker)
    assert rows[0].startswith("M02 | ") is leads
    assert (read == ["asset-2"]) is leads  # its motion line is read only when it plays


class LastRowJudge:
    # WHY: the model boundary. The measured reader kept the last row it was shown, so the two
    # orders split whenever the moving row led one of them; this replays that split offline.
    def __init__(self):
        self.prompts = []

    def ask(self, _stage, prompt, **_kwargs):
        self.prompts.append(prompt)
        count = int(re.search(r"gets (\d+) picture", prompt)[1])
        return json.dumps({"keep": [offered_rows(prompt)[-1][:3]][:count]})


def test_a_one_slot_vote_the_two_orders_split_goes_to_the_moment_that_plays():
    judge = LastRowJudge()
    selected, _read = pick_moving_story(judge, kinds=("still", "video", "still"))

    kept = [offered_rows(prompt)[-1][:3] for prompt in judge.prompts]
    assert kept == ["M03", "M02"]  # the source order kept a still, the reversed one the video
    assert selected == ["choice-2"]


def test_a_split_vote_between_two_stills_still_follows_the_source_order():
    judge = LastRowJudge()
    selected, _read = pick_moving_story(judge, kinds=("still", "still", "still"))

    assert selected == ["choice-3"]


def _near_total_choices(count):
    """A month's worth of short rows: they all fit one request, so only the answer's size pages them."""
    return [
        DepictedChoice(
            f"choice-{i:03d}",
            "K01",
            f"2030-05-{i // 24 + 1:02d}T{i % 24:02d}:30:00",
            "A morning by the canal",
            f"asset-{i:03d}",
        )
        for i in range(count)
    ]


def _pick_near_total(judge, *, count=65, choice_count=102):
    records = {}
    selected = pick_story_moments(
        judge,
        story={"key": "K01", "title": "A long month", "seen": {"days": 30}},
        choices=_near_total_choices(choice_count),
        count=count,
        starred=lambda _c: False,
        contract="Remember the month",
        record=lambda name, value: records.__setitem__(name, value),
    )
    return selected, records["story-pick-K01"]


def test_a_page_that_would_need_more_labels_than_one_answer_holds_is_split():
    judge = PagingJudge()
    selected, record = _pick_near_total(judge)

    grants = [int(re.search(r"gets (\d+) picture", call["prompt"])[1]) for call in judge.calls]
    assert max(grants) <= MAX_LABELS_PER_ASK
    assert len(record["pages"]) > 1
    # Bytes were never the reason: every row of the story fits one request together.
    assert max(len(call["prompt"]) for call in judge.calls) * len(record["pages"]) < PAGE_CHARS * 2
    assert sum(page["offered"] for page in record["pages"]) == 102
    assert sum(page["share"] for page in record["pages"]) == 65
    assert len(selected) == 65


class OverrunJudge:
    # WHY: the model boundary. This scripted reader answers the way the 30B answered the year
    # that died: every label it was offered, again after the repair ask.
    def __init__(self):
        self.calls = []

    def ask(self, stage, prompt, **_kwargs):
        self.calls.append(stage)
        return json.dumps({"keep": offered_rows(prompt), "unused_slots": 0})


def test_a_reader_that_answers_with_every_label_still_leaves_a_film():
    judge = OverrunJudge()
    selected, record = _pick_near_total(judge)

    trims = [v for v in record["vote_records"] if v.get("review_stage") == "pick-cap-trim"]
    assert trims, record["vote_records"]
    assert all(len(trim["keep"]) <= MAX_LABELS_PER_ASK for trim in trims)
    assert len(selected) == 65
    assert [c.taken for c in selected] == sorted(c.taken for c in selected)
