"""The period story reader: episode rows survive placement, roles come from the synthesis."""

from __future__ import annotations

import json
import re

from immich_memories.analysis.editorial_story_reading import read_period_story


class ScriptedJudge:
    # WHY: stands in for the local 30B text model behind StructureTextJudge. Unit tests must
    # answer from canned strings; no unit test may open a model connection.
    def __init__(self, reply):
        self._reply = reply
        self.calls: list[dict] = []

    @property
    def asked(self):
        return self.calls

    def ask(self, stage, prompt, max_tokens=0):
        self.calls.append({"stage": stage, "prompt": prompt, "cache_hit": False})
        return self._reply(stage, prompt)

    def prompt(self, stage):
        return next(a["prompt"] for a in self.calls if a["stage"] == stage)


def episode_row(index, *, minute=None, headline=None, day="2022-06-19", moments=None, wide=False):
    """One banked 90-minute episode, in the shape story_episode_rows produces."""
    minute = index if minute is None else minute
    key = f"M{index:03d}"
    taken = f"{day}T{9 + minute // 60:02d}:{minute % 60:02d}:00"
    return {
        "episode": f"E{index:03d}",
        "episode_evidence_key": f"evidence-{index:03d}",
        "capture_group": key,
        "moments": list(moments or [key]),
        "taken": taken,
        "last_taken": taken,
        "places": "",
        "known_people_in_group": "",
        "person_links": "",
        "captures": 2,
        "favourites": 0,
        "video": 0,
        "live": 0,
        "what_happened": f"A stretch of time number {index}",
        "observations": (
            [
                f"{kind} view {number} of stretch {index} " + "x" * 200
                for number, kind in enumerate(
                    ("a first", "a second", "a third", "a starred", "another starred")
                )
            ]
            if wide
            else [headline or f"a plain view number {index}", "a second view"]
        ),
        "named_observations": 3 if wide else 1,
    }


def readings(count, start=1):
    return [f"r{number}" for number in range(start, start + count)]


def page_answer(pairs, new_episodes=()):
    return json.dumps(
        {
            "fragments": [{"reading": r, "episode": e} for r, e in pairs],
            "new_episodes": list(new_episodes),
        }
    )


def place(reading_ids, episode, new_episodes=()):
    return page_answer([(r, episode) for r in reading_ids], new_episodes)


def opened(key, title, *, role="supporting"):
    return {"id": key, "title": title, "account": "What the reader wrote about it.", "role": role}


def synthesis(priorities, *, connections=(), uncertainties=()):
    return json.dumps(
        {
            "thesis": "What this period was about.",
            "about": [],
            "connections": list(connections),
            "priorities": list(priorities),
            "uncertainties": list(uncertainties),
        }
    )


def weighing(weights, *, about=()):
    # WHY: grouping and weighing are separate model answers. These controlled weights keep
    # each test focused on its reading/role boundary without relying on an unanswered stage.
    return json.dumps({"about": list(about), "weights": weights, "join": [], "retitle": {}})


def read(judge, evidence):
    return read_period_story(judge, evidence=evidence, contract="Test contract.", prior={})


def test_facts_attach_to_every_placed_row_chronologically_and_once():
    long_headline = "a very long observation that keeps going " * 6
    evidence = [episode_row(index, minute=300 if index == 3 else index) for index in range(17)]
    evidence[0] = episode_row(0, headline=long_headline)

    def reply(stage, _prompt):
        if stage.startswith("story-weighing"):
            return weighing({"K01": "major"})
        if stage.startswith("story-episodes"):
            return place(
                readings(17), "S0001", [opened("S0001", "A long ordinary day", role="central")]
            )
        return synthesis([{"episode": "S0001", "purpose": "the spine of the period"}])

    story = read(ScriptedJudge(reply), evidence)
    facts = story.episodes[0].facts
    groups = [fact["capture_group"] for fact in facts]
    assert groups[-1] == "M003"  # taken hours later, listed last
    assert groups[:-1] == [f"M{index:03d}" for index in range(17) if index != 3]
    assert len(facts) == len({fact["reading"] for fact in facts}) == 17
    assert facts[0] == {
        "reading": "r1",
        "capture_group": "M000",
        "taken": "2022-06-19T09:00:00",
        "fact": long_headline[:160],
    }


def test_a_row_placed_into_the_same_episode_keeps_its_headline_in_the_record():
    milestone = "a single test strip on the kitchen counter, two lines"
    evidence = [episode_row(index) for index in range(16)]
    evidence.append(episode_row(16, headline=milestone))

    def reply(stage, _prompt):
        if stage.startswith("story-weighing"):
            return weighing({"K01": "major"})
        if stage.startswith("story-episodes"):
            return place(
                readings(17), "S0001", [opened("S0001", "Screens and projections at home")]
            )
        return synthesis([{"episode": "S0001", "purpose": "the only episode"}])

    record = read(ScriptedJudge(reply), evidence).as_record()
    episode = record["episodes"][0]
    assert milestone not in episode["account"]
    assert [f["fact"] for f in episode["facts"] if f["reading"] == "r17"] == [milestone]


def test_roles_come_from_weighed_stories_not_from_the_page():
    evidence = [episode_row(index) for index in range(5)]
    page_roles = ["central", "supporting", "supporting", "incidental", "central"]

    def reply(stage, _prompt):
        if stage.startswith("story-weighing"):
            return weighing({"K01": "major", "K02": "minor", "K03": "minor", "K04": "glimpse"})
        if stage.startswith("story-episodes"):
            return page_answer(
                [(f"r{index + 1}", f"S{index + 1:04d}") for index in range(5)],
                [
                    opened(f"S{index + 1:04d}", f"Occasion {index + 1}", role=page_roles[index])
                    for index in range(5)
                ],
            )
        return synthesis(
            [
                {"episode": "S0003", "purpose": "the change"},
                {"episode": "S0002", "purpose": "its setting"},
                {"episode": "S0005", "purpose": "the return"},
            ]
        )

    story = read(ScriptedJudge(reply), evidence)
    assert {e.key: e.role for e in story.episodes} == {
        "S0001": "texture",
        "S0002": "supporting",
        "S0003": "central",
        "S0004": "incidental",
        "S0005": "supporting",
    }
    assert [e.page_role for e in story.episodes] == page_roles


def test_priorities_given_as_titles_resolve_to_episode_ids():
    evidence = [episode_row(0), episode_row(1)]

    def reply(stage, _prompt):
        if stage.startswith("story-weighing"):
            return weighing({"K01": "major", "K02": "minor"})
        if stage.startswith("story-episodes"):
            return page_answer(
                [("r1", "S0001"), ("r2", "S0002")],
                [opened("S0001", "Morning at the lake"), opened("S0002", "Evening walk home")],
            )
        return synthesis(
            [
                {"title": "Morning at the lake", "purpose": "the day itself"},
                "evening walk",
                {"title": "an occasion nobody photographed"},
            ]
        )

    story = read(ScriptedJudge(reply), evidence)
    assert [row["episodes"] for row in story.priorities] == [["S0001"], ["S0002"]]
    assert story.audit["unresolved_priorities"] == 1
    assert {e.key: e.role for e in story.episodes} == {"S0001": "central", "S0002": "supporting"}


def test_ungrouped_episodes_are_weighed_and_the_gap_is_recorded():
    """A page role is not a weight. Ungrouped episodes remain available to weighing, whose
    explicit glimpse judgment makes them texture despite a central page role."""
    evidence = [episode_row(0), episode_row(1)]

    def reply(stage, _prompt):
        if stage.startswith("story-weighing"):
            return weighing({"K01": "glimpse", "K02": "glimpse"})
        if stage.startswith("story-episodes"):
            return page_answer(
                [("r1", "S0001"), ("r2", "S0002")],
                [
                    opened("S0001", "Morning at the lake", role="central"),
                    opened("S0002", "Evening walk home", role="texture"),
                ],
            )
        return synthesis([])

    story = read(ScriptedJudge(reply), evidence)
    assert {e.key: e.role for e in story.episodes} == {"S0001": "texture", "S0002": "texture"}
    assert [s["weight"] for s in story.stories] == ["glimpse", "glimpse"]
    assert all(s.get("unplaced_by_synthesis") for s in story.stories)


def test_stories_group_days_and_the_gate_weighs_what_the_synthesis_left_out():
    """A two-day story is one story with one weight; an unplaced remarkable episode is a minor
    story, an unplaced background episode is none."""
    evidence = [episode_row(index) for index in range(4)]

    def reply(stage, _prompt):
        if stage.startswith("story-weighing"):
            return weighing({"K02": "none", "K03": "none"}, about=["K01"])
        if stage.startswith("story-episodes"):
            return page_answer(
                [(f"r{index + 1}", f"S{index + 1:04d}") for index in range(4)],
                [
                    opened("S0001", "Arrival at the coast"),
                    opened("S0002", "Second day at the coast"),
                    opened("S0003", "Cafe visit"),
                    opened("S0004", "Kitchen evening"),
                ],
            )
        return json.dumps(
            {
                "thesis": "A week at the coast.",
                "about": ["S0001", "S0002"],
                "stories": [
                    {
                        "title": "Coast holiday",
                        "episodes": ["S0001", "S0002"],
                        "purpose": "the holiday",
                    }
                ],
                "uncertainties": [],
            }
        )

    hints = {"S0003": {"gate": "remarkable"}, "S0004": {"gate": "background"}}
    story = read_period_story(
        ScriptedJudge(reply),
        evidence=evidence,
        contract="Test contract.",
        prior={},
        enrich=lambda _episodes: hints,
    )
    weights = {tuple(s["episodes"]): s["weight"] for s in story.stories}
    assert weights == {("S0001", "S0002"): "dominant", ("S0003",): "minor", ("S0004",): "none"}
    assert {e.key: e.role for e in story.episodes} == {
        "S0001": "central",
        "S0002": "central",
        "S0003": "supporting",
        "S0004": "incidental",
    }
    assert story.priorities[0]["episodes"] == ["S0001", "S0002"]


def test_merging_central_stories_compacts_question_labels_and_candidate_references():
    days = ("2022-06-01", "2022-06-02", "2022-06-06", "2022-06-10")
    evidence = [episode_row(index, day=day) for index, day in enumerate(days)]

    def reply(stage, prompt):
        if stage.startswith("story-episodes"):
            return page_answer(
                [(f"r{index + 1}", f"S{index + 1:04d}") for index in range(4)],
                [
                    opened(f"S{index + 1:04d}", title)
                    for index, title in enumerate(
                        ("Arrival", "Second day", "Cafe visit", "Celebration")
                    )
                ],
            )
        if stage.startswith("story-understanding"):
            return json.dumps(
                {
                    "thesis": "A stay and a later celebration.",
                    "about": ["S0001", "S0002", "S0004"],
                    "stories": [
                        {
                            "title": f"Occasion {index}",
                            "episodes": [f"S{index:04d}"],
                            "purpose": "A distinct occasion",
                        }
                        for index in range(1, 5)
                    ],
                    "uncertainties": [],
                }
            )
        assert stage.startswith("story-weighing")
        assert set(re.findall(r"^(K\d+) \|", prompt, re.MULTILINE)) == {"K01", "K02", "K03"}
        assert "THE READING SAYS THIS MEMORY IS ABOUT: K01, K03\n" in prompt
        return weighing({"K02": "minor", "K03": "major"}, about=["K01"])

    story = read_period_story(
        ScriptedJudge(reply),
        evidence=evidence,
        contract="Test contract.",
        prior={},
        enrich=lambda _: {f"S{index + 1:04d}": {"day": day} for index, day in enumerate(days)},
    )
    assert {s["key"]: s["episodes"] for s in story.stories} == {
        "K01": ["S0001", "S0002"],
        "K02": ["S0003"],
        "K03": ["S0004"],
    }
    assert [s["weight"] for s in story.stories] == ["dominant", "minor", "major"]


def test_synthesis_cards_carry_bounded_facts_with_the_omitted_count():
    evidence = [episode_row(index) for index in range(20)]

    def reply(stage, _prompt):
        if stage.startswith("story-weighing"):
            return weighing({"K01": "major"})
        if stage.startswith("story-episodes"):
            return place(readings(20), "S0001", [opened("S0001", "One long stretch")])
        return synthesis([{"episode": "S0001", "purpose": "everything"}])

    judge = ScriptedJudge(reply)
    read(judge, evidence)
    prompt = judge.prompt("story-understanding-1")
    cards = json.loads(prompt.split("remarkable, maybe or background)\n", 1)[1].split("\n", 1)[0])
    assert len(cards) == 1
    assert len(cards[0]["facts"]) == 12
    assert cards[0]["facts_omitted"] == 8
    assert cards[0]["facts"][0].endswith("a plain view number 0")
    assert cards[0]["facts"][-1].endswith("a plain view number 19")
    assert "belongs to exactly one story" in prompt
    assert 'The thesis and "about" express the same decision' in prompt
    assert "If no single story stands out" in prompt
    weighing_prompt = judge.prompt("story-weighing-source")
    assert "Assess all 1 stories" in weighing_prompt
    assert "Do not return a sample answer" in weighing_prompt


def test_regrouping_can_withdraw_the_rejected_answers_central_story():
    evidence = [episode_row(0, day="2030-03-01"), episode_row(1, day="2030-03-04")]

    def reply(stage, _prompt):
        if stage.startswith("story-episodes"):
            return page_answer(
                [("r1", "S0001"), ("r2", "S0002")],
                [opened("S0001", "First outing"), opened("S0002", "Second outing")],
            )
        if stage == "story-understanding-1":
            return json.dumps(
                {
                    "thesis": "One long outing.",
                    "about": ["S0001"],
                    "stories": [{"title": "One outing", "episodes": ["S0001", "S0002"]}],
                }
            )
        if stage == "story-understanding-1-try2":
            return json.dumps(
                {
                    "thesis": "Two separate outings; neither dominates.",
                    "about": [],
                    "stories": [
                        {"title": title, "episodes": [key]}
                        for key, title in [("S0001", "First outing"), ("S0002", "Second outing")]
                    ],
                }
            )
        return weighing({"K01": "minor", "K02": "minor"})

    judge = ScriptedJudge(reply)
    result = read_period_story(
        judge,
        evidence=evidence,
        contract="Several occasions.",
        prior={},
        enrich=lambda _episodes: {
            key: {"day": day, "moments": 1, "pictures": 1, "gate": "maybe"}
            for key, day in [("S0001", "2030-03-01"), ("S0002", "2030-03-04")]
        },
    )

    assert "THE READING SAYS THIS MEMORY IS ABOUT: nothing in particular" in judge.prompt(
        "story-weighing-source"
    )
    assert [row["weight"] for row in result.stories] == ["minor", "minor"]
    assert result.thesis == "Two separate outings; neither dominates."


def test_weighing_takes_the_heavier_order_and_floors_remarkable_or_starred_stories():
    """Source order says none, reversed says major: major stands. A remarkable day the model
    weighed none is at least minor; so is a day holding a favourite."""
    evidence = [episode_row(index) for index in range(3)]

    def reply(stage, prompt):
        if stage.startswith("story-episodes"):
            return page_answer(
                [("r1", "S0001"), ("r2", "S0002"), ("r3", "S0003")],
                [
                    opened("S0001", "Race day"),
                    opened("S0002", "Kitchen evening"),
                    opened("S0003", "Garden afternoon"),
                ],
            )
        if stage.startswith("story-understanding"):
            return json.dumps(
                {
                    "thesis": "A month.",
                    "stories": [
                        {"title": "Race day", "episodes": ["S0001"], "purpose": "the race"},
                        {"title": "Kitchen evening", "episodes": ["S0002"], "purpose": ""},
                    ],
                    "uncertainties": [],
                }
            )
        if stage == "story-weighing-source":
            assert "K03" in prompt, "the unplaced garden afternoon must be on the table"
            return weighing({"K01": "none", "K02": "none", "K03": "none"})
        if stage == "story-weighing-reversed":
            return weighing({"K01": "major", "K02": "none", "K03": "none"})
        raise AssertionError(stage)

    hints = {
        "S0001": {"day": "2030-05-02", "gate": "remarkable", "favourites": 0},
        "S0002": {"day": "2030-05-03", "gate": "background", "favourites": 0},
        "S0003": {"day": "2030-05-09", "gate": "background", "favourites": 1},
    }
    story = read_period_story(
        ScriptedJudge(reply),
        evidence=evidence,
        contract="Test contract.",
        prior={},
        enrich=lambda _episodes: hints,
    )
    weights = {tuple(s["episodes"]): s["weight"] for s in story.stories}
    assert weights == {("S0001",): "major", ("S0002",): "none", ("S0003",): "minor"}


def test_gaps_are_kept_for_a_subject_memory_and_split_elsewhere():
    """Four weekend episodes of one project over two months: one story when the memory is about
    a subject (allow_gaps), split into its days for a month."""
    days = {
        "S0001": "2017-03-04",
        "S0002": "2017-03-18",
        "S0003": "2017-04-08",
        "S0004": "2017-04-22",
    }
    evidence = [episode_row(index, day=day) for index, day in enumerate(days.values())]

    def reply(stage, prompt):
        if stage.startswith("story-episodes"):
            offered = re.findall(r'"reading": "(r\d+)"', prompt)
            return page_answer(
                [(reading, f"S{index + 1:04d}") for index, reading in enumerate(offered)],
                [
                    opened(f"S{index + 1:04d}", f"Occasion {index + 1}")
                    for index in range(len(offered))
                ],
            )
        if stage.startswith("story-understanding"):
            return json.dumps(
                {
                    "thesis": "Works.",
                    "stories": [
                        {
                            "title": "Kitchen works",
                            "episodes": ["S0001", "S0002", "S0003", "S0004"],
                            "purpose": "the works",
                        }
                    ],
                    "uncertainties": [],
                }
            )
        keys = re.findall(r"^(K\d{2}) \|", prompt, re.MULTILINE)
        return weighing(dict.fromkeys(keys, "major"))

    hints = {key: {"day": day} for key, day in days.items()}
    subject = read_period_story(
        ScriptedJudge(reply),
        evidence=evidence,
        contract="c",
        prior={},
        enrich=lambda _e: hints,
        allow_gaps=True,
    )
    assert [tuple(s["episodes"]) for s in subject.stories] == [("S0001", "S0002", "S0003", "S0004")]
    month = read_period_story(
        ScriptedJudge(reply),
        evidence=[episode_row(index, day=day) for index, day in enumerate(days.values())],
        contract="c",
        prior={},
        enrich=lambda _e: hints,
    )
    assert sorted(len(s["episodes"]) for s in month.stories) == [1, 1, 1, 1]


def _card(episode, *, what_happened="", representatives=(), evidence_key="k"):
    from immich_memories.analysis.editorial_structure_contract import EpisodeReadingCard

    return EpisodeReadingCard(episode, evidence_key, what_happened, tuple(representatives), False)


def _moment(alias, taken, **fields):
    row = {
        "moment_id": alias,
        "taken": taken,
        "places": "",
        "people": "",
        "person_links": "",
        "visuals": 1,
        "favorites": 0,
        "video": 0,
        "live": 0,
        "episode_context": "",
    }
    return row | fields


def _place_all(stage, prompt):
    """Put every offered row of every page into one episode of that page."""
    if stage.startswith("story-episodes"):
        return place(
            re.findall(r'"reading": "(r\d+)"', prompt), "S0001", [opened("S0001", "An occasion")]
        )
    if stage.startswith("story-weighing"):
        return weighing(dict.fromkeys(re.findall(r"^(K\d+) \|", prompt, re.MULTILINE), "minor"))
    if stage.startswith("story-understanding"):
        return synthesis([])
    return synthesis([])


def test_every_wall_episode_becomes_one_row_with_its_moments_places_and_people():
    """One row per canonical episode: its moments, its place names, its people, its numbers."""
    from immich_memories.analysis.editorial_story_reading import story_episode_rows

    moments = [
        _moment(
            "M001",
            "2030-05-02T08:00:00",
            places="L01:Canal du Centre",
            people="P01:name=Alex|relationship=partner",
            person_links="P01-partner->P02|source=people file",
            visuals=3,
            favorites=1,
            live=1,
            episode_context="A short line.",
        ),
        _moment(
            "M002",
            "2030-05-02T09:00:00",
            places="L01:Canal du Centre;L02:Old bridge",
            people="P02:name=Sam|relationship=owner",
            visuals=2,
            video=1,
            episode_context="A short line.",
        ),
        _moment("M003", "2030-05-03T08:00:00"),
    ]
    rows = story_episode_rows(
        moments,
        readings={
            "M001": _card(
                "episode-1", what_happened="A walk along the canal.", representatives=("a1",)
            ),
            "M002": _card(
                "episode-1", what_happened="A walk along the canal.", representatives=("a1", "a2")
            ),
            "M003": _card("episode-2", representatives=("a4",)),
        },
        sources={"M001": ["a1", "a2"], "M002": ["a3"], "M003": ["a4"]},
        lines={"a1": "A view of the water", "a2": "A view of the bridge", "a4": "A later view"},
        favourite=lambda asset: asset == "a2",
    )

    assert [row["episode"] for row in rows] == ["episode-1", "episode-2"]
    first = rows[0]
    assert first["moments"] == ["M001", "M002"]
    assert first["capture_group"] == "M001"
    assert first["places"] == "Canal du Centre; Old bridge"
    assert first["known_people_in_group"] == (
        "P01:name=Alex|relationship=partner;P02:name=Sam|relationship=owner"
    )
    assert first["person_links"] == "P01-partner->P02|source=people file"
    assert (first["captures"], first["favourites"], first["video"], first["live"]) == (5, 1, 1, 1)
    assert first["what_happened"] == "A walk along the canal."
    assert first["observations"] == ["A view of the water", "A view of the bridge"]
    assert (first["taken"], first["last_taken"]) == ("2030-05-02T08:00:00", "2030-05-02T09:00:00")
    # nothing read the second episode: a factual line, never an invisible row
    assert rows[1]["what_happened"] == "1 captures on 2030-05-03"
    assert rows[1]["observations"] == ["A later view"]


def test_a_row_shows_place_names_and_never_a_wall_alias():
    from immich_memories.analysis.editorial_story_reading import _prompt_row, story_episode_rows

    rows = story_episode_rows(
        [_moment("M017", "2030-05-02T08:00:00", places="L03:Jette", episode_context="At home.")],
        readings={"M017": _card("episode-9", representatives=("a1",))},
        sources={"M017": ["a1"]},
        lines={"a1": "A view of the garden"},
        favourite=lambda _asset: False,
    )
    rendered = json.dumps(_prompt_row(rows[0] | {"reading": "r1"}, shared_year=2030))

    assert "Jette" in rendered
    assert not re.search(r"\b[MEL]\d{2,}\b", rendered)
    assert rows[0]["what_happened"] == "At home."


def test_observations_stop_at_three_representatives_and_two_favourites():
    from immich_memories.analysis.editorial_story_reading import story_episode_rows

    lines = {f"a{index}": f"description number {index} " + "x" * 200 for index in range(9)}
    rows = story_episode_rows(
        [_moment("M001", "2030-05-02T08:00:00")],
        readings={"M001": _card("episode-1", representatives=tuple(f"a{i}" for i in range(5)))},
        sources={"M001": [f"a{i}" for i in range(9)]},
        lines=lines,
        favourite=lambda asset: asset in {"a5", "a6", "a7"},
    )

    observations = rows[0]["observations"]
    assert len(observations) == 5
    assert [line[:22] for line in observations] == [
        "description number 0 x",
        "description number 1 x",
        "description number 2 x",
        "description number 5 x",
        "description number 6 x",
    ]
    assert all(len(line) == 160 for line in observations)
    assert rows[0]["named_observations"] == 3


def test_a_month_is_one_page_and_never_splits_a_day():
    """A calendar month is the page. A day too big for one keeps its rows and drops lines."""
    from immich_memories.analysis.editorial_story_reading import month_pages

    january = [episode_row(index, day=f"2030-01-{index + 1:02d}") for index in range(6)]
    crowded = [
        episode_row(100 + index, day="2030-02-14", minute=index * 30, wide=True)
        for index in range(20)
    ]

    book = month_pages(january + crowded)

    assert [(page.month, page.part, len(page.rows)) for page in book] == [
        ("2030-01", 0, 6),
        ("2030-02", 0, 20),
    ]
    assert [page.stage for page in book] == ["story-episodes-2030-01", "story-episodes-2030-02"]
    assert (book[0].trimmed, book[1].trimmed) == (False, True)
    # the two favourites' lines went first, then the third representative
    assert {len(row["observations"]) for row in book[1].rows} == {2}
    assert {len(row["observations"]) for row in book[0].rows} == {2}  # untouched, it fits
    assert [row["reading"] for row in book[0].rows] == readings(6)


def test_a_month_too_large_for_one_request_is_cut_between_days():
    from immich_memories.analysis.editorial_story_reading import month_pages

    busy = [
        episode_row(index, day=f"2030-03-{index // 2 + 1:02d}", minute=index) for index in range(40)
    ]

    book = month_pages(busy)

    assert [page.part for page in book] == [1, 2, 3]
    assert sum(len(page.rows) for page in book) == 40
    days = [{row["taken"][:10] for row in page.rows} for page in book]
    assert not days[0] & days[1] and not days[1] & days[2]
    assert [row["reading"] for row in book[1].rows][:2] == ["r1", "r2"]


def test_a_page_prompt_is_the_same_bytes_alone_or_inside_a_year():
    """Nothing run-scoped reaches a month's prompt, so the judgment bank answers February
    again whether the memory asked for February or for the whole year."""

    def year():
        return [episode_row(index, day=f"2030-{index + 1:02d}-1{index % 5}") for index in range(12)]

    def prompt_of(evidence):
        judge = ScriptedJudge(_place_all)
        read(judge, evidence)
        return judge.prompt("story-episodes-2030-02")

    whole = prompt_of(year())
    alone = prompt_of([row for row in year() if row["taken"].startswith("2030-02")])

    assert whole == alone
    assert "Test contract." not in whole
    assert "OPEN EPISODES" not in whole
    assert whole.count("(2030-02)") == 1


def test_global_keys_are_minted_in_page_order():
    """Every page numbers from S0001; the ledger gives the global key when the page lands."""
    evidence = [episode_row(index, day=f"2030-0{index + 1}-05") for index in range(3)]
    judge = ScriptedJudge(_place_all)

    story = read(judge, evidence)

    assert [episode.key for episode in story.episodes] == ["S0001", "S0002", "S0003"]
    assert [episode.moments for episode in story.episodes] == [["M000"], ["M001"], ["M002"]]
    assert all(
        "Number new episodes S0001, S0002," in judge.prompt(f"story-episodes-2030-0{month}")
        for month in (1, 2, 3)
    )


def test_a_multi_day_answer_is_split_by_the_day_rule():
    """A month page can file a whole week into one episode; one episode is still one day."""
    evidence = [episode_row(index, day=f"2030-04-0{index + 1}") for index in range(3)]

    def reply(stage, prompt):
        if stage.startswith("story-episodes"):
            return place(readings(3), "S0001", [opened("S0001", "A week of works")])
        return _place_all(stage, prompt)

    story = read(ScriptedJudge(reply), evidence)

    assert [episode.key for episode in story.episodes] == ["S0001", "S0002", "S0003"]
    assert {episode.title for episode in story.episodes} == {"A week of works"}
    assert [episode.moments for episode in story.episodes] == [["M000"], ["M001"], ["M002"]]


def test_omitted_rows_are_reasked_in_their_month_then_kept_visible():
    """A row the reader keeps skipping is re-asked inside its own month, never in the next."""
    evidence = [episode_row(0, day="2030-05-02"), episode_row(1, day="2030-06-02")]

    def reply(stage, prompt):
        if stage.startswith("story-episodes-2030-05"):
            return page_answer([])
        return _place_all(stage, prompt)

    judge = ScriptedJudge(reply)
    story = read(judge, evidence)

    assert [
        call["stage"] for call in judge.calls if call["stage"].startswith("story-episodes")
    ] == [
        "story-episodes-2030-05",
        "story-episodes-2030-05-again-1",
        "story-episodes-2030-05-again-2",
        "story-episodes-2030-06",
    ]
    unplaced = next(e for e in story.episodes if e.title == "Unplaced source episodes")
    assert unplaced.moments == ["M000"]
    assert story.audit["pages"][0]["unplaced"] == ["r1"]
    assert story.audit["reading_calls"]["retries"] == 2


def test_the_record_reports_pages_with_month_key_and_cache_hit():
    evidence = [episode_row(0, day="2030-07-02"), episode_row(1, day="2030-08-02")]
    judge = ScriptedJudge(_place_all)

    story = read(judge, evidence)

    recorded = story.audit["pages"]
    assert [(row["month"], row["part"], row["stage"], row["rows"]) for row in recorded] == [
        ("2030-07", 0, "story-episodes-2030-07", 1),
        ("2030-08", 0, "story-episodes-2030-08", 1),
    ]
    assert all(len(row["evidence_key"]) == 64 for row in recorded)
    assert len({row["evidence_key"] for row in recorded}) == 2
    assert [row["cache_hit"] for row in recorded] == [False, False]
    assert story.audit["reading_calls"] == {"pages": 2, "fresh": 2, "banked": 0, "retries": 0}


def test_lenient_object_closes_an_object_the_model_left_open_before_a_closing_bracket():
    from immich_memories.analysis.editorial_story_replies import _lenient_object

    raw = (
        '{"thesis":"t","stories":[{"title":"A","episodes":["S0001"],"purpose":"p"},'
        '{"title":"B","episodes":["S0002"],"purpose":"q"],"uncertainties":[]}'
    )
    obj = _lenient_object(raw)
    assert [s["title"] for s in obj["stories"]] == ["A", "B"]
    assert obj["uncertainties"] == []


def test_relations_on_a_line_are_the_people_files_words_without_names():
    from immich_memories.analysis.editorial_story_replies import relations_on

    line = (
        "2024-02-18 17:15+00:00 | A woman holds a baby | at Somewhere | with Riley Example (partner; aged 33; inner circle); "
        "Bob Example (library owner (inferred); aged 36; inner circle); Carla Example (grandmother; aged 68; family) | children=yes"
    )
    assert relations_on(line) == ["partner", "library owner (inferred)", "grandmother"]
    assert relations_on("2024-02-18 | A landscape | children=no") == []


def test_one_uncertainty_where_a_list_belongs_is_one_row_not_its_letters():
    """A bare string is iterable, so the old shape check turned one sentence into 44 rows."""
    from immich_memories.analysis.editorial_story_replies import _read_synthesis

    reply = json.dumps(
        {
            "thesis": "What this period was about.",
            "about": [],
            "connections": [],
            "priorities": [],
            "stories": [],
            "uncertainties": "The cafe visit may belong to the coast week.",
        }
    )

    read = _read_synthesis(reply, {"S0001": "Arrival at the coast"})

    assert read["uncertainties"] == ["The cafe visit may belong to the coast week."]
