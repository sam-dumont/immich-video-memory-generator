"""The period story reader: fragment facts survive placement, roles come from the synthesis."""

from __future__ import annotations

import json
import re

from immich_memories.analysis.editorial_story_reading import read_period_story


class ScriptedJudge:
    # WHY: stands in for the local 30B text model behind StructureTextJudge. Unit tests must
    # answer from canned strings; no unit test may open a model connection.
    def __init__(self, reply):
        self._reply = reply
        self.asked: list[dict] = []

    def ask(self, stage, prompt, max_tokens=0):
        self.asked.append({"stage": stage, "prompt": prompt})
        return self._reply(stage, prompt)

    def prompt(self, stage):
        return next(a["prompt"] for a in self.asked if a["stage"] == stage)


def fragment(index, *, minute=None, headline=None):
    minute = index if minute is None else minute
    key = f"M{index:03d}"
    return {
        "reading": f"{key}/1",
        "capture_group": key,
        "taken": f"2022-06-19T{9 + minute // 60:02d}:{minute % 60:02d}:00",
        "known_people_in_group": "",
        "places": "",
        "episode_context": "",
        "observations": [headline or f"a plain view number {index}", "a second view"],
    }


def page_answer(pairs, new_episodes=()):
    return json.dumps(
        {
            "fragments": [{"reading": r, "episode": e} for r, e in pairs],
            "new_episodes": list(new_episodes),
        }
    )


def place(readings, episode, new_episodes=()):
    return page_answer([(r, episode) for r in readings], new_episodes)


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


def test_facts_attach_to_every_placed_fragment_chronologically_and_once():
    long_headline = "a very long observation that keeps going " * 6
    evidence = [fragment(i, minute=300 if i == 3 else i) for i in range(17)]
    evidence[0] = fragment(0, headline=long_headline)
    evidence.append(evidence[0])  # the same fragment offered again on the next page

    def reply(stage, _prompt):
        if stage.startswith("story-weighing"):
            return weighing({"K01": "major"})
        if stage == "story-episodes-1":
            return place(
                [f"M{i:03d}/1" for i in range(16)],
                "S0001",
                [opened("S0001", "A long ordinary day", role="central")],
            )
        if stage == "story-episodes-2":
            return place(["M016/1", "M000/1"], "S0001")
        return synthesis([{"episode": "S0001", "purpose": "the spine of the period"}])

    story = read(ScriptedJudge(reply), evidence)
    facts = story.episodes[0].facts
    readings = [f["reading"] for f in facts]
    assert readings[-1] == "M003/1"  # taken hours later, listed last
    assert readings[:-1] == [f"M{i:03d}/1" for i in range(17) if i != 3]
    assert readings.count("M000/1") == 1
    assert facts[0] == {
        "reading": "M000/1",
        "capture_group": "M000",
        "taken": "2022-06-19T09:00:00",
        "fact": long_headline[:160],
    }


def test_a_fragment_placed_into_an_open_episode_keeps_its_headline_in_the_record():
    milestone = "a single test strip on the kitchen counter, two lines"
    evidence = [fragment(i) for i in range(16)] + [fragment(16, headline=milestone)]

    def reply(stage, _prompt):
        if stage.startswith("story-weighing"):
            return weighing({"K01": "major"})
        if stage == "story-episodes-1":
            return place(
                [f"M{i:03d}/1" for i in range(16)],
                "S0001",
                [opened("S0001", "Screens and projections at home")],
            )
        if stage == "story-episodes-2":
            return place(["M016/1"], "S0001")  # continues an episode already open
        return synthesis([{"episode": "S0001", "purpose": "the only episode"}])

    record = read(ScriptedJudge(reply), evidence).as_record()
    episode = record["episodes"][0]
    assert milestone not in episode["account"]
    assert [f["fact"] for f in episode["facts"] if f["reading"] == "M016/1"] == [milestone]


def test_roles_come_from_weighed_stories_not_from_the_page():
    evidence = [fragment(i) for i in range(5)]
    page_roles = ["central", "supporting", "supporting", "incidental", "central"]

    def reply(stage, _prompt):
        if stage.startswith("story-weighing"):
            return weighing({"K01": "major", "K02": "minor", "K03": "minor", "K04": "glimpse"})
        if stage == "story-episodes-1":
            return page_answer(
                [(f"M{i:03d}/1", f"S{i + 1:04d}") for i in range(5)],
                [
                    opened(f"S{i + 1:04d}", f"Occasion {i + 1}", role=page_roles[i])
                    for i in range(5)
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
    evidence = [fragment(0), fragment(1)]

    def reply(stage, _prompt):
        if stage.startswith("story-weighing"):
            return weighing({"K01": "major", "K02": "minor"})
        if stage == "story-episodes-1":
            return page_answer(
                [("M000/1", "S0001"), ("M001/1", "S0002")],
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
    evidence = [fragment(0), fragment(1)]

    def reply(stage, _prompt):
        if stage.startswith("story-weighing"):
            return weighing({"K01": "glimpse", "K02": "glimpse"})
        if stage == "story-episodes-1":
            return page_answer(
                [("M000/1", "S0001"), ("M001/1", "S0002")],
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
    """A two-day story is one story with one weight; an unplaced remarkable episode is a minor story,
    an unplaced background episode is none."""
    evidence = [fragment(0), fragment(1), fragment(2), fragment(3)]

    def reply(stage, _prompt):
        if stage.startswith("story-weighing"):
            return weighing({"K02": "none", "K03": "none"}, about=["K01"])
        if stage == "story-episodes-1":
            return page_answer(
                [
                    ("M000/1", "S0001"),
                    ("M001/1", "S0002"),
                    ("M002/1", "S0003"),
                    ("M003/1", "S0004"),
                ],
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
    evidence = [fragment(i) for i in range(4)]
    days = ("2022-06-01", "2022-06-02", "2022-06-06", "2022-06-10")
    for row, day in zip(evidence, days, strict=True):
        row["taken"] = day + "T12:00:00"

    def reply(stage, prompt):
        if stage == "story-episodes-1":
            return page_answer(
                [(f"M{i:03d}/1", f"S{i + 1:04d}") for i in range(4)],
                [
                    opened(f"S{i + 1:04d}", title)
                    for i, title in enumerate(
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
                            "title": f"Occasion {i}",
                            "episodes": [f"S{i:04d}"],
                            "purpose": "A distinct occasion",
                        }
                        for i in range(1, 5)
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
        enrich=lambda _: {f"S{i + 1:04d}": {"day": day} for i, day in enumerate(days)},
    )
    assert {s["key"]: s["episodes"] for s in story.stories} == {
        "K01": ["S0001", "S0002"],
        "K02": ["S0003"],
        "K03": ["S0004"],
    }
    assert [s["weight"] for s in story.stories] == ["dominant", "minor", "major"]


def test_synthesis_cards_carry_bounded_facts_with_the_omitted_count():
    evidence = [fragment(i) for i in range(20)]

    def reply(stage, _prompt):
        if stage.startswith("story-weighing"):
            return weighing({"K01": "major"})
        if stage == "story-episodes-1":
            return place(
                [f"M{i:03d}/1" for i in range(16)], "S0001", [opened("S0001", "One long stretch")]
            )
        if stage == "story-episodes-2":
            return place([f"M{i:03d}/1" for i in range(16, 20)], "S0001")
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
    evidence = [fragment(0), fragment(1)]
    evidence[0]["taken"] = "2030-03-01T10:00:00"
    evidence[1]["taken"] = "2030-03-04T10:00:00"

    def reply(stage, _prompt):
        if stage == "story-episodes-1":
            return page_answer(
                [("M000/1", "S0001"), ("M001/1", "S0002")],
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
    """Source order says none, reversed says major: major stands. A remarkable day the model weighed
    none is at least minor; so is a day holding a favourite. Unplaced episodes are weighed too."""
    evidence = [fragment(0), fragment(1), fragment(2)]

    def reply(stage, prompt):
        if stage == "story-episodes-1":
            return page_answer(
                [("M000/1", "S0001"), ("M001/1", "S0002"), ("M002/1", "S0003")],
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


def test_people_relationships_survive_caption_reading_and_story_summaries():
    from immich_memories.analysis.editorial_story_reading import story_evidence_rows

    people = (
        "P1:name=Taylor Example|relationship=friend|source=confirmed;"
        "P2:name=Casey Example|relationship=child|source=confirmed"
    )
    links = "P2-child-of->P3|source=confirmed;P2-cousin-of->P4|source=derived"
    evidence = story_evidence_rows(
        [
            {
                "moment_id": "M001",
                "taken": "2030-02-05T12:00:00",
                "people": people,
                "person_links": links,
            }
        ],
        sources={"M001": ["picture"]},
        annotations={},
        lines={"picture": "A mother cuddles her baby."},
    )

    def reply(stage, _prompt):
        if stage.startswith("story-weighing"):
            return weighing({"K01": "minor"})
        if stage == "story-episodes-1":
            return place(["M001/1"], "S0001", [opened("S0001", "A friend visits")])
        return synthesis([{"episode": "S0001", "purpose": "A visit"}])

    judge = ScriptedJudge(reply)
    story = read(judge, evidence)

    assert evidence[0]["person_links"] == links
    assert story.episodes[0].facts[0]["known_people_in_group"] == people
    assert story.episodes[0].facts[0]["person_links"] == links
    # Every reader gets the authoritative graph even when its visual caption
    # has guessed the wrong relationship; summaries must not erase that correction.
    for call in judge.asked:
        assert people in call["prompt"], call["stage"]
        assert links in call["prompt"], call["stage"]


def test_gaps_are_kept_for_a_subject_memory_and_split_elsewhere():
    """Four weekend episodes of one project over two months: one story when the memory is about a
    subject (allow_gaps), split into its days for a month, where a week of one activity around an
    unrelated day is the reader folding days together."""
    evidence = [fragment(i) for i in range(4)]
    days = {
        "S0001": "2017-03-04",
        "S0002": "2017-03-18",
        "S0003": "2017-04-08",
        "S0004": "2017-04-22",
    }

    def reply(stage, prompt):
        if stage == "story-episodes-1":
            return page_answer(
                [(f"M00{i}/1", f"S000{i + 1}") for i in range(4)],
                [opened(f"S000{i + 1}", f"Occasion {i + 1}") for i in range(4)],
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

    hints = {k: {"day": d} for k, d in days.items()}
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
        ScriptedJudge(reply), evidence=evidence, contract="c", prior={}, enrich=lambda _e: hints
    )
    assert sorted(len(s["episodes"]) for s in month.stories) == [1, 1, 1, 1]


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
