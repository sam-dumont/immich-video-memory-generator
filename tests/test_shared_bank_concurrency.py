"""Two runs writing one library bank at once keep both runs' answers.

Two `generate` runs, a web UI cut beside a CLI run, or a film beside idle fill all plan at the
same time: the pipeline lock only covers assembly. Each case here opens the bank twice, as two
runs do, lets each writer bank a different key, and reads back both.
"""

from concurrent.futures import ThreadPoolExecutor

from immich_memories.analysis.editorial_block_votes import (
    ONE_ORDER_ROWS,
    load_vote_bank,
    save_vote_bank,
)
from immich_memories.analysis.editorial_story_standing import StandingBankFile, standing_bank_path
from immich_memories.analysis.editorial_structure_audience import AudienceBank
from immich_memories.analysis.place_name_cache import PlaceNameCache
from immich_memories.people.companion import add_confirmed_person, load_document, people_entries

HOLD = {"verdict": "do_not_show", "finding": "exposure_evidence", "policy": "nsfw-head"}


def test_two_runs_keep_each_others_audience_answers_and_holds(tmp_path):
    path = tmp_path / "audience-verdicts.private.json"
    first = AudienceBank(path, answerer="reader")
    second = AudienceBank(path, answerer="reader")

    first.hold("held-picture", HOLD)
    first.keep("first-key", {"parsed": True, "verdict": "share"})
    second.keep("second-key", {"parsed": True, "verdict": "family_only"})

    reread = AudienceBank(path, answerer="reader")
    assert reread.answer("first-key") == {"parsed": True, "verdict": "share"}
    assert reread.answer("second-key") == {"parsed": True, "verdict": "family_only"}
    assert reread.held("held-picture")["verdict"] == "do_not_show"


def _vote_bank(block: str, row: str, one_order_row: str) -> dict:
    return {
        block: {"first": {"a": "yes"}},
        "rows": {row: {"votes": 2, "why": ""}},
        ONE_ORDER_ROWS: {one_order_row: {"votes": 1, "why": ""}},
    }


def test_two_runs_saving_one_vote_bank_keep_both_runs_blocks_and_rows(tmp_path):
    path = tmp_path / "memory-worthy.private.json"
    first, second = load_vote_bank(path), load_vote_bank(path)

    first.update(_vote_bank("block-1", "row-1", "partial-1"))
    save_vote_bank(path, first)
    second.update(_vote_bank("block-2", "row-2", "partial-2"))
    save_vote_bank(path, second)

    banked = load_vote_bank(path)
    assert set(banked) == {"block-1", "block-2", "rows", ONE_ORDER_ROWS}
    assert set(banked["rows"]) == {"row-1", "row-2"}
    assert set(banked[ONE_ORDER_ROWS]) == {"partial-1", "partial-2"}


def _bank_rows_at_once(case_bank_dir, writers: int, rows_each: int) -> None:
    def cut(writer: int) -> None:
        for n in range(rows_each):
            bank = StandingBankFile.open(case_bank_dir)
            bank.entries.setdefault("rows", {})[f"w{writer}-r{n}"] = {"votes": 2, "why": ""}
            bank.save()

    with ThreadPoolExecutor(writers) as pool:
        list(pool.map(cut, range(writers)))


def test_films_saving_the_standing_bank_at_the_same_moment_keep_every_row(tmp_path):
    case_bank_dir = tmp_path / "structure-banks" / "month"
    padding = {f"old-{n}": {"votes": 1, "why": "x" * 40} for n in range(3000)}
    StandingBankFile(standing_bank_path(case_bank_dir), {"rows": padding}).save()

    _bank_rows_at_once(case_bank_dir, writers=4, rows_each=15)

    rows = StandingBankFile.open(case_bank_dir).entries["rows"]
    assert {f"w{w}-r{n}" for w in range(4) for n in range(15)} <= set(rows)
    assert len(rows) == 3000 + 60


def test_two_runs_naming_different_places_keep_both_names(tmp_path):
    # WHY: the reader stands in for the geocoder, the only outside call the cache makes.
    first = PlaceNameCache(tmp_path, "fr", lambda *_: "Lyon, France")
    second = PlaceNameCache(tmp_path, "fr", lambda *_: "Gand, Belgique")

    first.name_for(45.76, 4.84, None)
    second.name_for(51.05, 3.72, None)
    first.flush()
    second.flush()

    offline = PlaceNameCache(tmp_path, "fr", None)
    assert offline.name_for(45.76, 4.84, None) == "Lyon, France"
    assert offline.name_for(51.05, 3.72, None) == "Gand, Belgique"


def test_people_added_from_the_web_ui_and_the_cli_at_once_are_all_kept(tmp_path):
    path = tmp_path / "people.yaml"
    names = [f"person-{writer}-{n}" for writer in range(4) for n in range(10)]

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda name: add_confirmed_person(path, name), names))

    assert {entry["name"] for entry in people_entries(load_document(path))} == set(names)
