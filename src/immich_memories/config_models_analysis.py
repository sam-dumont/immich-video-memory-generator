"""Configuration models for what the pipeline learns about a clip.

Scene and segment detection, LLM content scoring, audio events, voice activity,
and transcription — the analysis passes that decide which clips are worth using
and where they should be cut.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

_CLIP_STYLE_PRESETS: dict[str, dict[str, float]] = {
    "fast-cuts": {
        "optimal_clip_duration": 3.0,
        "max_optimal_duration": 6.0,
        "target_extraction_ratio": 0.3,
        "max_segment_duration": 8.0,
        "min_segment_duration": 1.5,
    },
    "balanced": {
        "optimal_clip_duration": 5.0,
        "max_optimal_duration": 10.0,
        "target_extraction_ratio": 0.4,
        "max_segment_duration": 15.0,
        "min_segment_duration": 2.0,
    },
    "long-cuts": {
        "optimal_clip_duration": 8.0,
        "max_optimal_duration": 15.0,
        "target_extraction_ratio": 0.5,
        "max_segment_duration": 25.0,
        "min_segment_duration": 3.0,
    },
}


class AnalysisConfig(BaseModel):
    """Settings for video analysis."""

    download_workers: int = Field(
        default=3,
        ge=1,
        le=8,
        description="Concurrent isolated clients used for video and thumbnail prefetching",
    )
    max_refinement_passes: int = Field(
        default=10,
        ge=1,
        le=20,
        description="How many times selection may verify, judge and review before "
        "settling (#503). Three refinement loops run up to this many times, so it "
        "is the largest multiplier on warm-run time — and on the bill for anyone "
        "pointing llm.base_url at a paid API. Lower it to spend less per run; the "
        "cost is that a late refill may ship less scrutinised.",
    )
    max_album_assets: int = Field(
        default=10000,
        ge=1,
        description="Per media type, the most assets read from an album (#270). "
        "Smart albums reach tens of thousands; Immich returns newest first, so a "
        "larger album is truncated to its most recent assets.",
    )
    # The messaging-app globs carry their prefix and four-digit counter on
    # purpose: "*-wa[0-9]*" alone also matches a photograph of Olympia-WA2019.
    exclude_filename_patterns: list[str] = Field(
        default_factory=lambda: [
            "RingVideo_*",
            "RPReplay_Final*",
            "Screen Recording *",
            "Screenshot*",
            "img-*-wa[0-9][0-9][0-9][0-9]*",
            "vid-*-wa[0-9][0-9][0-9][0-9]*",
        ],
        description="Case-insensitive globs for source files a memory must "
        "never use. Settles for free, before analysis pays for the clip, what "
        "the holistic review would need a model to decide.",
    )
    exclude_stills_without_camera_exif: bool = Field(
        default=True,
        description="Drop photographs whose EXIF names no camera — on a real "
        "library 1532 of 1541 such stills were received or downloaded. Turn "
        "off for a library of exported originals. Videos are exempt.",
    )
    include_off_timeline_assets: bool = Field(
        default=False,
        description="Let assets Immich keeps off the timeline (archive, hidden, "
        "locked) into analysis. Generation overrides this to false whatever it "
        "says, so a flag left on after an experiment cannot put the locked "
        "folder into a video. Off by default.",
    )
    scene_threshold: float = Field(default=27.0, ge=1.0, le=100.0)
    min_scene_duration: float = Field(default=1.0, ge=0.5, le=10.0)
    duplicate_hash_threshold: int = Field(default=8, ge=0, le=64)

    # Clip style preset — sets the 5 duration params below.
    # Explicit overrides win over the preset.
    clip_style: Literal["fast-cuts", "balanced", "long-cuts"] | None = Field(
        default=None,
        description="Clip pacing preset (fast-cuts | balanced | long-cuts). "
        "Sets duration params below; explicit overrides win.",
    )

    # Scene detection settings
    use_scene_detection: bool = Field(
        default=True,
        description="Use scene detection for natural boundaries (enabled by default)",
    )
    max_segment_duration: float = Field(
        default=15.0,
        ge=2.0,
        le=30.0,
        description="Maximum segment duration in seconds (long scenes are subdivided)",
    )
    min_segment_duration: float = Field(
        default=2.0,
        ge=0.5,
        le=5.0,
        description="Minimum segment duration in seconds (clips shorter than this are discarded)",
    )
    optimal_clip_duration: float = Field(
        default=5.0,
        ge=2.0,
        le=15.0,
        description="Base sweet spot clip duration in seconds (scales up for longer sources)",
    )
    # 10.0 and 0.15 are what the pipeline has always actually run: these two
    # never reached the analyzer, so its constructor defaults were in force while
    # the documented 15.0/0.25 were never exercised. Wiring the config through
    # made the documented values live and moved the duration curve's peak on 32
    # of 92 clips, so the defaults were aligned to the behaviour instead.
    max_optimal_duration: float = Field(
        default=10.0,
        ge=5.0,
        le=30.0,
        description="Maximum optimal clip duration for long source videos",
    )
    target_extraction_ratio: float = Field(
        default=0.15,
        ge=0.05,
        le=0.5,
        description="Target ratio of clip to source duration (0.15 = 15% of source)",
    )

    @model_validator(mode="before")
    @classmethod
    def apply_clip_style(cls, data: dict) -> dict:
        """Expand clip_style preset into duration params (explicit overrides win)."""
        if not isinstance(data, dict):
            return data
        style = data.get("clip_style")
        if style is None:
            return data
        if style not in _CLIP_STYLE_PRESETS:
            raise ValueError(
                f"Invalid clip_style '{style}'. Choose from: {', '.join(_CLIP_STYLE_PRESETS)}"
            )
        preset = _CLIP_STYLE_PRESETS[style]
        for key, value in preset.items():
            if key not in data:
                data[key] = value
        return data

    # Live Photo settings
    include_live_photos: bool = Field(
        default=True,
        description="Include Live Photo video clips (3s clips from iPhone Live Photos)",
    )
    live_photo_merge_window_seconds: float = Field(
        default=10.0,
        ge=1.0,
        le=60.0,
        description="Max gap between Live Photos to group into a burst cluster",
    )
    # A lone Live Photo stitches to exactly the raw 3.0s with nothing merged,
    # and the smallest genuine merge of two reaches 4.0s, so the boundary sits
    # between them. Measured, not chosen.
    live_photo_min_clip_seconds: float = Field(
        default=3.5,
        ge=0.0,
        le=30.0,
        description="Below this a burst renders as a photograph rather than as motion",
    )

    @model_validator(mode="after")
    def validate_duration_constraints(self) -> AnalysisConfig:
        """Ensure min_segment_duration < max_segment_duration and related constraints."""
        if self.min_segment_duration >= self.max_segment_duration:
            raise ValueError(
                f"min_segment_duration ({self.min_segment_duration}) must be less than "
                f"max_segment_duration ({self.max_segment_duration})"
            )
        if self.min_segment_duration >= self.optimal_clip_duration:
            raise ValueError(
                f"min_segment_duration ({self.min_segment_duration}) must be less than "
                f"optimal_clip_duration ({self.optimal_clip_duration})"
            )
        if self.optimal_clip_duration > self.max_optimal_duration:
            raise ValueError(
                f"optimal_clip_duration ({self.optimal_clip_duration}) must not exceed "
                f"max_optimal_duration ({self.max_optimal_duration})"
            )
        return self

    # Speed optimization: downscale videos for analysis
    enable_downscaling: bool = Field(
        default=True,
        description="Downscale videos before analysis for speed (~3-5x faster)",
    )
    analysis_resolution: int = Field(
        default=480,
        ge=240,
        le=1080,
        description="Target height for analysis (480 = 480p). Lower = faster.",
    )

    # Unified analysis settings (audio-aware boundaries)
    use_unified_analysis: bool = Field(
        default=True,
        description="Use unified analysis with audio-aware boundaries to avoid mid-sentence cuts",
    )
    cut_point_merge_tolerance: float = Field(
        default=0.5,
        ge=0.1,
        le=2.0,
        description="Time window (seconds) for merging nearby visual/audio boundaries",
    )
    silence_threshold_db: float = Field(
        default=-40.0,
        ge=-60.0,
        le=-10.0,
        description="Audio level threshold in dB for silence detection (lower = more sensitive)",
    )
    min_silence_duration: float = Field(
        # 0.3 for the same reason as the duration fields above: this never
        # reached the analyzer, so 0.3 is the gap width silence detection has
        # actually been using.
        default=0.3,
        ge=0.1,
        le=1.0,
        description="Minimum duration (seconds) of quiet audio to count as a silence gap",
    )
    min_source_short_side: int = Field(
        default=1080,
        ge=0,
        description=(
            "Unknown clips below this short side are dropped; it also enables the "
            "measured 2048px UUID-JPEG forwarded-media fingerprint. Camera EXIF, a "
            "favorite, and media captured before 2008 override the inference"
        ),
    )
    subject_policy_enabled: bool = Field(
        default=True,
        description="Prefer clips of people; ration animals and exclude object-only clips",
    )
    max_animal_ratio: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description=(
            "Share of a video that may be animal-only clips, so the allowance "
            "scales with length (0 disables them entirely)"
        ),
    )
    max_object_ratio: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description=(
            "Share of a video that may be object-only clips. They must also beat "
            "the median people clip, which is what separates a new car from a lawnmower"
        ),
    )


class ContentAnalysisConfig(BaseModel):
    """Settings for LLM-based content analysis."""

    enabled: bool = Field(
        default=False,
        description="Enable LLM content analysis (slower but more intelligent scoring)",
    )
    weight: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description="Weight of content score in overall scoring (0-1)",
    )

    # Analysis parameters
    analyze_frames: int = Field(
        default=2,
        ge=1,
        le=4,
        description="Number of frames to analyze per segment (reduced from 3 for speed)",
    )
    min_confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum confidence threshold to use content analysis score",
    )

    # Frame optimization parameters
    frame_max_height: int = Field(
        default=480,
        ge=240,
        le=1080,
        description="Max frame height for LLM analysis (480=fast/cheap, 720=balanced, 1080=quality)",
    )
    openai_image_detail: Literal["low", "high", "auto"] = Field(
        default="low",
        description="OpenAI image detail level (low=85 tokens/cheap, high=1889 tokens/detailed)",
    )


class AudioContentConfig(BaseModel):
    """Settings for audio content analysis (laughter/speech detection)."""

    enabled: bool = Field(
        default=False,
        description="Enable audio content analysis for laughter/speech detection",
    )
    weight: float = Field(
        default=0.15,
        ge=0.0,
        le=0.5,
        description="Weight of audio content score in overall scoring (0-0.5)",
    )

    # Detection settings
    use_panns: bool = Field(
        default=True,
        description="Use PANNs ML model for audio classification (requires torch)",
    )
    min_confidence: float = Field(
        default=0.3,
        ge=0.1,
        le=0.9,
        description="Minimum confidence for audio event detection",
    )
    laughter_confidence: float = Field(
        default=0.1,
        ge=0.1,
        le=0.5,
        description=(
            "Lower confidence threshold for laughter/baby sounds (often quieter). "
            "At 0.2 more than half the laughter in a real library never fires an "
            "event; 0.1 raises recall from 47% to 79% and costs some precision"
        ),
    )

    # Laughter bonus
    laughter_bonus: float = Field(
        default=0.1,
        ge=0.0,
        le=0.3,
        description="Extra score bonus for segments containing laughter",
    )

    # Boundary protection
    protect_laughter: bool = Field(
        default=True,
        description="Avoid cutting during laughter events",
    )
    protect_speech: bool = Field(
        default=True,
        description="Avoid cutting during speech events",
    )


class SpeechConfig(BaseModel):
    """Settings for speech-derived segment boundaries.

    FireRedVAD is the only detector, so there is no engine selector: `enabled:
    false` is how you turn voice activity off and fall back to PANNs-derived
    protected ranges.
    """

    enabled: bool = Field(
        default=True,
        description="Derive segment boundaries from voice activity instead of PANNs speech tags",
    )
    vad_threshold: float = Field(
        default=0.25,
        ge=0.1,
        le=0.9,
        description=(
            "Speech probability above which a frame counts as voice activity. "
            "Below FireRedVAD upstream's default "
            "of 0.4: measured across 143 library clips, 0.25 detects speech in every "
            "clip the removed Silero engine did plus 49 more, with zero false "
            "positives on clips below -40 dBFS"
        ),
    )
    min_silence_ms: int = Field(
        default=200,
        ge=50,
        le=2000,
        description=(
            "Silence needed before a speech region is closed (milliseconds). Also "
            "caps how far protected ranges are widened before boundary adjustment, "
            "so the pauses split here survive"
        ),
    )


class TranscriptionConfig(BaseModel):
    """Settings for speech transcription of candidate clips.

    Transcription is gated on voice activity, so it needs `speech.enabled`. With
    speech off there are no VAD regions, so there is no gate, so nothing is
    transcribed.
    """

    enabled: bool = Field(
        default=False,
        description="Transcribe speech in the top candidate clips",
    )
    languages: list[str] = Field(
        default_factory=list,
        description=(
            "Languages the library actually contains, e.g. ['fr', 'en']. One entry "
            "forces that language and skips detection; several restrict detection to "
            "that set. Empty means no transcription -- automatic detection across all "
            "99 languages put French audio in Japanese and in German, twice out of two"
        ),
    )
    model: str = Field(
        default="medium",
        description=(
            "ggml model name (tiny/base/small/medium/large) or a path to a model file. "
            "medium measured markedly better than base on real family audio -- base "
            "returned '- Dear.' and 'La papa.' where medium returned whole sentences -- "
            "for about 0.6s per segment. It is a ~1.5 GB first-use download"
        ),
    )
    min_voiced_seconds: float = Field(
        default=1.0,
        ge=0.0,
        le=10.0,
        description=(
            "Voice activity required inside a clip before it is transcribed. Unmeasured "
            "starting value: VAD already returns no regions at all on roughly half a "
            "real library, which is where most of the saving comes from"
        ),
    )
    min_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Mean token probability below which a transcript is discarded. Defaults to "
            "0.0 because this signal is inverted on real audio: correct transcripts "
            "measured 0.63-0.71 and fluent nonsense 0.84-0.95, so raising the floor "
            "removes good output before bad. The voice-activity floor and the repetition "
            "guard do the filtering"
        ),
    )
    use_gpu: bool = Field(
        default=True,
        description="Use GPU when the installed whisper.cpp build supports it (Metal on macOS)",
    )
