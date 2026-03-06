"""Audio processing module for automatic music and mixing."""

from immich_memories.audio.content_analyzer import (
    AudioAnalysisResult,
    AudioContentAnalyzer,
    AudioEvent,
    adjust_boundaries_for_audio,
    get_audio_content_score,
)
from immich_memories.audio.mixer import (
    AudioMixer,
    loop_audio_to_duration,
    mix_audio_with_ducking,
    mix_audio_with_stem_ducking,
)
from immich_memories.audio.mood_analyzer import (
    MoodAnalyzer,
    OllamaMoodAnalyzer,
    OpenAIMoodAnalyzer,
    VideoMood,
    get_mood_analyzer,
)
from immich_memories.audio.music_generator import (
    ClipMood,
    GeneratedMusic,
    MusicGenClient,
    MusicGenClientConfig,
    MusicGenConfig,
    MusicGenerationResult,
    MusicStems,
    StemDuckingConfig,
    VideoTimeline,
    generate_music_for_video,
    generate_music_sync,
)
from immich_memories.audio.music_sources import (
    LocalMusicSource,
    MusicSource,
    MusicTrack,
    PixabayMusicSource,
    get_music_source,
)

__all__ = [
    # Music sources
    "MusicTrack",
    "MusicSource",
    "PixabayMusicSource",
    "LocalMusicSource",
    "get_music_source",
    # AI Music generation
    "ClipMood",
    "MusicGenClient",
    "MusicGenClientConfig",
    "MusicGenConfig",
    "VideoTimeline",
    "MusicStems",
    "GeneratedMusic",
    "MusicGenerationResult",
    "StemDuckingConfig",
    "generate_music_for_video",
    "generate_music_sync",
    # Mood analysis
    "VideoMood",
    "MoodAnalyzer",
    "OllamaMoodAnalyzer",
    "OpenAIMoodAnalyzer",
    "get_mood_analyzer",
    # Audio content analysis
    "AudioEvent",
    "AudioAnalysisResult",
    "AudioContentAnalyzer",
    "adjust_boundaries_for_audio",
    "get_audio_content_score",
    # Audio mixing
    "AudioMixer",
    "mix_audio_with_ducking",
    "mix_audio_with_stem_ducking",
    "loop_audio_to_duration",
]
