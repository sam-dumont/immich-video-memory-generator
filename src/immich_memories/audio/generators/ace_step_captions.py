"""ACE-Step caption templates and prompt building.

Dense caption templates with genre, instruments, key, BPM, and time signature
for high-quality ACE-Step 1.5 music generation.

BPM, key_scale, and time_signature are returned separately so they can be
sent as explicit API parameters (not buried in the caption text).
"""

from __future__ import annotations

from dataclasses import dataclass

# Dense caption templates for ACE-Step.
# Each template uses a descriptive sentence (not just tags) for better LLM guidance.
# BPM, key, and time_signature are sent as separate API params.
# "instrumental, no vocals" is reinforced in every caption to prevent singing.
ACE_CAPTION_TEMPLATES: dict[str, dict[str, str | int]] = {
    "lofi": {
        "caption": (
            "A mellow lo-fi hip hop instrumental with dusty drum machine grooves, "
            "warm Rhodes electric piano chords, and smooth sub bass. "
            "Vinyl crackle texture, jazzy and relaxed. Instrumental, no vocals, no singing"
        ),
        "key": "D minor",
        "bpm": 75,
        "time_signature": "4/4",
    },
    "upbeat_pop": {
        "caption": (
            "A bright, upbeat pop instrumental with punchy drums, synth bass, "
            "and shimmering synth pads. Feel-good energy with handclaps "
            "and electric guitar accents. Instrumental, no vocals, no singing"
        ),
        "key": "C major",
        "bpm": 120,
        "time_signature": "4/4",
    },
    "indie_electronic": {
        "caption": (
            "Dreamy indie electronic instrumental with analog synths, "
            "a driving programmed beat, and arpeggiated melodies over warm pads. "
            "Atmospheric and hypnotic. Instrumental, no vocals, no singing"
        ),
        "key": "A minor",
        "bpm": 110,
        "time_signature": "4/4",
    },
    "tropical": {
        "caption": (
            "A warm, sunny electronic pop instrumental with plucked guitar loops, "
            "soft synth chords, light claps, and a groovy bass line. "
            "Relaxed summer feel with airy pads. Instrumental, no vocals, no singing"
        ),
        "key": "F major",
        "bpm": 112,
        "time_signature": "4/4",
    },
    "cinematic": {
        "caption": (
            "Epic cinematic orchestral instrumental with sweeping strings, "
            "powerful brass, timpani hits, and emotional piano melodies. "
            "Grand and dynamic. Instrumental, no vocals, no singing"
        ),
        "key": "E minor",
        "bpm": 90,
        "time_signature": "4/4",
    },
    "acoustic": {
        "caption": (
            "Gentle acoustic folk instrumental with fingerpicked guitar, "
            "soft brushed percussion, upright bass, and light piano. "
            "Warm and heartfelt. Instrumental, no vocals, no singing"
        ),
        "key": "G major",
        "bpm": 100,
        "time_signature": "4/4",
    },
    "future_bass": {
        "caption": (
            "Energetic future bass instrumental with massive supersaw synths, "
            "heavy sidechained bass drops, and snappy drums. "
            "Euphoric and bouncy with bright lead melodies. Instrumental, no vocals, no singing"
        ),
        "key": "Bb major",
        "bpm": 150,
        "time_signature": "4/4",
    },
    "jazz": {
        "caption": (
            "Smooth jazz instrumental with walking upright bass, brushed drums, "
            "mellow jazz guitar, tenor saxophone solos, and Rhodes piano. "
            "Sophisticated and laid-back. Instrumental, no vocals, no singing"
        ),
        "key": "F major",
        "bpm": 95,
        "time_signature": "4/4",
    },
    "ambient": {
        "caption": (
            "Lush ambient instrumental with ethereal reverb pads, "
            "granular textures, soft piano notes, and subtle wind chimes. "
            "Spacious and calming. Instrumental, no vocals, no singing"
        ),
        "key": "C major",
        "bpm": 70,
        "time_signature": "4/4",
    },
    "holiday": {
        "caption": (
            "Festive holiday instrumental with sleigh bells, glockenspiel, "
            "warm orchestral strings, gentle brass, and soft piano. "
            "Joyful and celebratory. Instrumental, no vocals, no singing"
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
) -> ACECaptionResult:
    """Build structured ACE-Step caption with explicit musical parameters.

    Returns an ACECaptionResult with caption text, lyrics, and separate
    bpm/key_scale/time_signature fields for the API.

    Args:
        mood: Mood string (e.g. "happy", "nostalgic")
        season: Optional season modifier ("winter", "summer", etc.)
        scene_moods: Optional list of per-scene mood strings for voting.
        memory_type: Optional memory type preset for template selection.
            Takes priority over mood when a known mapping exists.

    Returns:
        ACECaptionResult with all fields populated.
    """
    # Memory type takes priority for template selection
    if memory_type and memory_type in _MEMORY_TYPE_TO_TEMPLATE:
        template_name = _MEMORY_TYPE_TO_TEMPLATE[memory_type]
    elif scene_moods:
        template_name = _pick_template_for_scenes(scene_moods)
    else:
        template_name = _match_template(mood)

    template = ACE_CAPTION_TEMPLATES[template_name]

    # Use the descriptive caption directly from the template
    caption = str(template["caption"])

    # Add seasonal modifier if not already implied by the template
    if season:
        modifier = _SEASON_TAG_MODIFIERS.get(season.lower(), "")
        if modifier:
            caption = f"{caption}. {modifier}"

    return ACECaptionResult(
        caption=caption,
        lyrics="[Instrumental]",
        bpm=int(template["bpm"]),
        key_scale=str(template["key"]),
        time_signature=str(template["time_signature"]),
    )
