"""Step 4a-half: transcribe the top candidate segments.

Split out of unified_analyzer.py, which had reached the 800-line limit. This is a
cohesive unit: it decides which segments are worth sending to whisper and copies
the result onto them, and it needs nothing from the analyzer but the speech
service.

Deliberately not folded into the LLM scoring pass, which returns early when there
is no content analyzer -- transcription has to work with content analysis off.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from immich_memories.speech.transcription import Transcript

TRANSCRIBE_TOP_N = 5

# Two candidates overlapping by this much of the shorter span hold the same speech.
# The top five are variants of one moment: on a real library, one utterance was
# transcribed four times from four ranges differing by tenths of a second.
TRANSCRIBE_REUSE_OVERLAP = 0.8


class SegmentTranscriber(Protocol):
    def transcribe_segment(
        self, video_path: Path, start: float, end: float
    ) -> Transcript | None: ...


def span_overlap_ratio(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Overlap of two time spans as a fraction of the shorter one."""
    overlap = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    shorter = min(a[1] - a[0], b[1] - b[0])
    return overlap / shorter if shorter > 0 else 0.0


def transcribe_top_segments(
    speech_analysis: SegmentTranscriber,
    scored_segments: list,
    audio_video: Path,
) -> None:
    """Transcribe the top candidates in place, running whisper once per moment."""
    top_n = min(TRANSCRIBE_TOP_N, len(scored_segments))
    if not top_n:
        return

    done: list[tuple[tuple[float, float], Transcript]] = []
    for segment in scored_segments[:top_n]:
        span = (segment.start_time, segment.end_time)
        transcript = next(
            (
                known
                for other, known in done
                if span_overlap_ratio(span, other) >= TRANSCRIBE_REUSE_OVERLAP
            ),
            None,
        )
        if transcript is None:
            transcript = speech_analysis.transcribe_segment(
                audio_video, segment.start_time, segment.end_time
            )
            if transcript is None:
                continue
            done.append((span, transcript))

        segment.transcript = transcript.text
        segment.transcript_language = transcript.language
        segment.transcript_confidence = transcript.confidence
