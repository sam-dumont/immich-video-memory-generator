"""Literal scenes and source-known relationships reach the first importance judgment."""

from immich_memories.analysis.editorial_episode_documents import anchor_observations


def test_baby_relationship_and_age_are_preserved_without_inventing_a_first():
    rows = {
        "M001": {
            "evidence_1_description": "Two babies lie next to each other on a blanket.",
            "people": "P01:name=Child A|relationship=son|age=3 months;P02:name=Child B|relationship=nephew|age=1 month",
        }
    }
    line = anchor_observations(["M001"], rows)
    assert "Two babies lie next to each other" in line
    assert "Child A (son, age 3 months)" in line
    assert "Child B (nephew, age 1 month)" in line
    assert "first" not in line.lower()


def test_long_anchor_samples_its_span_instead_of_only_the_first_fragment():
    rows = {f"M{i}": {"evidence_1_description": f"Scene number {i}."} for i in range(7)}
    line = anchor_observations(list(rows), rows)
    assert "Scene number 0." in line and "Scene number 3." in line and "Scene number 6." in line
    assert "Scene number 1." not in line


def test_missing_people_do_not_become_an_invented_relationship():
    line = anchor_observations(["M1"], {"M1": {"evidence_1_description": "A person holds a box."}})
    assert line == "seen: A person holds a box."


def test_early_second_descriptions_cannot_hide_the_last_sampled_scene():
    rows = {
        str(i): {
            "evidence_1_description": f"Scene {i}",
            "evidence_2_description": f"Another angle of scene {i}",
        }
        for i in range(9)
    }
    text = anchor_observations(list(rows), rows)
    assert "Scene 0 / Scene 4 / Scene 8" in text
    assert "Another angle" not in text


def test_people_in_unsampled_family_moment_reach_the_same_three_scene_summary():
    rows = {f"M{i}": {"evidence_1_description": f"Scene {i}."} for i in range(1, 8)}
    rows["M6"]["people"] = (
        "P01:name=Person A|relationship=library owner (inferred)|source=owner|age=34y;"
        "P02:name=Person B|relationship=unconfirmed|source=unconfirmed|age=?"
    )
    rows["outside-family"] = {
        "evidence_1_description": "Unrelated scene.",
        "people": "P03:name=Person C|relationship=friend|age=20y",
    }

    line = anchor_observations([f"M{i}" for i in range(1, 8)], rows)

    assert line == (
        "seen: Scene 1. / Scene 4. / Scene 7. | known people: "
        "Person A (library owner (inferred), age 34y); Person B (unconfirmed)"
    )


def test_family_people_keep_whole_facts_deduplicate_and_report_eight_name_overflow():
    rows = {f"M{i}": {"evidence_1_description": f"Scene {i}."} for i in range(1, 8)}
    people = [f"P{i:02}:name=Person {i}|relationship=unconfirmed|age=?" for i in range(1, 11)]
    rows["M2"]["people"] = ";".join(people)
    rows["M6"]["people"] = people[0]

    line = anchor_observations(list(rows), rows)

    assert line == (
        "seen: Scene 1. / Scene 4. / Scene 7. | known people: "
        + "; ".join(f"Person {i} (unconfirmed)" for i in range(1, 9))
        + "; +2 others"
    )
