"""Audio processing module for automatic music and mixing."""

import importlib as _importlib

__all__ = [
    # Music sources
    "MusicTrack",
    "MusicSource",
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
    "OpenAICompatibleMoodAnalyzer",
    "OpenAIMoodAnalyzer",  # backwards compat alias
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
    # Music generation backends
    "MusicGenerator",
    "GenerationRequest",
    "GenerationResult",
    "MusicGenBackend",
    "ACEStepBackend",
    "create_generator",
]

_SUBMODULE_MAP = {
    "AudioAnalysisResult": "immich_memories.audio.content_analyzer",
    "AudioContentAnalyzer": "immich_memories.audio.content_analyzer",
    "AudioEvent": "immich_memories.audio.content_analyzer",
    "adjust_boundaries_for_audio": "immich_memories.audio.content_analyzer",
    "get_audio_content_score": "immich_memories.audio.content_analyzer",
    "AudioMixer": "immich_memories.audio.mixer",
    "loop_audio_to_duration": "immich_memories.audio.mixer",
    "mix_audio_with_ducking": "immich_memories.audio.mixer",
    "mix_audio_with_stem_ducking": "immich_memories.audio.mixer",
    "MoodAnalyzer": "immich_memories.audio.mood_analyzer",
    "OllamaMoodAnalyzer": "immich_memories.audio.mood_analyzer",
    "OpenAICompatibleMoodAnalyzer": "immich_memories.audio.mood_analyzer",
    "OpenAIMoodAnalyzer": "immich_memories.audio.mood_analyzer",
    "VideoMood": "immich_memories.audio.mood_analyzer",
    "get_mood_analyzer": "immich_memories.audio.mood_analyzer",
    "ClipMood": "immich_memories.audio.music_generator",
    "GeneratedMusic": "immich_memories.audio.music_generator",
    "MusicGenClient": "immich_memories.audio.music_generator",
    "MusicGenClientConfig": "immich_memories.audio.music_generator",
    "MusicGenConfig": "immich_memories.audio.music_generator",
    "MusicGenerationResult": "immich_memories.audio.music_generator",
    "MusicStems": "immich_memories.audio.music_generator",
    "StemDuckingConfig": "immich_memories.audio.music_generator",
    "VideoTimeline": "immich_memories.audio.music_generator",
    "generate_music_for_video": "immich_memories.audio.music_generator",
    "generate_music_sync": "immich_memories.audio.music_generator",
    "LocalMusicSource": "immich_memories.audio.music_sources",
    "MusicSource": "immich_memories.audio.music_sources",
    "MusicTrack": "immich_memories.audio.music_sources",
    "get_music_source": "immich_memories.audio.music_sources",
    "MusicGenerator": "immich_memories.audio.generators.base",
    "GenerationRequest": "immich_memories.audio.generators.base",
    "GenerationResult": "immich_memories.audio.generators.base",
    "MusicGenBackend": "immich_memories.audio.generators.musicgen_backend",
    "ACEStepBackend": "immich_memories.audio.generators.ace_step_backend",
    "create_generator": "immich_memories.audio.generators.factory",
}


def __getattr__(name: str):
    if name in _SUBMODULE_MAP:
        module = _importlib.import_module(_SUBMODULE_MAP[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
