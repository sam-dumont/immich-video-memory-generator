"""ACE-Step caption templates and prompt building.

Dense caption templates with genre, instruments, key, BPM, and time signature
for high-quality ACE-Step 1.5 music generation.

BPM, key_scale, and time_signature are returned separately so they can be
sent as explicit API parameters (not buried in the caption text).
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.audio.mood_analyzer import VideoMood

# Dense caption templates for ACE-Step.
# Each template uses a descriptive sentence (not just tags) for better LLM guidance.
# BPM, key, and time_signature are sent as separate API params.
# WHY: ACE-Step's metadata vocabulary is a bare beat count (2 for 2/4, 3 for 3/4,
# 4 for 4/4, 6 for 6/8) — acestep.constants.VALID_TIME_SIGNATURES. Its constrained
# decoder injects whatever it is given straight into the LM's chain-of-thought
# metadata stream with no validation, so "4/4" lands as out-of-vocabulary tokens in
# the hints that condition the DiT. The tables below stay human-readable and are
# translated on the way out.
VALID_TIME_SIGNATURES = ("2", "3", "4", "6")
_TIME_SIGNATURE_BEATS = {"2/4": "2", "3/4": "3", "4/4": "4", "6/8": "6"}


def normalize_time_signature(written: str) -> str:
    """Translate a written time signature to ACE-Step's beat-count value.

    Returns an empty string for anything ACE-Step does not accept, which asks it
    to infer the meter instead of conditioning on a value it never saw in training.
    """
    value = written.strip()
    if value in VALID_TIME_SIGNATURES:
        return value
    return _TIME_SIGNATURE_BEATS.get(value, "")


# "instrumental, no vocals" is reinforced in every caption to prevent singing.
# Seasonal modifiers appended to tags
_SEASON_TAG_MODIFIERS = {
    "winter": "cozy, warm tones, intimate",
    "spring": "fresh, bright, blossoming",
    "summer": "sunny, carefree, vibrant",
    "autumn": "warm, golden, mellow",
    "holiday": "festive, joyful, celebratory",
}


@dataclass(frozen=True)
class MoodProfile:
    """What a mood contributes: where it sits on the energy axis, and its colour.

    Energy is a position (0 = still, 1 = driving) rather than a fixed BPM, so each
    style can place it inside a tempo its own genre actually carries.
    """

    word: str
    energy: float
    mode: str
    production: str


@dataclass(frozen=True)
class StyleProfile:
    """What a style contributes: the genre anchor and the instruments to render.

    A style may name a different genre per mood. Electronic needs this: its
    sub-genres are defined by tempo, and the guides warn that a genre fighting
    the BPM confuses the model — drum and bass at 70 bpm is not a thing.
    """

    genre: str
    instruments: str
    tempo_range: tuple[int, int] = (70, 150)
    genre_by_mood: Mapping[str, str] = field(default_factory=dict)

    def genre_for(self, mood_key: str) -> str:
        return self.genre_by_mood.get(mood_key, self.genre)

    def bpm_for(self, energy: float) -> int:
        """Place the mood's energy inside this genre's own tempo band."""
        low, high = self.tempo_range
        return int(round(low + (high - low) * energy))


# Mood sets the tempo and feel; style sets the genre and instruments. Every
# combination is a valid caption, so one mood no longer always sounds the same.
#
# Five buckets collapsed the LLM's own vocabulary ("mysterious", "melancholic",
# "dramatic" all arrived as "calm"/"nostalgic"/"happy"), which is what made every
# soundtrack sound alike (#1007). The table now carries one profile per mood the
# reader can name, each with its own register and production texture.
MOOD_PROFILES: dict[str, MoodProfile] = {
    "calm": MoodProfile("serene", 0.00, "major", "hi-fi, intimate"),
    "peaceful": MoodProfile("peaceful", 0.06, "major", "warm, spacious, intimate"),
    "sad": MoodProfile("sorrowful", 0.10, "minor", "intimate, sparse, heartfelt"),
    "melancholic": MoodProfile("reflective", 0.15, "minor", "analog warmth, delicate"),
    "nostalgic": MoodProfile("wistful", 0.20, "minor", "analog warmth, tape saturation"),
    "mysterious": MoodProfile("mysterious", 0.30, "minor", "cinematic, dark, atmospheric"),
    "tender": MoodProfile("tender", 0.37, "major", "analog warmth, intimate"),
    "romantic": MoodProfile("romantic", 0.46, "major", "lush, warm, cinematic"),
    "playful": MoodProfile("playful", 0.58, "major", "bright, bouncy, wide stereo"),
    "happy": MoodProfile("joyful", 0.68, "major", "hi-fi, polished, wide stereo"),
    "inspiring": MoodProfile("uplifting", 0.76, "major", "cinematic, wide stereo"),
    "uplifting": MoodProfile("anthemic", 0.82, "major", "anthemic, wide stereo"),
    "exciting": MoodProfile("exhilarating", 0.90, "major", "hi-fi, wide stereo, punchy"),
    "energetic": MoodProfile("driving", 1.00, "minor", "hi-fi, wide stereo"),
    "dramatic": MoodProfile("dramatic", 0.95, "minor", "cinematic, tense, swelling"),
}

# Roots rotate across the matrix so neighbouring combinations do not share a key.
VALID_KEY_ROOTS = ("C", "D", "E", "F", "G", "A", "Bb")

STYLE_PROFILES: dict[str, StyleProfile] = {
    "acoustic": StyleProfile(
        genre="acoustic folk",
        instruments=("fingerpicked acoustic guitar, upright bass, brushed drums, glockenspiel"),
        tempo_range=(68, 132),
    ),
    # Electronic still spans several genres, chosen by tempo, so two styles do not
    # mean two sounds. Rock, EDM and drum-and-bass styles were cut after a listening
    # pass: they produced usable tracks only 2-5 times in 10, against 8 and 6 here.
    "electronic": StyleProfile(
        genre="future bass",
        instruments=(
            "analog synth bass, plucky lead synth, crisp electronic drums, sidechained pads, punchy"
        ),
        tempo_range=(72, 150),
        genre_by_mood={
            "calm": "downtempo electronic",
            "peaceful": "ambient",
            "sad": "downtempo electronic",
            "melancholic": "trip hop",
            "nostalgic": "trip hop",
            "mysterious": "dark ambient",
            "tender": "chillwave",
            "romantic": "chillwave",
            "playful": "synth pop",
            "happy": "future bass",
            "inspiring": "melodic house",
            "uplifting": "uplifting trance",
            "exciting": "electro house",
            "energetic": "drum and bass",
            "dramatic": "cinematic electronic",
        },
    ),
    # Named directly by the reader; ambient kept its own home rather than riding
    # the future-bass instruments, which would fight a sparse brief (#1007).
    "ambient": StyleProfile(
        genre="ambient",
        instruments="evolving pads, airy synth textures, soft sub bass, field recordings",
        tempo_range=(50, 90),
    ),
    # The reader names these genres directly; without a home for them the LLM's
    # "jazz"/"orchestral"/"rock" suggestions all collapsed to future bass (#1007).
    "jazz": StyleProfile(
        genre="jazz trio",
        instruments="upright bass, brushed drums, rhodes, muted trumpet",
        tempo_range=(80, 160),
    ),
    "rock": StyleProfile(
        genre="indie rock",
        instruments="electric guitar, driving bass, live drums, organ",
        tempo_range=(90, 170),
    ),
    "orchestral": StyleProfile(
        genre="cinematic orchestral",
        instruments="strings, french horn, timpani, piano",
        tempo_range=(60, 120),
    ),
    "piano": StyleProfile(
        genre="solo piano",
        instruments="grand piano, felt piano, upright bass, light strings",
        tempo_range=(60, 140),
    ),
    # Added for event memories that name the genre directly (a festival day),
    # so the reader can say "metal" and have it mean metal, not smooth jazz (#1007).
    # The concrete techniques is what makes ACE-Step render it: a bare "metal,
    # power chords" reads as a muddy wash, but "chugging palm-muted riffs" locks
    # the model onto the genre.
    "metal": StyleProfile(
        genre="heavy metal",
        instruments="chugging palm-muted guitar riffs, double bass drums, heavy bass, power chords",
        tempo_range=(90, 160),
    ),
}

# The reader's genre vocabulary (mood_analyzer.VALID_GENRES) onto a style. Words
# that describe a vibe rather than a genre ("upbeat", "relaxing") are left out so
# they do not hijack the style; they steer energy instead.
_GENRE_TO_STYLE: dict[str, str] = {
    "acoustic": "acoustic",
    "folk": "acoustic",
    "guitar": "acoustic",
    "electronic": "electronic",
    "ambient": "ambient",
    "pop": "electronic",
    "rock": "rock",
    "indie": "rock",
    "metal": "metal",
    "jazz": "jazz",
    "cinematic": "orchestral",
    "orchestral": "orchestral",
    "classical": "orchestral",
    "piano": "piano",
}

# The reader states energy and tempo as words, not numbers; both map onto the
# same 0..1 position the mood would otherwise supply.
_ENERGY_LEVELS: dict[str, float] = {"low": 0.0, "medium": 0.5, "high": 1.0}
_TEMPO_ENERGY: dict[str, float] = {"slow": 0.0, "medium": 0.5, "fast": 1.0}

# Memory types that suggest a style; otherwise the style is sampled for variety.
_MEMORY_TYPE_TO_STYLE: dict[str, str] = {
    "person_spotlight": "acoustic",
    "on_this_day": "electronic",
}

_MOOD_ALIASES: dict[str, str] = {
    "upbeat": "happy",
    "fun": "playful",
    "sunny": "happy",
    "cheerful": "happy",
    "dreamy": "calm",
    "cozy": "tender",
    "groovy": "playful",
    "jazzy": "tender",
    "warm": "tender",
    "holiday": "tender",
    "festive": "happy",
    "hopeful": "inspiring",
    "relaxing": "calm",
    "relaxed": "peaceful",
    "reflective": "nostalgic",
    "somber": "sad",
    "epic": "dramatic",
    "intense": "dramatic",
}


# _transform_mood prepends these to every mood, so they must not win a match:
# "upbeat romantic" is romantic, not upbeat.
_BOOSTER_WORDS = frozenset({"upbeat", "warm", "groovy", "hopeful"})


def resolve_mood(mood: str) -> str:
    """Map any mood phrase onto one of the five profiles.

    Specific mood words are matched before the generic boosters that get
    prepended upstream, which would otherwise send everything to one profile.
    """
    words = [w.strip(",.! ") for w in mood.lower().split() if w.strip(",.! ")]
    specific = [w for w in words if w not in _BOOSTER_WORDS]

    for group in (specific, words):
        for word in group:
            if word in MOOD_PROFILES:
                return word
        for word in group:
            alias = _MOOD_ALIASES.get(word)
            if alias in MOOD_PROFILES:
                return alias
    return "happy"


def pick_style(
    memory_type: str | None = None,
    style: str | None = None,
    genres: list[str] | None = None,
) -> str:
    """Choose the style for this generation.

    An explicit style wins, then the reader's own genre yields, then a memory
    type with a natural fit. Only when none of those speak is one sampled, so
    repeated memories of the same mood still do not all sound alike.
    """
    if style in STYLE_PROFILES:
        return style
    if genres:
        for genre in genres:
            chosen = _GENRE_TO_STYLE.get(genre)
            if chosen in STYLE_PROFILES:
                return chosen
    if memory_type and memory_type in _MEMORY_TYPE_TO_STYLE:
        return _MEMORY_TYPE_TO_STYLE[memory_type]
    return random.choice(sorted(STYLE_PROFILES))


def compose_caption(
    mood_key: str,
    style_key: str,
    cadence_seconds: float | None = None,
    energy_override: float | None = None,
) -> tuple[str, int, str]:
    """Build the caption on ACE-Step's documented order, with its tempo and key.

    A cadence nudges the tempo within the style's own range so a photo lasts a
    whole number of beats — the cuts are already fixed by the time music is
    chosen, so the music is what adapts. An energy override comes from the
    reader's stated energy/tempo, which knows the film better than any one mood
    word does.

    Returns (caption, bpm, key_scale).
    """
    from immich_memories.audio.beat_grid import beat_aligned_bpm

    profile, style = MOOD_PROFILES[mood_key], STYLE_PROFILES[style_key]
    energy = profile.energy if energy_override is None else energy_override
    bpm = style.bpm_for(energy)
    if cadence_seconds:
        bpm = beat_aligned_bpm(bpm, cadence_seconds, style.tempo_range)
    key_scale = f"{_key_root(mood_key, style_key)} {profile.mode}"
    caption = (
        f"{style.genre_for(mood_key)}, {profile.word}, {style.instruments}, "
        f"{profile.production}, {bpm} bpm"
    )
    return caption, bpm, key_scale


def _key_root(mood_key: str, style_key: str) -> str:
    """Rotate roots across the matrix so neighbouring cells differ in tonality."""
    index = sorted(MOOD_PROFILES).index(mood_key) + sorted(STYLE_PROFILES).index(style_key)
    return VALID_KEY_ROOTS[index % len(VALID_KEY_ROOTS)]


@dataclass
class ACECaptionResult:
    """Structured result from build_ace_caption_structured.

    Separates musical parameters so they can be sent as explicit
    API fields rather than embedded in the caption text.
    """

    caption: str
    lyrics: str
    bpm: int
    key_scale: str
    time_signature: str


def build_ace_caption_structured(
    mood: str,
    season: str | None = None,
    scene_moods: list[str] | None = None,
    memory_type: str | None = None,
    style: str | None = None,
    cadence_seconds: float | None = None,
    mood_detail: VideoMood | None = None,
) -> ACECaptionResult:
    """Build a structured ACE-Step caption from the mood x style matrix.

    Mood decides tempo and emotional register; style decides genre and
    instruments. The reader's own music judgment (``mood_detail``) is honoured
    where it exists: a stated ``specific_style`` wins outright (the event wants
    a music style the genre list cannot name), otherwise its genre yields pick
    the style and its stated energy or tempo places the tempo. Without either a
    style is sampled, so repeated memories of the same mood do not all sound
    alike.
    """
    mood_key = resolve_mood(scene_moods[0] if scene_moods else mood)
    specific = mood_detail.specific_style if mood_detail else None

    if specific:
        profile = MOOD_PROFILES[mood_key]
        energy = _energy_override(mood_detail)
        energy = profile.energy if energy is None else energy
        bpm = _specific_style_bpm(energy, cadence_seconds)
        key_scale = f"{_key_root(mood_key, 'acoustic')} {profile.mode}"
        caption = f"{specific}, {profile.word}, {profile.production}, {bpm} bpm"
    else:
        genres = mood_detail.genre_suggestions if mood_detail else None
        style_key = pick_style(memory_type=memory_type, style=style, genres=genres)
        caption, bpm, key_scale = compose_caption(
            mood_key, style_key, cadence_seconds, _energy_override(mood_detail)
        )

    if season:
        modifier = _SEASON_TAG_MODIFIERS.get(season.lower(), "")
        if modifier:
            caption = f"{caption}, {modifier}"

    return ACECaptionResult(
        caption=caption,
        lyrics="[Instrumental]",
        bpm=bpm,
        key_scale=key_scale,
        time_signature=normalize_time_signature("4"),
    )


_SPECIFIC_STYLE_TEMPO = (60, 140)


def _specific_style_bpm(energy: float, cadence_seconds: float | None) -> int:
    """Place a free-form style in a neutral tempo band; no style profile owns it."""
    from immich_memories.audio.beat_grid import beat_aligned_bpm

    low, high = _SPECIFIC_STYLE_TEMPO
    bpm = int(round(low + (high - low) * energy))
    if cadence_seconds:
        bpm = beat_aligned_bpm(bpm, cadence_seconds, _SPECIFIC_STYLE_TEMPO)
    return bpm


def _energy_override(mood_detail: VideoMood | None) -> float | None:
    """The 0..1 position from the reader's energy, then its tempo, else None."""
    if mood_detail is None:
        return None
    if mood_detail.energy_level in _ENERGY_LEVELS:
        return _ENERGY_LEVELS[mood_detail.energy_level]
    if mood_detail.tempo_suggestion in _TEMPO_ENERGY:
        return _TEMPO_ENERGY[mood_detail.tempo_suggestion]
    return None
