"""Flags decide admission deterministically, and a refused carrier is replaced or dropped."""

from __future__ import annotations

import json
import sqlite3

import pytest

from immich_memories.analysis import editorial_shareability as share


def flag_store(tmp_path, rows):
    path = tmp_path / "annotations.sqlite"
    con = sqlite3.connect(path)
    con.execute("create table flags (asset_id text, flag text, evidence text, source text)")
    con.executemany("insert into flags values (?, ?, ?, ?)", rows)
    con.commit()
    con.close()
    return path


def test_flags_are_read_for_the_asked_assets_with_readable_detector_evidence(tmp_path):
    path = flag_store(
        tmp_path,
        [
            ("one", "never_auto", json.dumps({"exposure": "partial", "score": 0.9}), "detector"),
            ("one", "review", "  needs   a look  ", "detector"),
            ("two", "never_auto", json.dumps({"exposure": None, "cleared": False}), "detector"),
            ("unasked", "never_auto", "", "detector"),
        ],
    )

    flags = share.load_flags(path, ["one", "two", "one"])

    assert set(flags) == {"one", "two"}
    assert [row.flag for row in flags["one"]] == ["never_auto", "review"]
    assert flags["one"][0].reason == "exposure=partial score=0.9"
    assert flags["one"][1].reason == "needs a look"
    assert flags["two"][0].reason == ""


def test_no_asked_assets_reads_nothing(tmp_path):
    assert share.load_flags(tmp_path / "absent.sqlite", []) == {}


def test_an_owner_clearance_lifts_the_exclusion_a_detector_wrote(tmp_path):
    flagged = share.FlagRow("one", "never_auto", "exposure=partial", "detector")
    cleared = share.FlagRow("two", "cleared", "the owner looked", "owner")

    excluded = share.never_auto_ids(
        {
            "one": (flagged,),
            "two": (share.FlagRow("two", "never_auto", "", "detector"), cleared),
            "three": (share.FlagRow("three", "review", "", "detector"),),
        }
    )

    assert excluded == frozenset({"one"})


def test_one_flagged_member_excludes_the_whole_live_photo_burst():
    units = [
        {"asset_id": "still", "members": ["still", "second"], "video_ids": ["motion"]},
        {"asset_id": "clean", "members": ["clean"]},
    ]

    kept, excluded = share.partition_units(units, {"motion"})

    assert [unit["asset_id"] for unit in kept] == ["clean"]
    assert [unit["asset_id"] for unit in excluded] == ["still"]


def test_every_selected_carrier_is_checked_even_without_a_flag_or_a_worrying_line():
    assert share.needs_check("A landscape at sunset.", ()) is True
    assert share.needs_check(None, ()) is True


def test_flags_are_rendered_for_the_reader_with_their_source_and_reason():
    rows = (
        share.FlagRow("one", "never_auto", "exposure=partial", "detector"),
        share.FlagRow("one", "review", "", "owner"),
    )

    assert share.render_flags(()) == "none"
    assert share.render_flags(rows) == "never_auto (exposure=partial) [detector]; review [owner]"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"why":"Nothing private.","verdict":"share"}', ("share", "Nothing private.")),
        ('```\n{"why":"A bath.","verdict":"family_only"}', ("family_only", "A bath.")),
        ('{"verdict":"share"} trailing prose', ("share", "")),
        ('{"why":"x","verdict":"maybe"}', None),
        ("no json at all", None),
    ],
)
def test_only_one_of_the_three_words_is_a_verdict(raw, expected):
    assert share.parse_verdict(raw) == expected


def test_a_long_reason_is_kept_to_the_words_the_answer_needs():
    raw = json.dumps({"why": " ".join(f"word{n}" for n in range(30)), "verdict": "share"})

    assert len(share.parse_verdict(raw)[1].split()) == share.REASON_WORDS


def test_the_strictest_verdict_wins_and_nothing_known_means_share():
    assert share.tighten("share", "family_only", None) == "family_only"
    assert share.tighten("family_only", "do_not_show") == "do_not_show"
    assert share.tighten(None, "not a verdict") == "share"


@pytest.mark.parametrize(
    "verdict,audience,expected",
    [
        ("share", "sendable", True),
        ("family_only", "family", True),
        ("family_only", "sendable", False),
        ("do_not_show", "family", False),
    ],
)
def test_a_sendable_export_keeps_only_share(verdict, audience, expected):
    assert share.allowed(verdict, audience) is expected


def gate(carriers, verdicts, pools, audience="family"):
    return share.apply_gate(
        carriers,
        verdict_of=lambda unit: verdicts.get(str(unit.get("asset_id"))),
        pool_for=lambda carrier: pools.get(str(carrier.get("asset_id")), ()),
        audience=audience,
    )


def test_a_refused_carrier_is_replaced_from_its_own_anchor_and_the_film_stays_chronological():
    carriers = [
        {"asset_id": "late", "event": "afternoon", "taken": "2021-06-05T15:00"},
        {"asset_id": "early", "event": "morning", "taken": "2021-06-05T09:00"},
    ]
    pools = {
        "early": [
            {"asset_id": "also-refused", "taken": "2021-06-05T09:10"},
            {"asset_id": "clean", "taken": "2021-06-05T09:20", "why": "the same morning"},
        ]
    }

    kept, log = gate(
        carriers,
        {"early": "family_only", "also-refused": "do_not_show", "clean": "share", "late": "share"},
        pools,
        audience="sendable",
    )

    assert [unit["asset_id"] for unit in kept] == ["clean", "late"]
    assert kept[0]["event"] == "morning"
    assert "replaces an unshareable carrier" in kept[0]["why"]
    assert log["substituted"] == [
        {"from": "early", "to": "clean", "event": "morning", "verdict": "family_only"}
    ]
    assert log["tightened"] == [{"asset_id": "early", "event": "morning", "verdict": "family_only"}]
    assert log["checked"] == 4


def test_an_anchor_with_no_shareable_replacement_loses_the_slot_rather_than_borrowing_one():
    carriers = [{"asset_id": "refused", "event": "bathtime", "taken": "2021-06-05T19:00"}]
    pools = {"refused": [{"asset_id": "also-refused", "taken": "2021-06-05T19:05"}]}

    kept, log = gate(carriers, {"refused": "do_not_show", "also-refused": "do_not_show"}, pools)

    assert kept == []
    assert log["dropped"] == [
        {"asset_id": "refused", "event": "bathtime", "verdict": "do_not_show"}
    ]
    assert log["substituted"] == []


def test_a_carrier_already_in_the_film_is_never_reused_as_its_own_replacement():
    carriers = [
        {"asset_id": "refused", "event": "evening", "taken": "2021-06-05T19:00"},
        {"asset_id": "kept", "event": "evening", "taken": "2021-06-05T20:00"},
    ]
    pools = {
        "refused": [
            {"asset_id": "kept", "taken": "2021-06-05T20:00"},
            {"asset_id": "fresh", "taken": "2021-06-05T19:30"},
        ]
    }

    kept, log = gate(
        carriers,
        {"refused": "family_only", "kept": "share", "fresh": "share"},
        pools,
        audience="sendable",
    )

    assert [unit["asset_id"] for unit in kept] == ["fresh", "kept"]
    assert log["substituted"][0]["to"] == "fresh"


def test_a_carrier_no_check_applies_to_is_kept_without_counting_as_checked():
    carriers = [{"asset_id": "unchecked", "taken": "2021-06-05T09:00"}]

    kept, log = gate(carriers, {}, {})

    assert [unit["asset_id"] for unit in kept] == ["unchecked"]
    assert log["checked"] == 0 and log["tightened"] == []


def test_an_unchecked_pool_unit_is_accepted_as_the_replacement_without_a_verdict():
    carriers = [{"asset_id": "refused", "taken": "2021-06-05T09:00"}]
    pools = {"refused": [{"asset_id": "unchecked", "taken": "2021-06-05T09:30"}]}

    kept, log = gate(carriers, {"refused": "do_not_show"}, pools)

    assert [unit["asset_id"] for unit in kept] == ["unchecked"]
    assert log["checked"] == 1
