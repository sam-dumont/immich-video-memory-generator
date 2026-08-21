"""The content-analysis prompt, with and without audio context."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from immich_memories.analysis._content_providers import (
    OllamaContentAnalyzer,
    OpenAICompatibleContentAnalyzer,
)
from immich_memories.analysis.analyzer_models import ScoredSegment
from immich_memories.analysis.llm_response_parser import (
    CONTENT_ANALYSIS_PROMPT,
    PROMPT_TRANSCRIPT_MAX_CHARS,
    build_content_analysis_prompt,
)
from immich_memories.analysis.unified_analyzer import UnifiedSegmentAnalyzer
from immich_memories.config_models import AnalysisConfig, AudioContentConfig

# Hard-coded rather than compared against the constant: comparing against the
# constant would pass even if both changed together, which is the regression this
# guards. Updating this literal is therefore a deliberate act — it means the
# prompt genuinely changed and every analysis from here on differs (#483).
TODAYS_PROMPT = """Describe what you see in this image.

Return JSON with these fields:
- description: What is happening in this scene?
- category: What is this mainly of? Exactly one of: people, animal, landscape, object, screen.
  Use "screen" if the subject is a phone, watch, computer or TV display, a screenshot,
  or a document or form -- even when a person is holding or wearing the device.
  Use "people" if a person is the subject. Use "animal" only for a live animal, not a
  toy, figurine, drawing or photo of one. Use "landscape" only for a wide outdoor view,
  never for a close-up of a thing. Use "object" for anything else.
- subjects: What is in frame? (short lowercase nouns, e.g. ["child", "dog", "beach"])
- setting: Where is it? Exactly one of: indoor_home, indoor_public, outdoor_nature,
  outdoor_urban, vehicle, water. Use "water" for in or on water (pool, sea, boat),
  "vehicle" for inside a car, train or plane, "outdoor_urban" for streets and towns,
  "outdoor_nature" for anything outdoors that is not built up.
- emotion: What is the mood? (one word: happy, calm, excited, playful, joyful, peaceful)
- interestingness: How memorable is this moment? (0.0 to 1.0)
- quality: How good is the image quality? (0.0 to 1.0)

Example format: {"description": "...", "category": "people", "subjects": ["child", "sand"], "setting": "outdoor_nature", "emotion": "...", "interestingness": 0.7, "quality": 0.8}

JSON:"""


def test_no_transcript_returns_todays_prompt_byte_for_byte():
    """Anyone not using transcription must see no change at all."""
    assert build_content_analysis_prompt() == TODAYS_PROMPT
    assert CONTENT_ANALYSIS_PROMPT == TODAYS_PROMPT


def test_empty_transcript_is_treated_as_no_transcript():
    assert build_content_analysis_prompt("") == TODAYS_PROMPT
    assert build_content_analysis_prompt("   ") == TODAYS_PROMPT


def test_transcript_is_included_with_a_reliability_warning():
    """The vision model is the only hallucination filter available: it sees both."""
    prompt = build_content_analysis_prompt("Il est mignon. Tu veux lui faire une douce ?")

    assert "Il est mignon. Tu veux lui faire une douce ?" in prompt
    assert "may be inaccurate" in prompt
    assert "ignore it if it does not match the image" in prompt
    assert prompt.endswith("JSON:"), "the JSON instruction must stay last"


def test_long_transcript_is_truncated_on_a_word_boundary():
    """30s of speech is far more text than a slice produced, and small vision models
    have tight context -- the Ollama provider already drops to two images to stay
    under Moondream's 2048-token limit."""
    words = " ".join(["mot"] * 500)

    prompt = build_content_analysis_prompt(words)

    quoted = prompt.split('"')[1]
    assert len(quoted) <= PROMPT_TRANSCRIPT_MAX_CHARS + 1  # +1 for the ellipsis
    assert quoted.endswith("…")
    assert "mot mot" in quoted
    assert not quoted.rstrip("…").endswith("mo"), "must not cut mid-word"


def _fake_frames(tmp_path):
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xd9")
    return [frame]


def test_ollama_puts_the_transcript_in_the_request(tmp_path):
    analyzer = OllamaContentAnalyzer(model="moondream", base_url="http://x")

    # WHY: replaces frame extraction, which shells out to FFmpeg.
    # WHY: replaces the HTTP call to the Ollama server.
    with (
        patch.object(analyzer, "extract_frames", return_value=_fake_frames(tmp_path)),
        patch.object(analyzer, "_ollama_request_with_retry", return_value=MagicMock()) as req,
    ):
        analyzer.analyze_segment(tmp_path / "v.mov", 0.0, 3.0, transcript="Tu fais quoi ?")

    assert "Tu fais quoi ?" in req.call_args[0][0]["prompt"]


def test_openai_compatible_puts_the_transcript_in_the_request(tmp_path):
    """This provider posts directly rather than through a retry helper, so the
    HTTP client is the boundary to replace."""
    analyzer = OpenAICompatibleContentAnalyzer(model="qwen", base_url="http://x", api_key="")

    response = MagicMock()
    response.json.return_value = {
        "choices": [{"message": {"content": '{"description": "x"}'}}],
        "usage": {},
    }

    # WHY: replaces frame extraction, which shells out to FFmpeg.
    # WHY: replaces the httpx POST, the network boundary. `client` itself is a
    # read-only property, so the method is what gets replaced.
    with (
        patch.object(analyzer, "extract_frames", return_value=_fake_frames(tmp_path)),
        patch.object(analyzer.client, "post", return_value=response) as post,
    ):
        analyzer.analyze_segment(tmp_path / "v.mov", 0.0, 3.0, transcript="Tu fais quoi ?")

    payload = post.call_args.kwargs["json"]
    prompt = payload["messages"][0]["content"][0]["text"]
    assert "Tu fais quoi ?" in prompt


def test_score_content_passes_the_segments_transcript():
    """The analyzer must hand the stored transcript to the content analyzer.

    Without this the whole feature is inert -- the transcript sits on the segment
    and never reaches the prompt.
    """
    # WHY: replaces the vision LLM, an external network service.
    content = MagicMock()
    content.analyze_segment.return_value = MagicMock(
        confidence=0.9,
        description="d",
        emotion="happy",
        setting="",
        subjects=[],
        interestingness=0.7,
        quality=0.8,
        content_score=0.73,
    )
    analyzer = UnifiedSegmentAnalyzer(
        scorer=MagicMock(),
        content_analyzer=content,
        audio_content_config=AudioContentConfig(),
        analysis_config=AnalysisConfig(),
    )
    segment = ScoredSegment(start_time=0.0, end_time=3.0)
    segment.transcript = "Il est mignon"

    analyzer._score_content(Path("/fake.mov"), 0.0, 3.0, segment=segment)

    assert content.analyze_segment.call_args.kwargs["transcript"] == "Il est mignon"


def test_no_transcript_sends_the_unchanged_prompt(tmp_path):
    """Transcription off must produce a byte-identical request."""
    analyzer = OllamaContentAnalyzer(model="moondream", base_url="http://x")

    # WHY: replaces frame extraction (FFmpeg) and the HTTP call.
    with (
        patch.object(analyzer, "extract_frames", return_value=_fake_frames(tmp_path)),
        patch.object(analyzer, "_ollama_request_with_retry", return_value=MagicMock()) as req,
    ):
        analyzer.analyze_segment(tmp_path / "v.mov", 0.0, 3.0)

    assert req.call_args[0][0]["prompt"] == TODAYS_PROMPT
