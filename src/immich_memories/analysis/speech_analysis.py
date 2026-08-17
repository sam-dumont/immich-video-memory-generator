"""Audio content and speech-boundary analysis.

Owns the PANNs audio-content analyzer and the VAD speech detector, plus their
per-video caches, and computes the protected ranges — time spans a segment
boundary must not cut through (speech, laughter, applause, cheering).
"""

from __future__ import annotations

import logging
import operator
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from immich_memories.audio.audio_models import SPEECH_EVENT_CLASS
from immich_memories.config_models import SpeechConfig, TranscriptionConfig
from immich_memories.speech.models import SpeechRegion
from immich_memories.speech.transcription import select_transcriber
from immich_memories.speech.vad import VAD_SAMPLE_RATE, extract_audio_16k, select_detector

if TYPE_CHECKING:
    from immich_memories.audio.audio_models import AudioAnalysisResult, AudioEvent
    from immich_memories.config_models import AudioContentConfig
    from immich_memories.speech.transcription import Transcriber, Transcript

logger = logging.getLogger(__name__)


def protected_ranges_from_speech(
    regions: list[SpeechRegion],
    max_duration: float,
) -> list[tuple[float, float]]:
    """Convert VAD regions into protected ranges, clamped to the analysed window."""
    ranges: list[tuple[float, float]] = []
    for region in regions:
        if region.start >= max_duration:
            continue
        ranges.append((region.start, min(region.end, max_duration)))
    return ranges


def non_speech_protected_ranges(
    events: list[AudioEvent],
    max_duration: float,
) -> list[tuple[float, float]]:
    """Protected ranges for the events VAD cannot see.

    FireRedVAD supersedes PANNs only for speech. Its speech column does not
    fire on laughter, applause or cheering, so those events must keep their
    PANNs-derived ranges or a clip of someone laughing loses all protection
    and a cut lands mid-laugh.
    """
    return [
        (event.start_time, min(event.end_time, max_duration))
        for event in events
        if event.is_protected
        and event.event_class != SPEECH_EVENT_CLASS
        and event.start_time < max_duration
    ]


def voiced_seconds(regions: list[SpeechRegion], start: float, end: float) -> float:
    """Total voice-activity time inside [start, end]."""
    return sum(max(0.0, min(region.end, end) - max(region.start, start)) for region in regions)


# Whisper is trained on 30-second windows and pads anything shorter with silence,
# which is a documented hallucination trigger. Measured on a real library, feeding it
# 2-5 second slices produced confident nonsense -- "- Dear.", "La papa." -- where a
# 30-second window around the same moment produced "Il est mignon. Tu veux lui faire
# une petite douce ? Pas la tête, pas le ventre."
TRANSCRIPTION_WINDOW_SECONDS = 30.0


def transcription_window(audio_len_seconds: float, start: float, end: float) -> tuple[float, float]:
    """The span of audio to hand the model for a segment.

    Slides rather than shrinks near the ends of a file, so a clip at 0s still gets a
    full window. A segment longer than the window is returned unchanged -- windowing
    it would hand the model less audio than the clip itself contains.
    """
    if end - start >= TRANSCRIPTION_WINDOW_SECONDS:
        return (start, end)
    if audio_len_seconds <= TRANSCRIPTION_WINDOW_SECONDS:
        return (0.0, audio_len_seconds)

    centre = (start + end) / 2
    hi = min(
        audio_len_seconds,
        max(centre + TRANSCRIPTION_WINDOW_SECONDS / 2, TRANSCRIPTION_WINDOW_SECONDS),
    )
    return (hi - TRANSCRIPTION_WINDOW_SECONDS, hi)


class SpeechAnalysisService:
    """Audio-content (PANNs) and VAD speech-boundary analysis.

    Injected into UnifiedSegmentAnalyzer as a collaborator that owns the
    audio analyzer, the speech detector, and their per-video caches.
    """

    def __init__(
        self,
        *,
        audio_content_config: AudioContentConfig,
        speech_config: SpeechConfig | None = None,
        audio_content_enabled: bool = False,
        audio_analyzer: object | None = None,
        transcription_config: TranscriptionConfig | None = None,
        transcriber: Transcriber | None = None,
    ):
        """Initialize the speech analysis service.

        Args:
            audio_content_config: AudioContentConfig for lazy audio analyzer init.
            speech_config: SpeechConfig controlling VAD-derived protected ranges.
            audio_content_enabled: Enable audio content analysis (laughter detection).
            audio_analyzer: Pre-built PANNs analyzer, or None to lazy-create.
            transcription_config: TranscriptionConfig controlling speech transcription.
            transcriber: Pre-built transcriber, or None to select one from config.
        """
        self._audio_content_config = audio_content_config
        self.speech_config = speech_config or SpeechConfig()
        self._speech_detector = select_detector(self.speech_config)
        self.audio_content_enabled = audio_content_enabled
        self._audio_analyzer = audio_analyzer  # Injected or lazy-created
        self._audio_analysis_cache: dict[str, AudioAnalysisResult] = {}
        self._vad_audio_cache: dict[str, np.ndarray | None] = {}
        self._vad_regions_cache: dict[str, list[SpeechRegion]] = {}
        self.transcription_config = transcription_config or TranscriptionConfig()
        self._transcriber = (
            transcriber
            if transcriber is not None
            else select_transcriber(self.transcription_config)
        )

    def clear_cache(self, release_audio_analyzer: bool = False) -> None:
        """Clear internal caches to free memory.

        Args:
            release_audio_analyzer: If True, also release the audio analyzer.
                Usually False because the analyzer is shared across clips.
        """
        self._audio_analysis_cache.clear()
        self._vad_audio_cache.clear()
        self._vad_regions_cache.clear()
        if release_audio_analyzer and self._audio_analyzer is not None:
            if hasattr(self._audio_analyzer, "cleanup"):
                self._audio_analyzer.cleanup()
            self._audio_analyzer = None

    def reset_for_video(self) -> None:
        """Release current-video state while retaining reusable configuration and models."""
        self._audio_analysis_cache.clear()
        self._vad_audio_cache.clear()
        self._vad_regions_cache.clear()

    def run_audio_content_analysis(
        self,
        audio_video: Path,
        video_duration: float,
    ) -> AudioAnalysisResult | None:
        """Run audio content analysis and log results.

        Args:
            audio_video: Path to video for audio analysis.
            video_duration: Total video duration.

        Returns:
            AudioAnalysisResult or None if disabled/failed.
        """
        if not self.audio_content_enabled:
            return None

        logger.info("Step 1c: Analyzing audio content (laughter, speech, etc.)")
        result = self.analyze(audio_video, video_duration)
        if not result:
            return None

        logger.info(
            f"  -> Audio score: {result.audio_score:.2f}, "
            f"laughter: {result.has_laughter}, "
            f"speech: {result.has_speech}, "
            f"protected_ranges: {len(result.protected_ranges)}"
        )

        for i, (start, end) in enumerate(result.protected_ranges[:5]):
            logger.info(
                f"     Protected range {i + 1}: {start:.2f}s - {end:.2f}s (duration: {end - start:.2f}s)"
            )

        total_protected = sum(end - start for start, end in result.protected_ranges)
        speech_coverage = total_protected / video_duration if video_duration > 0 else 0
        if speech_coverage > 0.8:
            logger.warning(
                f"  ⚠️ High speech coverage: {speech_coverage:.0%} of video is speech/laughter. "
                "May be difficult to find clean cut points."
            )

        self.log_speech_at_video_end(result, video_duration)
        return result

    def log_speech_at_video_end(self, result: AudioAnalysisResult, video_duration: float) -> None:
        """Log informational note if speech extends to video end."""
        if not result.protected_ranges:
            return

        last_range_end = max(end for _, end in result.protected_ranges)
        if abs(last_range_end - video_duration) < 0.1:
            last_range = max(result.protected_ranges, key=operator.itemgetter(1))
            if last_range[1] - last_range[0] > 1.0:
                logger.info(
                    f"  ℹ️ Speech detected at video end ({last_range[0]:.1f}s-{last_range_end:.1f}s). "
                    "Segment boundaries will be adjusted."
                )

    def get_audio_content_result(
        self,
        audio_video: Path,
        video_duration: float,
        enable_audio_content_analysis: bool,
    ) -> tuple[bool, AudioAnalysisResult | None]:
        """Return whether audio *scoring* applies, plus any analysis result.

        The two are separate capabilities. Scoring needs PANNs, an optional extra
        that is off by default; boundary placement needs only 16 kHz audio and the
        bundled VAD model. Returning a result with protected ranges but no events
        lets boundaries work while audio abstains from scoring.
        """
        audio_content_enabled = enable_audio_content_analysis and self.audio_content_enabled
        if not audio_content_enabled:
            return False, self.speech_boundaries_only(audio_video, video_duration)
        return True, self.run_audio_content_analysis(audio_video, video_duration)

    def speech_boundaries_only(
        self, audio_video: Path, video_duration: float | None
    ) -> AudioAnalysisResult | None:
        """VAD-derived protected ranges with no audio events to score."""
        if not self.speech_config.enabled or self._speech_detector is None:
            return None
        # Deferred like the other audio imports here: this module must stay
        # importable without the optional audio extra installed.
        from immich_memories.audio.audio_models import AudioAnalysisResult

        result = self.apply_vad_ranges(audio_video, AudioAnalysisResult(), video_duration)
        return result if result.protected_ranges else None

    def analyze(
        self, video_path: Path, video_duration: float | None = None
    ) -> AudioAnalysisResult | None:
        """Analyze audio content (laughter, speech, etc.) in a video.

        Args:
            video_path: Path to video file.
            video_duration: Video duration to clamp audio timestamps.

        Returns:
            AudioAnalysisResult or None if analysis fails.
        """
        # Check cache first
        cache_key = str(video_path)
        if cache_key in self._audio_analysis_cache:
            return self._audio_analysis_cache[cache_key]

        try:
            from immich_memories.audio.content_analyzer import AudioContentAnalyzer

            if self._audio_analyzer is None:
                ac_config = self._audio_content_config
                self._audio_analyzer = AudioContentAnalyzer(
                    use_panns=ac_config.use_panns,
                    min_confidence=ac_config.min_confidence,
                    laughter_confidence=ac_config.laughter_confidence,
                )

            result = self._audio_analyzer.analyze(video_path, video_duration)
            result = self.apply_vad_ranges(video_path, result, video_duration)
            self._audio_analysis_cache[cache_key] = result
            return result

        except (ImportError, RuntimeError, OSError, subprocess.SubprocessError) as e:
            logger.warning(f"Audio content analysis failed: {e}")
            return None

    def apply_vad_ranges(
        self,
        video_path: Path,
        result: AudioAnalysisResult,
        video_duration: float | None,
    ) -> AudioAnalysisResult:
        """Replace the PANNs *speech* protected ranges with VAD regions.

        PANNs merges contiguous same-class frames into one span, so a noisy clip
        becomes a single protected range covering everything and boundary
        adjustment has nowhere to move. VAD keeps the pauses between utterances.

        Only the speech ranges are replaced: laughter, singing, cheering and
        applause are also protected and VAD is blind to all of them, so their
        PANNs ranges are unioned back in.
        """
        if not self.speech_config.enabled or self._speech_detector is None:
            return result

        regions = self.detect_regions_cached(video_path)
        if not regions:
            return result

        audio = self.extract_audio_cached(video_path)
        if audio is None:
            return result

        duration = video_duration or (len(audio) / VAD_SAMPLE_RATE)
        previous_count = len(result.protected_ranges)
        non_speech = non_speech_protected_ranges(result.events, duration)
        result.protected_ranges = sorted(
            protected_ranges_from_speech(regions, duration) + non_speech
        )
        logger.info(
            "VAD: %d speech regions + %d non-speech PANNs ranges -> "
            "%d protected ranges (was %d from PANNs)",
            len(regions),
            len(non_speech),
            len(result.protected_ranges),
            previous_count,
        )
        return result

    def extract_audio_cached(self, video_path: Path) -> np.ndarray | None:
        """Extract 16kHz mono audio once per video, cached by path.

        VAD extraction shells out to FFmpeg; candidates within the same video
        must reuse the array rather than each triggering their own extraction.
        """
        cache_key = str(video_path)
        if cache_key not in self._vad_audio_cache:
            self._vad_audio_cache[cache_key] = extract_audio_16k(video_path)
        return self._vad_audio_cache[cache_key]

    def detect_regions_cached(self, video_path: Path) -> list[SpeechRegion]:
        """VAD regions for a video, computed once and reused.

        Both boundary placement and the transcription gate need them; running
        FireRedVAD twice over one cached array buys nothing.
        """
        cache_key = str(video_path)
        if cache_key not in self._vad_regions_cache:
            audio = self.extract_audio_cached(video_path)
            if audio is None or self._speech_detector is None:
                self._vad_regions_cache[cache_key] = []
            else:
                self._vad_regions_cache[cache_key] = self._speech_detector.detect(
                    audio, VAD_SAMPLE_RATE
                )
        return self._vad_regions_cache[cache_key]

    def transcribe_segment(self, video_path: Path, start: float, end: float) -> Transcript | None:
        """Transcribe one segment, or decline.

        Declining is the common case and is not an error: no transcriber
        configured, too little voice activity, or a result the post-ASR gate
        rejected. Nothing downstream scores on the outcome either way.

        The transcriber receives a 30-second window of the cached 16 kHz array
        centred on the segment, so the result is speech heard *around* this moment
        rather than strictly inside it. Whisper is still never asked *when*
        anything was said -- no code reads its timestamps -- but it is given the
        window size it was trained on, which is what makes it accurate.
        """
        if self._transcriber is None or not self.speech_config.enabled:
            return None

        regions = self.detect_regions_cached(video_path)
        voiced = voiced_seconds(regions, start, end)
        if voiced < self.transcription_config.min_voiced_seconds:
            logger.debug(
                "Skipping transcription of %.1fs-%.1fs: %.2fs voiced (floor %.2fs)",
                start,
                end,
                voiced,
                self.transcription_config.min_voiced_seconds,
            )
            return None

        audio = self.extract_audio_cached(video_path)
        if audio is None:
            return None

        window_start, window_end = transcription_window(len(audio) / VAD_SAMPLE_RATE, start, end)
        segment_audio = audio[
            int(window_start * VAD_SAMPLE_RATE) : int(window_end * VAD_SAMPLE_RATE)
        ]
        if segment_audio.size == 0:
            return None

        try:
            return self._transcriber.transcribe(segment_audio)
        except (RuntimeError, ValueError, OSError) as exc:
            logger.warning("Transcription failed for %s: %s", video_path.name, exc)
            return None
