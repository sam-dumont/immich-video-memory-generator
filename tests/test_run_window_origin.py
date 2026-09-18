"""A window nobody typed is filed with the run, so `runs why` can explain it."""

from __future__ import annotations

import json

from immich_memories.operations.editorial_attempt import window_origin_note

ORIGIN = "the window starts at the youngest birth date the people it needs allow"


def _attempt(tmp_path, request):
    (tmp_path / "status.private.json").write_text(json.dumps({"request": request}))
    return tmp_path


def test_a_derived_window_is_explained_to_whoever_asks_why(tmp_path):
    """The run derived its own span; a picture outside it was never a candidate."""
    attempt = _attempt(tmp_path, {"key": "k", "window_origin": ORIGIN})

    assert window_origin_note(attempt) == f"Window: nobody typed one — {ORIGIN}"


def test_a_typed_window_owes_no_explanation(tmp_path):
    """The dates were asked for, so there is nothing to explain."""
    assert window_origin_note(_attempt(tmp_path, {"key": "k"})) == ""


def test_a_run_with_no_record_says_nothing(tmp_path):
    """An older attempt directory must not break reading the cut."""
    assert window_origin_note(tmp_path) == ""


def test_the_run_context_carries_the_derived_origin_to_the_record():
    """What the CLI derived has to survive into the run the record is written from."""
    from datetime import datetime

    from immich_memories.cli._editorial_context import build_editorial_context
    from immich_memories.cli._run_inputs import ResolvedRunInputs
    from immich_memories.config_loader import Config
    from immich_memories.timeperiod import DateRange

    config = Config()
    resolved = ResolvedRunInputs.from_arguments(
        include_photos=False,
        photo_assets=None,
        dry_run=False,
        automation_attempt_id=None,
        upload_to_immich=False,
        config=config,
        person_names=["Adult A"],
        music=None,
        memory_preset_params={"window_origin": ORIGIN},
    )

    context = build_editorial_context(
        resolved=resolved,
        config=config,
        memory_type="multi_person",
        memory_key=None,
        output_stem="people",
        assets=[],
        date_range=DateRange(datetime(2024, 3, 11), datetime(2026, 9, 18)),
        date_ranges=None,
        duration=60.0,
        transition="smart",
        title_override=None,
        person_names=["Adult A"],
        accept_any_provenance=False,
    )

    assert context.window_origin == ORIGIN
