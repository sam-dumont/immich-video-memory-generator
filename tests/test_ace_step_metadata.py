"""ACE-Step captions and metadata must match what its API accepts (#308).

Two lessons are encoded here. ACE-Step's constrained decoder force-injects
user-supplied metadata into the language model's chain-of-thought stream
*without validating it*, so an out-of-vocabulary value silently corrupts the
hints that condition generation. And its prompting guides are explicit that a
caption needs concrete sound sources plus a fidelity tag — adjectives alone
give the model nothing to render.
"""

from __future__ import annotations

import pytest

from immich_memories.audio.generators.ace_step_captions import (
    VALID_TIME_SIGNATURES,
    build_ace_caption_structured,
    normalize_time_signature,
)
from immich_memories.audio.mood_analyzer import VALID_MOODS

# Concrete sound sources: "grand piano, upright bass, brushed drums" gives the
# model three things to render; "sophisticated, elegant" gives it nothing.
INSTRUMENTS = (
    "piano",
    "guitar",
    "bass",
    "drums",
    "strings",
    "cello",
    "violin",
    "harp",
    "horn",
    "trumpet",
    "flute",
    "clarinet",
    "oboe",
    "rhodes",
    "wurlitzer",
    "glockenspiel",
    "mandolin",
    "harmonium",
    "celeste",
    "synth",
    "timpani",
    "percussion",
    "shaker",
    "tambourine",
    "organ",
    "vibraphone",
    "marimba",
    # Electronic sound sources are just as concrete as acoustic ones.
    "kick",
    "snare",
    "pad",
    "lead",
    "riser",
    "break",
    "stab",
    "clavinet",
)
FIDELITY_TAGS = (
    "hi-fi",
    "lo-fi",
    "polished",
    "dusty",
    "analog warmth",
    "tape saturation",
    "vinyl crackle",
    "intimate",
    "cinematic",
    "wide stereo",
    "raw",
    "vintage",
)


@pytest.mark.parametrize("mood", sorted(VALID_MOODS))
def test_every_mood_yields_a_time_signature_the_api_accepts(mood):
    assert build_ace_caption_structured(mood).time_signature in VALID_TIME_SIGNATURES


class TestNormalizeTimeSignature:
    @pytest.mark.parametrize(
        ("written", "expected"),
        [("4/4", "4"), ("3/4", "3"), ("6/8", "6"), ("2/4", "2"), ("4", "4")],
    )
    def test_common_notations_map_to_the_api_value(self, written, expected):
        assert normalize_time_signature(written) == expected

    def test_an_unsupported_signature_defers_to_auto_detection(self):
        """Better to let ACE-Step infer the meter than to inject a bad hint."""
        assert normalize_time_signature("7/8") == ""
        assert normalize_time_signature("") == ""


@pytest.mark.parametrize("mood", sorted(VALID_MOODS))
class TestCaptionsFollowTheGuides:
    def test_caption_fits_the_api_limit(self, mood):
        assert len(build_ace_caption_structured(mood).caption) < 512

    def test_caption_names_concrete_instruments(self, mood):
        caption = build_ace_caption_structured(mood).caption.lower()

        assert any(i in caption for i in INSTRUMENTS), caption

    def test_caption_states_the_intended_fidelity(self, mood):
        """Fidelity tags are the documented lever for overall clarity."""
        caption = build_ace_caption_structured(mood).caption.lower()

        assert any(t in caption for t in FIDELITY_TAGS), caption

    def test_caption_does_not_stack_contradictory_fidelity(self, mood):
        caption = build_ace_caption_structured(mood).caption.lower()

        assert not ("hi-fi" in caption and "lo-fi" in caption), caption

    def test_bpm_appears_in_the_caption_as_well_as_the_field(self, mood):
        """The guides say to set BPM in the tags and as the parameter."""
        result = build_ace_caption_structured(mood)

        assert f"{result.bpm} bpm" in result.caption.lower(), result.caption

    def test_caption_names_enough_instruments_to_sound_full(self, mood):
        """Listening sweep: 2-3 instruments reads as thin; 4+ fills the arrangement.

        The published guides cap this at 2-3 to avoid muddy output, but at our
        settings that was audibly sparse. Adding arrangement language on top of the
        extra instruments tipped it into incoherence, so we widen instruments only.
        """
        caption = build_ace_caption_structured(mood).caption.lower()

        named = {i for i in INSTRUMENTS if i in caption}

        assert len(named) >= 3, f"only {named} in: {caption}"
