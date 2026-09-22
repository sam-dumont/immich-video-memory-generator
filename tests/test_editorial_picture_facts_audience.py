"""What the optional picture reader adds to an audience decision, and what it may never lift."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from immich_memories.analysis.editorial_shareability_audience import picture_facts_hold

LINE = "2026-08-25 12:00 | People in a room. | picture: "


@pytest.mark.parametrize(
    ("segment", "expected"),
    [
        ("record 0.96; worth 0.90; what=meaningful_record", "do_not_show"),
        ("record 0.72; worth 0.90; what=meaningful_record", None),
        ("worth 0.90; what=people_moment; adult=bare_torso", "family_only"),
        ("worth 0.90; what=people_moment; adult=underwear_only", "family_only"),
        ("worth 0.90; what=people_moment; adult=nude", "family_only"),
        ("worth 0.90; what=people_moment; child=nude", "family_only"),
        ("bathing 0.94; worth 0.90; what=people_moment", "family_only"),
        ("breastfeeding 0.95; worth 0.90; what=people_moment", "family_only"),
        ("worth 0.90; what=people_moment; adult=swimwear", None),
        ("worth 0.90; what=people_moment; child=nappy_or_underwear_only", None),
    ],
)
def test_the_reader_names_a_hold_only_where_it_is_certain(segment, expected):
    assert picture_facts_hold(LINE + segment) == expected


def test_a_line_with_no_picture_row_holds_nothing():
    assert picture_facts_hold("2026-08-25 12:00 | People in a room. | DARK") is None


class _Pictures:
    # WHY: the picture bank is the film-time observation service; the gate only reads a line
    def __init__(self, line: str) -> None:
        self._line = line
        self.annotations: dict[str, object] = {}
        self.records: dict[str, object] = {}

    def enrich(self, _unit, **_kwargs):
        return None

    def line(self, _unit) -> str:
        return self._line


def _gate(line: str, verdict: str, tmp_path):
    from immich_memories.analysis.editorial_structure_audience import AudienceGate

    return AudienceGate(
        judge=SimpleNamespace(calls=[]),
        audience="family",
        picture_evidence=_Pictures(line),
        flag_rows={},
        lines={"a": line},
        bank_path=tmp_path / "bank.json",
        # WHY: the audience reader is the only model call this gate makes
        check_audience=lambda *_args: {"verdict": verdict, "finding": "none", "parsed": True},
    )


def test_a_certain_picture_fact_tightens_a_verdict_the_reader_was_happy_with(tmp_path):
    unit = {"asset_id": "a", "members": (), "kind": "still"}

    held = _gate(LINE + "bathing 0.96; worth 0.90; what=people_moment", "share", tmp_path)
    refused = _gate(LINE + "record 0.96; worth 0.90; what=meaningful_record", "share", tmp_path)

    assert held.verdict_of(unit) == "family_only"
    assert refused.verdict_of(unit) == "do_not_show"
    assert held.verdicts["a"]["picture_facts_hold"] == "family_only"


def test_a_picture_fact_never_loosens_what_the_reader_refused(tmp_path):
    unit = {"asset_id": "a", "members": (), "kind": "still"}

    gate = _gate(LINE + "worth 0.90; what=people_moment; adult=clothed", "do_not_show", tmp_path)

    assert gate.verdict_of(unit) == "do_not_show"
