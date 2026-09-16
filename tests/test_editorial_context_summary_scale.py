"""Full episode evidence is read once, then its account carries the meaning forward."""

import json

from tests.test_editorial_story_reading import ScriptedJudge, fragment, opened, place, read


def test_a_long_contextual_episode_uses_its_account_in_later_decisions():
    account = "A friend visits during a long stay; the visitor is not a parent."
    people = "P1:name=Taylor Example|relationship=friend|source=confirmed"
    evidence = [
        {
            **fragment(index),
            "known_people_in_group": people,
            "episode_context": f"Full surrounding evidence {index}: " + "A recorded detail. " * 60,
        }
        for index in range(64)
    ]

    def reply(stage, prompt):
        assert len(prompt) < 40_000, stage
        if stage.startswith("story-episodes"):
            rows = json.JSONDecoder().raw_decode(
                prompt.split("NEW FRAGMENTS TO PLACE", 1)[1].lstrip()
            )[0]
            assert all(
                row["episode_context"].startswith("Full surrounding evidence") for row in rows
            )
            first = stage == "story-episodes-1"
            return place(
                [row["reading"] for row in rows],
                "S0001",
                [{**opened("S0001", "A long stay"), "account": account}] if first else [],
            )
        assert people in prompt
        assert "Full surrounding evidence" not in prompt
        if stage.startswith("story-understanding"):
            assert account in prompt
            return json.dumps(
                {
                    "thesis": account,
                    "about": ["S0001"],
                    "stories": [
                        {"title": "A long stay", "episodes": ["S0001"], "purpose": account}
                    ],
                    "uncertainties": [],
                }
            )
        assert account in prompt
        return '{"about":["K01"],"weights":{},"join":[],"retitle":{}}'

    judge = ScriptedJudge(reply)
    story = read(judge, evidence)

    assert len(story.episodes[0].facts) == 64
    assert all("episode_context" in fact for fact in story.episodes[0].facts)
    assert story.episodes[0].account == account
    assert all(
        call["prompt"].count(people) == 1
        for call in judge.asked
        if call["stage"].startswith("story-weighing")
    )
