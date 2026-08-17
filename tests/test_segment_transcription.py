"""Step 4a-half: which segments reach whisper, and how often."""

from __future__ import annotations

from pathlib import Path

from immich_memories.analysis.analyzer_models import ScoredSegment
from immich_memories.analysis.segment_transcription import (
    TRANSCRIBE_TOP_N,
    span_overlap_ratio,
    transcribe_top_segments,
)
from immich_memories.speech.transcription import Transcript


class RecordingSpeechAnalysis:
    """# WHY: replaces SpeechAnalysisService, which owns FFmpeg extraction and the
    VAD and whisper models."""

    def __init__(self, result: Transcript | None = None):
        self.result = (
            result
            if result is not None
            else Transcript(text="bonjour", language="fr", confidence=0.9)
        )
        self.asked: list[tuple[float, float]] = []

    def transcribe_segment(self, video_path, start, end):
        self.asked.append((start, end))
        return self.result


def test_span_overlap_is_measured_against_the_shorter_span():
    assert span_overlap_ratio((0.0, 10.0), (0.0, 5.0)) == 1.0
    assert span_overlap_ratio((0.0, 10.0), (5.0, 15.0)) == 0.5
    assert span_overlap_ratio((0.0, 5.0), (10.0, 15.0)) == 0.0


def test_transcription_runs_on_the_top_candidates_only():
    """Same cost profile as LLM scoring: the top five, not every candidate."""
    speech = RecordingSpeechAnalysis()
    # Spaced apart so none is treated as a repeat of another.
    segments = [
        ScoredSegment(start_time=float(i) * 20.0, end_time=float(i) * 20.0 + 3.0) for i in range(8)
    ]

    transcribe_top_segments(speech, segments, Path("/fake.mov"))

    assert len(speech.asked) == TRANSCRIBE_TOP_N
    assert segments[0].transcript == "bonjour"
    assert segments[0].transcript_language == "fr"
    assert segments[0].transcript_confidence == 0.9
    assert segments[TRANSCRIBE_TOP_N].transcript is None


def test_declined_transcription_leaves_the_fields_empty():
    """Declining is the common case: 71% of real segments, measured."""

    class Declining:
        """# WHY: replaces SpeechAnalysisService; models the gate declining."""

        def transcribe_segment(self, video_path, start, end):
            return None

    segments = [ScoredSegment(start_time=0.0, end_time=3.0)]
    before = segments[0].total_score

    transcribe_top_segments(Declining(), segments, Path("/fake.mov"))

    assert segments[0].transcript is None
    assert segments[0].total_score == before, "transcription must never move a score"


def test_overlapping_candidates_are_transcribed_once():
    """The top five candidates are variants of the same moment.

    Measured on a real library: one utterance was transcribed four times from four
    ranges differing by tenths of a second. Whisper runs once, the result is reused.
    """
    speech = RecordingSpeechAnalysis(Transcript(text="tu fais quoi", language="fr", confidence=0.9))
    segments = [
        ScoredSegment(start_time=15.9, end_time=25.2),
        ScoredSegment(start_time=15.9, end_time=24.7),
        ScoredSegment(start_time=16.0, end_time=24.7),
        ScoredSegment(start_time=15.8, end_time=24.9),
        ScoredSegment(start_time=40.0, end_time=45.0),
    ]

    transcribe_top_segments(speech, segments, Path("/fake.mov"))

    assert len(speech.asked) == 2, "one call for the cluster, one for the distant segment"
    assert all(s.transcript == "tu fais quoi" for s in segments[:4])
    assert segments[4].transcript == "tu fais quoi"


def test_no_segments_is_not_an_error():
    speech = RecordingSpeechAnalysis()

    transcribe_top_segments(speech, [], Path("/fake.mov"))

    assert speech.asked == []
