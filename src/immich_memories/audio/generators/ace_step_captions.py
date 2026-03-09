"""ACE-Step caption templates and prompt building.

Dense caption templates with genre, instruments, key, BPM, and time signature
for high-quality ACE-Step 1.5 music generation.
"""

from __future__ import annotations

# Dense caption templates for ACE-Step.
# Each template specifies genre, instruments, key, BPM, time signature.
# Tested prompts that produce good results on ACE-Step 1.5 turbo.
ACE_CAPTION_TEMPLATES = {
    "lofi": {
        "tags": "lo-fi hip hop, chill beats, instrumental, jazzy, mellow, smooth, vinyl crackle, 75 BPM, 4/4",
        "instruments": "dusty drum machine, Rhodes electric piano, warm sub bass, vinyl noise, soft guitar",
        "key": "D minor",
    },
    "upbeat_pop": {
        "tags": "pop, upbeat, feel-good, bright, catchy, instrumental, 120 BPM, 4/4",
        "instruments": "punchy drums, synth bass, bright synth pads, claps, electric guitar",
        "key": "C major",
    },
    "indie_electronic": {
        "tags": "indie electronic, dreamy, atmospheric, driving beat, instrumental, 110 BPM, 4/4",
        "instruments": "analog synths, programmed drums, arpeggiated synth, warm pads, subtle bass",
        "key": "A minor",
    },
    "tropical": {
        "tags": "tropical house, sunny, bouncy, fun, summery, instrumental, 115 BPM, 4/4",
        "instruments": "steel drums, marimba, tropical percussion, synth bass, bright pads",
        "key": "F major",
    },
    "cinematic": {
        "tags": "cinematic, epic, orchestral, emotional, powerful, instrumental, 90 BPM, 4/4",
        "instruments": "strings ensemble, brass section, timpani, piano, choir pads",
        "key": "E minor",
    },
    "acoustic": {
        "tags": "acoustic, warm, gentle, folk, heartfelt, instrumental, 100 BPM, 4/4",
        "instruments": "acoustic guitar, soft percussion, upright bass, light piano, subtle strings",
        "key": "G major",
    },
    "future_bass": {
        "tags": "future bass, energetic, euphoric, bright, bouncy, instrumental, 150 BPM, 4/4",
        "instruments": "supersaw synths, heavy sidechained bass, chopped vocal chops, snappy drums, bright leads",
        "key": "Bb major",
    },
    "jazz": {
        "tags": "jazz, smooth, sophisticated, laid-back, instrumental, 95 BPM, 4/4",
        "instruments": "upright bass, brushed drums, jazz guitar, tenor saxophone, Rhodes piano",
        "key": "F major",
    },
    "ambient": {
        "tags": "ambient, ethereal, spacious, calming, atmospheric, instrumental, 70 BPM, 4/4",
        "instruments": "lush reverb pads, granular textures, soft piano, wind chimes, subtle field recordings",
        "key": "C major",
    },
    "holiday": {
        "tags": "holiday, festive, joyful, warm, celebratory, instrumental, 110 BPM, 4/4",
        "instruments": "sleigh bells, glockenspiel, warm strings, piano, gentle brass, soft drums",
        "key": "G major",
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

# Seasonal modifiers appended to tags
_SEASON_TAG_MODIFIERS = {
    "winter": "cozy, warm tones, intimate",
    "spring": "fresh, bright, blossoming",
    "summer": "sunny, carefree, vibrant",
    "autumn": "warm, golden, mellow",
    "holiday": "festive, joyful, celebratory",
}


def build_ace_caption(mood: str, season: str | None = None) -> tuple[str, str]:
    """Build ACE-Step tags and lyrics from mood + optional season.

    Returns dense caption with genre, instruments, key, BPM, time signature.

    Args:
        mood: Mood string (e.g. "happy", "upbeat warm groovy calm")
        season: Optional season modifier ("winter", "summer", etc.)

    Returns:
        Tuple of (tags, lyrics).
    """
    mood_lower = mood.lower()

    # Find best matching template
    template_name = "upbeat_pop"  # default
    for keyword, tpl_name in _MOOD_TO_TEMPLATE.items():
        if keyword in mood_lower:
            template_name = tpl_name
            break

    template = ACE_CAPTION_TEMPLATES[template_name]
    tags = f"{template['tags']}, {template['instruments']}, key of {template['key']}"

    # Add seasonal modifier
    if season:
        modifier = _SEASON_TAG_MODIFIERS.get(season.lower(), "")
        if modifier:
            tags = f"{tags}, {modifier}"

    lyrics = "[Instrumental]"
    return tags, lyrics
