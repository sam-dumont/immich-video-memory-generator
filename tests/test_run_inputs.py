"""The run's arguments, normalised once instead of at each point of use.

`run_pipeline_and_generate` takes 37 keyword arguments and normalised eight of
them inline, at the moment each was needed. Those rules had never been tested:
they were expressions in the middle of a function reachable only by running a
whole generation. Here they are, as assertions.

The duplicate is the reason this is a fix rather than tidying. "The photos, if
photos are enabled" was written twice — once for duration resolution, once for
canvas sizing. Tightening that rule in one place and not the other would leave
a video sized for photos it never budgeted time for.
"""

from __future__ import annotations

from pathlib import Path

from immich_memories.cli._run_inputs import ResolvedRunInputs
from immich_memories.config_loader import Config


def _resolve(**overrides) -> ResolvedRunInputs:
    defaults = {
        "include_photos": False,
        "photo_assets": None,
        "dry_run": False,
        "automation_attempt_id": None,
        "upload_to_immich": False,
        "config": Config(),
        "person_names": [],
        "music": None,
        "memory_preset_params": None,
    }
    defaults.update(overrides)
    return ResolvedRunInputs.from_arguments(**defaults)


def test_photos_are_withheld_when_photos_are_off() -> None:
    """One field, so duration and canvas cannot disagree about whether photos exist."""
    resolved = _resolve(include_photos=False, photo_assets=["a", "b"])

    assert resolved.photo_assets is None
    assert resolved.has_photos is False


def test_photos_are_present_when_enabled_and_supplied() -> None:
    resolved = _resolve(include_photos=True, photo_assets=["a", "b"])

    assert resolved.photo_assets == ["a", "b"]
    assert resolved.has_photos is True


def test_enabling_photos_without_any_is_not_having_photos() -> None:
    resolved = _resolve(include_photos=True, photo_assets=[])

    assert resolved.has_photos is False


def test_a_dry_run_claims_no_automation_attempt() -> None:
    """A rehearsal must not mark an automation attempt as spent."""
    assert _resolve(dry_run=True, automation_attempt_id="attempt-1").attempt_id is None
    assert _resolve(dry_run=False, automation_attempt_id="attempt-1").attempt_id == "attempt-1"


def test_upload_is_on_when_either_the_flag_or_the_config_says_so() -> None:
    config = Config()
    config.upload.enabled = True

    assert _resolve(upload_to_immich=True).should_upload is True
    assert _resolve(upload_to_immich=False, config=config).should_upload is True
    assert _resolve(upload_to_immich=False).should_upload is False


def test_the_first_person_names_the_memory() -> None:
    assert _resolve(person_names=["Emma", "Bob"]).person_name == "Emma"
    assert _resolve(person_names=[]).person_name is None


def test_auto_music_is_not_a_path() -> None:
    """ "auto" is a request to choose, not a file to load."""
    assert _resolve(music="auto").music_path is None
    assert _resolve(music=None).music_path is None
    assert _resolve(music="/tmp/track.mp3").music_path == Path("/tmp/track.mp3")


def test_preset_params_default_to_empty_rather_than_none() -> None:
    assert _resolve(memory_preset_params=None).preset_params == {}
    assert _resolve(memory_preset_params={"year": 2025}).preset_params == {"year": 2025}


def test_the_resolved_inputs_cannot_drift_mid_run() -> None:
    """Frozen, so nothing re-normalises halfway through a generation."""
    import dataclasses

    import pytest

    resolved = _resolve()
    with pytest.raises(dataclasses.FrozenInstanceError):
        resolved.should_upload = True  # type: ignore[misc]
