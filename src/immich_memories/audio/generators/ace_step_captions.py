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
ACE_CAPTION_TEMPLATES: dict[str, dict[str, str | int]] = {
    "lofi": {
        "caption": (
            "lo-fi jazz, wistful, Rhodes electric piano, double bass, brushed "
            "drums, muted trumpet, lo-fi, dusty, vinyl crackle, 75 bpm"
        ),
        "key": "D minor",
        "bpm": 75,
        "time_signature": "4/4",
    },
    "upbeat_pop": {
        "caption": (
            "acoustic pop, joyful, fingerpicked acoustic guitar, live drum kit, "
            "upright bass, glockenspiel, handclaps, hi-fi, polished, wide stereo, "
            "120 bpm"
        ),
        "key": "C major",
        "bpm": 120,
        "time_signature": "4/4",
    },
    "indie_electronic": {
        "caption": (
            "indie electronic, dreamy, analog synth, electric bass, programmed "
            "beat, electric piano, soft pad, polished, wide stereo, 110 bpm"
        ),
        "key": "A minor",
        "bpm": 110,
        "time_signature": "4/4",
    },
    "tropical": {
        "caption": (
            "tropical house, sunny, steel drums, nylon guitar, electric bass, "
            "shaker, marimba, hi-fi, polished, wide stereo, 112 bpm"
        ),
        "key": "F major",
        "bpm": 112,
        "time_signature": "4/4",
    },
    "cinematic": {
        "caption": (
            "orchestral, uplifting, warm string section, French horn, timpani, "
            "harp, celeste, hi-fi, cinematic, 90 bpm"
        ),
        "key": "E minor",
        "bpm": 90,
        "time_signature": "4/4",
    },
    "acoustic": {
        "caption": (
            "acoustic folk, tender, nylon guitar, upright bass, cello, brushed "
            "drums, glockenspiel, analog warmth, intimate, 100 bpm"
        ),
        "key": "G major",
        "bpm": 100,
        "time_signature": "4/4",
    },
    "future_bass": {
        "caption": (
            "indie rock, driving, live drum kit, electric bass, electric guitar, "
            "organ, tambourine, hi-fi, wide stereo, 150 bpm"
        ),
        "key": "Bb major",
        "bpm": 150,
        "time_signature": "4/4",
    },
    "jazz": {
        "caption": (
            "jazz trio, relaxed, grand piano, upright bass, brushed drums, muted "
            "trumpet, vibraphone, analog warmth, intimate, 95 bpm"
        ),
        "key": "F major",
        "bpm": 95,
        "time_signature": "4/4",
    },
    "ambient": {
        "caption": (
            "ambient, serene, felt piano, cello, harp, soft strings, hi-fi, intimate, 70 bpm"
        ),
        "key": "C major",
        "bpm": 70,
        "time_signature": "4/4",
    },
    "holiday": {
        "caption": (
            "orchestral, festive, celeste, sleigh bells, warm strings, French horn, "
            "harp, hi-fi, cinematic, 110 bpm"
        ),
        "key": "G major",
        "bpm": 110,
        "time_signature": "4/4",
    },
}

# Maps mood keywords to caption template names
_MOOD_TO_TEMPLATE = {
    "happy": "upbeat_pop",
    "energetic": "future_bass",
    "calm": "ambient",
    "nostalgic": "lofi",
    "romantic": "acoustic",
    "playful": "indie_electronic",
    "dramatic": "cinematic",
    "upbeat": "upbeat_pop",
    "peaceful": "ambient",
    "inspiring": "cinematic",
    "groovy": "lofi",
    "warm": "acoustic",
    "fun": "tropical",
    "sunny": "tropical",
    "dreamy": "indie_electronic",
    "jazzy": "jazz",
    "holiday": "holiday",
    "festive": "holiday",
    "cozy": "lofi",
    "mysterious": "ambient",
    "tender": "acoustic",
    "melancholic": "lofi",
    "exciting": "future_bass",
    "uplifting": "cinematic",
}

# Maps memory type presets to preferred music templates.
# Memory type takes priority over mood when provided.
_MEMORY_TYPE_TO_TEMPLATE: dict[str, str] = {
    "trip": "tropical",
    "season": "indie_electronic",
    "person_spotlight": "acoustic",
    "on_this_day": "lofi",
    "monthly_highlights": "upbeat_pop",
    "multi_person": "upbeat_pop",
    "year_in_review": "cinematic",
}

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
MOOD_PROFILES: dict[str, MoodProfile] = {
    "calm": MoodProfile("serene", 0.00, "major", "hi-fi, intimate"),
    "nostalgic": MoodProfile("wistful", 0.15, "minor", "analog warmth, tape saturation"),
    "tender": MoodProfile("tender", 0.35, "major", "analog warmth, intimate"),
    "happy": MoodProfile("joyful", 0.65, "major", "hi-fi, polished, wide stereo"),
    "energetic": MoodProfile("driving", 1.00, "minor", "hi-fi, wide stereo"),
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
            "tender": "chillwave",
            "nostalgic": "trip hop",
            "happy": "future bass",
            "energetic": "drum and bass",
        },
    ),
}

# Memory types that suggest a style; otherwise the style is sampled for variety.
_MEMORY_TYPE_TO_STYLE: dict[str, str] = {
    "person_spotlight": "acoustic",
    "on_this_day": "electronic",
}

_MOOD_ALIASES: dict[str, str] = {
    "upbeat": "happy",
    "fun": "happy",
    "sunny": "happy",
    "playful": "happy",
    "exciting": "energetic",
    "uplifting": "energetic",
    "inspiring": "energetic",
    "peaceful": "calm",
    "mysterious": "calm",
    "dreamy": "calm",
    "cozy": "calm",
    "melancholic": "nostalgic",
    "sad": "nostalgic",
    "groovy": "nostalgic",
    "jazzy": "nostalgic",
    "dramatic": "orchestral_mood",
    "romantic": "tender",
    "warm": "tender",
    "holiday": "tender",
    "festive": "happy",
    "hopeful": "tender",
}


def resolve_mood(mood: str) -> str:
    """Map any mood phrase onto one of the five profiles."""
    words = [w.strip(",.! ") for w in mood.lower().split() if w.strip(",.! ")]
    for word in words:
        if word in MOOD_PROFILES:
            return word
    for word in words:
        alias = _MOOD_ALIASES.get(word)
        if alias in MOOD_PROFILES:
            return alias
    return "happy"


def pick_style(memory_type: str | None = None, style: str | None = None) -> str:
    """Choose the style for this generation.

    An explicit style wins, then a memory type with a natural fit; otherwise one
    is sampled so repeated memories of the same mood do not all sound alike.
    """
    if style in STYLE_PROFILES:
        return style
    if memory_type and memory_type in _MEMORY_TYPE_TO_STYLE:
        return _MEMORY_TYPE_TO_STYLE[memory_type]
    return random.choice(sorted(STYLE_PROFILES))


def compose_caption(mood_key: str, style_key: str) -> tuple[str, int, str]:
    """Build the caption on ACE-Step's documented order, with its tempo and key.

    Returns (caption, bpm, key_scale).
    """
    profile, style = MOOD_PROFILES[mood_key], STYLE_PROFILES[style_key]
    bpm = style.bpm_for(profile.energy)
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
    """Structured result from build_ace_caption.

    Separates musical parameters so they can be sent as explicit
    API fields rather than embedded in the caption text.
    """

    caption: str
    lyrics: str
    bpm: int
    key_scale: str
    time_signature: str


def build_ace_caption(mood: str, season: str | None = None) -> tuple[str, str]:
    """Build ACE-Step tags and lyrics from mood + optional season.

    Returns caption with genre and instruments. BPM/key/time_signature
    are NOT included in the caption — use build_ace_caption_structured()
    to get them as separate fields for the API.

    Args:
        mood: Mood string (e.g. "happy", "upbeat warm groovy calm")
        season: Optional season modifier ("winter", "summer", etc.)

    Returns:
        Tuple of (tags, lyrics) for backwards compatibility.
    """
    result = build_ace_caption_structured(mood, season=season)
    # Include key in caption string for backwards compat (lib mode)
    tags = f"{result.caption}. Key of {result.key_scale}"
    return tags, result.lyrics


def _match_template(mood: str) -> str:
    """Match a single mood word to a template name.

    Prioritizes specific mood words over generic booster words that
    _transform_mood() prepends. For example, "upbeat romantic" should
    match "romantic" → acoustic, not "upbeat" → upbeat_pop.

    Args:
        mood: Single mood string (e.g. "happy", "nostalgic", "upbeat romantic")

    Returns:
        Template name from ACE_CAPTION_TEMPLATES.
    """
    # Generic words that _transform_mood prepends to everything.
    # These should only match if no more specific word is found.
    _BOOSTER_WORDS = {"upbeat", "warm", "groovy", "hopeful"}

    mood_words = [w.strip(",. ") for w in mood.lower().split() if w.strip(",. ")]

    # First pass: check specific (non-booster) words
    for word in mood_words:
        if word not in _BOOSTER_WORDS and word in _MOOD_TO_TEMPLATE:
            return _MOOD_TO_TEMPLATE[word]

    # Second pass: fall back to booster words
    for word in mood_words:
        if word in _MOOD_TO_TEMPLATE:
            return _MOOD_TO_TEMPLATE[word]

    return "upbeat_pop"


def _pick_template_for_scenes(scene_moods: list[str]) -> str:
    """Pick the best template by voting across scene moods.

    Each scene's mood votes for a template. The template with the most
    votes wins, with random tiebreaking for variety.

    Args:
        scene_moods: List of mood strings from individual scenes.

    Returns:
        Template name from ACE_CAPTION_TEMPLATES.
    """
    import random
    from collections import Counter

    if not scene_moods:
        return "upbeat_pop"

    votes: list[str] = [_match_template(mood) for mood in scene_moods]
    counts = Counter(votes)

    # Get all templates tied for the most votes
    max_count = counts.most_common(1)[0][1]
    top_templates = [tpl for tpl, count in counts.items() if count == max_count]

    return random.choice(top_templates)


def build_ace_caption_structured(
    mood: str,
    season: str | None = None,
    scene_moods: list[str] | None = None,
    memory_type: str | None = None,
    style: str | None = None,
) -> ACECaptionResult:
    """Build a structured ACE-Step caption from the mood x style matrix.

    Mood decides tempo and emotional register; style decides genre and
    instruments. Without an explicit style one is sampled, so repeated memories
    of the same mood do not all come back sounding the same.
    """
    mood_key = resolve_mood(scene_moods[0] if scene_moods else mood)
    style_key = pick_style(memory_type=memory_type, style=style)
    caption, bpm, key_scale = compose_caption(mood_key, style_key)

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
