"""ACE-Step caption templates and prompt building.

Dense caption templates with genre, instruments, key, BPM, and time signature
for high-quality ACE-Step 1.5 music generation.

BPM, key_scale, and time_signature are returned separately so they can be
sent as explicit API parameters (not buried in the caption text).
"""

from __future__ import annotations

from dataclasses import dataclass

# Dense caption templates for ACE-Step.
# Each template specifies genre, instruments, key, BPM, time signature.
# Tested prompts that produce good results on ACE-Step 1.5 turbo.
ACE_CAPTION_TEMPLATES: dict[str, dict[str, str | int]] = {
    "lofi": {
        "tags": "lo-fi hip hop, chill beats, jazzy, mellow, smooth, vinyl crackle",
        "instruments": "dusty drum machine, Rhodes electric piano, warm sub bass, vinyl noise, soft guitar",
        "key": "D minor",
        "bpm": 75,
        "time_signature": "4/4",
    },
    "upbeat_pop": {
        "tags": "pop, upbeat, feel-good, bright, catchy",
        "instruments": "punchy drums, synth bass, bright synth pads, claps, electric guitar",
        "key": "C major",
        "bpm": 120,
        "time_signature": "4/4",
    },
    "indie_electronic": {
        "tags": "indie electronic, dreamy, atmospheric, driving beat",
        "instruments": "analog synths, programmed drums, arpeggiated synth, warm pads, subtle bass",
        "key": "A minor",
        "bpm": 110,
        "time_signature": "4/4",
    },
    "tropical": {
        "tags": "tropical house, sunny, bouncy, fun, summery",
        "instruments": "steel drums, marimba, tropical percussion, synth bass, bright pads",
        "key": "F major",
        "bpm": 115,
        "time_signature": "4/4",
    },
    "cinematic": {
        "tags": "cinematic, epic, orchestral, emotional, powerful",
        "instruments": "strings ensemble, brass section, timpani, piano, choir pads",
        "key": "E minor",
        "bpm": 90,
        "time_signature": "4/4",
    },
    "acoustic": {
        "tags": "acoustic, warm, gentle, folk, heartfelt",
        "instruments": "acoustic guitar, soft percussion, upright bass, light piano, subtle strings",
        "key": "G major",
        "bpm": 100,
        "time_signature": "4/4",
    },
    "future_bass": {
        "tags": "future bass, energetic, euphoric, bright, bouncy",
        "instruments": "supersaw synths, heavy sidechained bass, chopped vocal chops, snappy drums, bright leads",
        "key": "Bb major",
        "bpm": 150,
        "time_signature": "4/4",
    },
    "jazz": {
        "tags": "jazz, smooth, sophisticated, laid-back",
        "instruments": "upright bass, brushed drums, jazz guitar, tenor saxophone, Rhodes piano",
        "key": "F major",
        "bpm": 95,
        "time_signature": "4/4",
    },
    "ambient": {
        "tags": "ambient, ethereal, spacious, calming, atmospheric",
        "instruments": "lush reverb pads, granular textures, soft piano, wind chimes, subtle field recordings",
        "key": "C major",
        "bpm": 70,
        "time_signature": "4/4",
    },
    "holiday": {
        "tags": "holiday, festive, joyful, warm, celebratory",
        "instruments": "sleigh bells, glockenspiel, warm strings, piano, gentle brass, soft drums",
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
    # Include key/BPM in tags string for backwards compat
    tags = f"{result.caption}, key of {result.key_scale}"
    return tags, result.lyrics


def _match_template(mood: str) -> str:
    """Match a single mood word to a template name.

    Uses exact word matching (not substring) to avoid false positives.
    For example, "energetic calm" should match "energetic" OR "calm",
    not accidentally match "happy" because it appears in some other word.

    Args:
        mood: Single mood string (e.g. "happy", "nostalgic", "energetic calm")

    Returns:
        Template name from ACE_CAPTION_TEMPLATES.
    """
    mood_words = set(mood.lower().split())

    # Check each mood word against the mapping
    for word in mood_words:
        # Strip commas/punctuation from individual words
        word = word.strip(",. ")
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

    # Build caption without BPM/key/time_sig (those go as explicit params)
    caption = f"{template['tags']}, {template['instruments']}, instrumental, loop background"

    # Add seasonal modifier
    if season:
        modifier = _SEASON_TAG_MODIFIERS.get(season.lower(), "")
        if modifier:
            caption = f"{caption}, {modifier}"

    return ACECaptionResult(
        caption=caption,
        lyrics="[Instrumental]",
        bpm=int(template["bpm"]),
        key_scale=str(template["key"]),
        time_signature=str(template["time_signature"]),
    )
