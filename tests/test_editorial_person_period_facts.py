"""People significance is factual, dated and independent of scene sampling."""

import json

import pytest

from immich_memories.analysis.editorial_person_period_facts import (
    person_period_facts,
    render_person_period_facts,
)


def _tables(*, first="2007-08", onset="2007-08", source="confirmed"):
    return {
        "people": (
            ["id", "name", "relationship", "source", "birth", "first", "onset", "tier"],
            [["P02", "Known person", "partner", source, "1989-12-03", first, onset, "inner"]],
        ),
        "moments": (
            ["id", "taken"],
            [[f"M{i:02d}", f"2007-08-{i:02d}T12:00:00+00:00"] for i in range(1, 8)],
        ),
        "moment_people": (["moment", "person", "age"], [["M02", "P02", "17y"]]),
    }


def test_tagged_nonsampled_moment_preserves_dated_facts_without_scene_text():
    tables = _tables()
    # Existing three-scene sampling would choose M01, M04 and M07.
    facts = person_period_facts(tables, [f"M{i:02d}" for i in range(1, 8)])
    assert len(facts) == 1
    fact = facts[0]
    assert fact.grounding_moment_ids == ("M02",)
    assert fact.first_library_month == fact.sustained_onset_month == "2007-08"
    assert fact.current_relationship == "partner"
    assert fact.relationship_source == "confirmed"
    assert fact.tier == "inner"
    assert person_period_facts(tables, ["M01", "M04", "M07"]) == ()


def test_first_and_sustained_onset_remain_distinct_and_scope_to_tagged_dates():
    tables = _tables(first="2007-08", onset="2008-02")
    tables["moments"][1].extend(
        [
            ["M08", "2008-02-12T12:00:00+00:00"],
            ["M09", "2008-03-12T12:00:00+00:00"],
        ]
    )
    tables["moment_people"][1].extend([["M08", "P02", "18y"], ["M09", "P02", "18y"]])
    first = person_period_facts(tables, ["M02"])[0]
    assert (first.first_library_month, first.sustained_onset_month) == ("2007-08", None)
    onset = person_period_facts(tables, ["M08"])[0]
    assert (onset.first_library_month, onset.sustained_onset_month) == (None, "2008-02")
    assert person_period_facts(tables, ["M09"]) == ()
    both = person_period_facts(tables, ["M08", "M02", "M09"])[0]
    assert (both.first_library_month, both.sustained_onset_month) == ("2007-08", "2008-02")
    assert both.grounding_moment_ids == ("M02", "M08")


@pytest.mark.parametrize("source", ["confirmed", "derived", "unconfirmed"])
def test_relationship_provenance_is_preserved_without_promoting_authority(source):
    fact = person_period_facts(_tables(source=source), ["M02"])[0]
    assert fact.relationship_source == source
    assert fact.current_relationship == "partner"


def test_duplicates_and_input_order_do_not_change_facts_or_rendering():
    tables = _tables()
    tables["people"][1].append(
        ["P01", "Another person", "friend", "derived", "null", "2007-08", "2007-08", "recurring"]
    )
    tables["moment_people"][1].extend(
        [
            ["M02", "P02", "17y"],
            ["M03", "P02", "17y"],
            ["M02", "P01", "?"],
        ]
    )
    expected = person_period_facts(tables, ["M03", "M02", "M02"])
    tables["moment_people"][1].reverse()
    tables["people"][1].reverse()
    actual = person_period_facts(tables, ["M02", "M03"])
    assert actual == expected
    assert [fact.person_token for fact in actual] == ["P01", "P02"]
    assert actual[1].grounding_moment_ids == ("M02", "M03")
    rendered = render_person_period_facts(actual)
    assert rendered == render_person_period_facts(expected)
    assert json.loads(rendered)[1]["grounding_moment_ids"] == ["M02", "M03"]
    assert "Known person" in rendered
    assert render_person_period_facts(()) == ""


@pytest.mark.parametrize("bad", ["?", "null", "", "2007-13", "2007-8", "2007-08-05", "0000-08"])
def test_invalid_people_months_create_no_temporal_claim(bad):
    assert person_period_facts(_tables(first=bad, onset=bad), ["M02"]) == ()


def test_invalid_taken_date_does_not_match_a_valid_people_month():
    tables = _tables()
    tables["moments"][1][1][1] = "2007-08-impossible"
    assert person_period_facts(tables, ["M02"]) == ()


def test_unnamed_associations_are_omitted_but_unknown_known_identity_is_rejected():
    tables = _tables()
    tables["moment_people"][1].append(["M02", "U01", "?"])
    assert len(person_period_facts(tables, ["M02"])) == 1
    tables["moment_people"][1].append(["M02", "P99", "?"])
    with pytest.raises(ValueError, match="ungrounded"):
        person_period_facts(tables, ["M02"])


def test_unknown_moment_cannot_ground_a_claim():
    with pytest.raises(ValueError, match="unknown moment"):
        person_period_facts(_tables(), ["M99"])
    tables = _tables()
    tables["moment_people"][1].append(["M99", "P02", "17y"])
    with pytest.raises(ValueError, match="unknown moment"):
        person_period_facts(tables, ["M02"])
