"""MATRIX_FROZEN_CARDS is optional: absent, the fresh-cards path builds for real."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import probe_smart_edit_matrix as matrix  # noqa: E402

from immich_memories.analysis import selection_selects  # noqa: E402


def _import_cascade():
    """A fresh, single execution against whatever the fixture just restored.

    The probe module unconditionally recaptures `selection_selects._ask_one_pair`
    as "the original" at import time (for its own later restore-after-cascade
    logic). `importlib.reload` on an already-imported, already-patched module
    would recapture the *patched* wrapper instead -- evict it from
    ``sys.modules`` first so every call is a genuine first import.
    """
    sys.modules.pop("probe_pairhead_live_cascade", None)
    import probe_pairhead_live_cascade as cascade

    return cascade


def _seed_matrix_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe reads these at import time; point them at a throwaway dir."""
    monkeypatch.setenv("PAIRHEAD_MATRIX_DIR", str(tmp_path))
    judgments = tmp_path / "judgments-blanked-selects.db"
    judgments.touch()
    monkeypatch.setenv("PAIRHEAD_JUDGMENTS_DB", str(judgments))
    monkeypatch.setenv("PAIRHEAD_DECISIONS_OUT", str(tmp_path / "decisions.jsonl"))


@pytest.fixture
def _restore_shared_patches():
    """Importing the probe patches shared modules as a side effect; undo it after."""
    sys.modules.pop("probe_pairhead_live_cascade", None)
    original_build_fused_cards = matrix._build_fused_cards
    original_verdicts_beside = matrix.verdicts_beside
    original_ask_one_pair = selection_selects._ask_one_pair
    yield
    matrix._build_fused_cards = original_build_fused_cards
    matrix.verdicts_beside = original_verdicts_beside
    selection_selects._ask_one_pair = original_ask_one_pair
    sys.modules.pop("probe_pairhead_live_cascade", None)


def test_frozen_cards_env_absent_leaves_the_real_card_builder_in_place(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    _restore_shared_patches: None,
) -> None:
    """No MATRIX_FROZEN_CARDS means the fresh-cards path runs _build_fused_cards for real."""
    monkeypatch.delenv("MATRIX_FROZEN_CARDS", raising=False)
    _seed_matrix_dir(tmp_path, monkeypatch)
    real_build_fused_cards = matrix._build_fused_cards

    _import_cascade()

    assert matrix._build_fused_cards is real_build_fused_cards
    assert "fresh-cards mode" in capsys.readouterr().out


def test_frozen_cards_env_present_still_replays_the_exact_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _restore_shared_patches: None,
) -> None:
    """Existing behavior is unchanged when the env var is set: replay stays wired."""
    monkeypatch.setenv("MATRIX_FROZEN_CARDS", str(tmp_path / "cards.json"))
    _seed_matrix_dir(tmp_path, monkeypatch)

    cascade = _import_cascade()

    assert matrix._build_fused_cards is cascade._frozen_cards
