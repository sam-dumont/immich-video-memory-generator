"""The library's own account of a period, read by a film that never writes it."""

from __future__ import annotations

import json
import sqlite3

from immich_memories.store.library_overviews import library_period_account

_SCHEMA = (
    "CREATE TABLE library_overviews (node_key TEXT, kind TEXT, period TEXT, "
    "account TEXT, children TEXT)"
)


def _bank(path, rows):
    with sqlite3.connect(path) as connection:
        connection.execute(_SCHEMA)
        connection.executemany(
            "INSERT INTO library_overviews VALUES (?, ?, ?, ?, ?)",
            [
                (key, kind, period, account, json.dumps(children))
                for key, kind, period, account, children in rows
            ],
        )
        connection.commit()
    return path


def test_a_library_with_no_overview_table_has_no_account(tmp_path):
    empty = tmp_path / "annotations.sqlite"
    sqlite3.connect(empty).close()
    assert library_period_account(empty, "2024-02") == ""
    assert library_period_account(tmp_path / "absent.sqlite", "2024-02") == ""


def test_the_fuller_revision_of_a_period_is_the_account(tmp_path):
    bank = _bank(
        tmp_path / "bank.sqlite",
        [
            ("n1", "month", "2024-02", "a short reading", ["e1"]),
            ("n2", "month", "2024-02", "a reading over more of the month", ["e1", "e2", "e3"]),
            ("n3", "month", "2024-03", "another month entirely", ["e9"]),
        ],
    )
    assert library_period_account(bank, "2024-02") == "a reading over more of the month"


def test_parts_of_a_period_are_joined_in_node_key_order(tmp_path):
    bank = _bank(
        tmp_path / "bank.sqlite",
        [
            ("n2", "month-part", "2024-02", "the second half", ["e2"]),
            ("n1", "month-part", "2024-02", "the first half", ["e1"]),
        ],
    )
    assert library_period_account(bank, "2024-02") == "the first half\n\nthe second half"


def test_a_blank_account_is_no_account(tmp_path):
    bank = _bank(tmp_path / "bank.sqlite", [("n1", "month", "2024-02", "   ", ["e1"])])
    assert library_period_account(bank, "2024-02") == ""
