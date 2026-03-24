"""Tests for LLM response parser and ContentAnalysis dataclass."""

from __future__ import annotations

import json

import pytest

from immich_memories.analysis.llm_response_parser import (
    ContentAnalysis,
    ContentAnalyzer,
)


class TestContentAnalysis:
    """ContentAnalysis dataclass and content_score property."""

    def test_default_values(self):
        ca = ContentAnalysis()
        assert ca.description == ""
        assert ca.activities == []
        assert ca.subjects == []
        assert ca.setting == ""
        assert ca.emotion == ""
        assert ca.interestingness == 0.5
        assert ca.quality == 0.5
        assert ca.confidence == 0.5

    def test_content_score_formula(self):
        ca = ContentAnalysis(interestingness=1.0, quality=1.0, confidence=1.0)
        # (1.0 * 0.7 + 1.0 * 0.3) * 1.0 = 1.0
        assert ca.content_score == pytest.approx(1.0)

    def test_content_score_weighted(self):
        ca = ContentAnalysis(interestingness=0.8, quality=0.6, confidence=0.5)
        # (0.8 * 0.7 + 0.6 * 0.3) * 0.5 = (0.56 + 0.18) * 0.5 = 0.37
        assert ca.content_score == pytest.approx(0.37)

    def test_content_score_zero_confidence(self):
        ca = ContentAnalysis(interestingness=1.0, quality=1.0, confidence=0.0)
        assert ca.content_score == pytest.approx(0.0)

    def test_content_score_defaults(self):
        ca = ContentAnalysis()
        # (0.5 * 0.7 + 0.5 * 0.3) * 0.5 = 0.5 * 0.5 = 0.25
        assert ca.content_score == pytest.approx(0.25)

    def test_custom_fields(self):
        ca = ContentAnalysis(
            description="A park scene",
            activities=["walking", "talking"],
            subjects=["person"],
            setting="outdoor park",
            emotion="happy",
            interestingness=0.9,
            quality=0.8,
            confidence=0.95,
        )
        assert ca.description == "A park scene"
        assert ca.activities == ["walking", "talking"]
        assert ca.subjects == ["person"]
        assert ca.setting == "outdoor park"
        assert ca.emotion == "happy"


class TestNumericEmotionToStr:
    """Threshold-based emotion conversion."""

    def test_joyful_at_1_0(self):
        assert ContentAnalyzer._numeric_emotion_to_str(1.0) == "joyful"

    def test_joyful_at_0_8(self):
        assert ContentAnalyzer._numeric_emotion_to_str(0.8) == "joyful"

    def test_happy_at_0_79(self):
        assert ContentAnalyzer._numeric_emotion_to_str(0.79) == "happy"

    def test_happy_at_0_6(self):
        assert ContentAnalyzer._numeric_emotion_to_str(0.6) == "happy"

    def test_calm_at_0_59(self):
        assert ContentAnalyzer._numeric_emotion_to_str(0.59) == "calm"

    def test_calm_at_0_4(self):
        assert ContentAnalyzer._numeric_emotion_to_str(0.4) == "calm"

    def test_neutral_at_0_39(self):
        assert ContentAnalyzer._numeric_emotion_to_str(0.39) == "neutral"

    def test_neutral_at_0_2(self):
        assert ContentAnalyzer._numeric_emotion_to_str(0.2) == "neutral"

    def test_subdued_at_0_19(self):
        assert ContentAnalyzer._numeric_emotion_to_str(0.19) == "subdued"

    def test_subdued_at_0_0(self):
        assert ContentAnalyzer._numeric_emotion_to_str(0.0) == "subdued"


class TestExtractJsonText:
    """Markdown fence stripping."""

    def test_json_fence(self):
        text = '```json\n{"key": "value"}\n```'
        result = ContentAnalyzer._extract_json_text(text)
        assert result == '{"key": "value"}'

    def test_plain_fence(self):
        text = '```\n{"key": "value"}\n```'
        result = ContentAnalyzer._extract_json_text(text)
        assert result == '{"key": "value"}'

    def test_no_fence(self):
        text = '{"key": "value"}'
        result = ContentAnalyzer._extract_json_text(text)
        assert result == '{"key": "value"}'

    def test_whitespace_stripping(self):
        text = '  \n  {"key": "value"}  \n  '
        result = ContentAnalyzer._extract_json_text(text)
        assert result == '{"key": "value"}'

    def test_json_fence_with_extra_text(self):
        text = 'Here is the result:\n```json\n{"a": 1}\n```\nDone.'
        result = ContentAnalyzer._extract_json_text(text)
        assert result == '{"a": 1}'


class TestStripThinkingBlocks:
    """<think> block removal and preamble stripping."""

    def test_removes_think_block(self):
        text = '<think>Let me analyze this...</think>{"key": "value"}'
        result = ContentAnalyzer._strip_thinking_blocks(text)
        assert result == '{"key": "value"}'

    def test_removes_multiline_think_block(self):
        text = '<think>\nStep 1: Look at image\nStep 2: Describe\n</think>\n{"key": "v"}'
        result = ContentAnalyzer._strip_thinking_blocks(text)
        assert result == '{"key": "v"}'

    def test_removes_preamble_before_json(self):
        text = 'The user wants a description of the image.\n{"description": "test"}'
        result = ContentAnalyzer._strip_thinking_blocks(text)
        assert result == '{"description": "test"}'

    def test_no_think_block_passthrough(self):
        text = '{"key": "value"}'
        result = ContentAnalyzer._strip_thinking_blocks(text)
        assert result == '{"key": "value"}'

    def test_empty_text(self):
        result = ContentAnalyzer._strip_thinking_blocks("")
        assert result == ""

    def test_think_block_plus_preamble(self):
        text = '<think>reasoning</think>Here is the answer:\n{"a": 1}'
        result = ContentAnalyzer._strip_thinking_blocks(text)
        assert result == '{"a": 1}'


class TestParseJsonObject:
    """JSON parsing with various malformations."""

    def test_clean_json_object(self):
        text = '{"description": "a dog", "emotion": "happy"}'
        result = ContentAnalyzer._parse_json_object(text)
        assert result == {"description": "a dog", "emotion": "happy"}

    def test_json_array_returns_first_element(self):
        text = '[{"description": "first"}, {"description": "second"}]'
        result = ContentAnalyzer._parse_json_object(text)
        assert result == {"description": "first"}

    def test_truncated_json_fixed(self):
        text = '{"description": "test", "emotion": "calm"'
        result = ContentAnalyzer._parse_json_object(text)
        assert result == {"description": "test", "emotion": "calm"}

    def test_truncated_json_with_trailing_comma(self):
        text = '{"description": "test", "emotion": "calm",'
        result = ContentAnalyzer._parse_json_object(text)
        assert result == {"description": "test", "emotion": "calm"}

    def test_json_embedded_in_prose(self):
        text = 'Here is the analysis: {"description": "a cat"} end of response'
        result = ContentAnalyzer._parse_json_object(text)
        assert result == {"description": "a cat"}

    def test_no_json_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON found"):
            ContentAnalyzer._parse_json_object("no json here at all")

    def test_empty_array_raises_value_error(self):
        with pytest.raises(ValueError, match="Empty array response"):
            ContentAnalyzer._parse_json_object("[]")

    def test_malformed_array_extracts_object(self):
        # Array that fails json.loads but contains an extractable object
        text = '[{"key": "value"} extra junk'
        result = ContentAnalyzer._parse_json_object(text)
        assert result == {"key": "value"}


class TestBuildContentAnalysis:
    """Validation, truncation, and clamping."""

    def test_basic_build(self):
        data = {
            "description": "A sunset",
            "emotion": "calm",
            "interestingness": 0.7,
            "quality": 0.8,
        }
        result = ContentAnalyzer._build_content_analysis(data, "calm")
        assert result.description == "A sunset"
        assert result.emotion == "calm"
        assert result.interestingness == pytest.approx(0.7)
        assert result.quality == pytest.approx(0.8)
        assert result.confidence == pytest.approx(0.8)

    def test_truncates_long_description(self):
        data = {"description": "x" * 600}
        result = ContentAnalyzer._build_content_analysis(data, "")
        assert len(result.description) == 500

    def test_truncates_long_emotion(self):
        long_emotion = "e" * 600
        data = {}
        result = ContentAnalyzer._build_content_analysis(data, long_emotion)
        assert len(result.emotion) == 500

    def test_limits_activities_list(self):
        data = {"activities": [f"act_{i}" for i in range(15)]}
        result = ContentAnalyzer._build_content_analysis(data, "")
        assert len(result.activities) == 10

    def test_limits_subjects_list(self):
        data = {"subjects": [f"sub_{i}" for i in range(15)]}
        result = ContentAnalyzer._build_content_analysis(data, "")
        assert len(result.subjects) == 10

    def test_truncates_items_in_lists(self):
        data = {"activities": ["a" * 600]}
        result = ContentAnalyzer._build_content_analysis(data, "")
        assert len(result.activities[0]) == 500

    def test_clamps_interestingness_above_1(self):
        data = {"interestingness": 1.5}
        result = ContentAnalyzer._build_content_analysis(data, "")
        assert result.interestingness == pytest.approx(1.0)

    def test_clamps_interestingness_below_0(self):
        data = {"interestingness": -0.3}
        result = ContentAnalyzer._build_content_analysis(data, "")
        assert result.interestingness == pytest.approx(0.0)

    def test_clamps_quality_above_1(self):
        data = {"quality": 2.0}
        result = ContentAnalyzer._build_content_analysis(data, "")
        assert result.quality == pytest.approx(1.0)

    def test_clamps_quality_below_0(self):
        data = {"quality": -0.5}
        result = ContentAnalyzer._build_content_analysis(data, "")
        assert result.quality == pytest.approx(0.0)

    def test_missing_fields_use_defaults(self):
        data: dict = {}
        result = ContentAnalyzer._build_content_analysis(data, "")
        assert result.description == ""
        assert result.activities == []
        assert result.subjects == []
        assert result.setting == ""
        assert result.interestingness == pytest.approx(0.5)
        assert result.quality == pytest.approx(0.5)

    def test_setting_field(self):
        data = {"setting": "indoor kitchen"}
        result = ContentAnalyzer._build_content_analysis(data, "happy")
        assert result.setting == "indoor kitchen"

    def test_truncates_long_setting(self):
        data = {"setting": "s" * 600}
        result = ContentAnalyzer._build_content_analysis(data, "")
        assert len(result.setting) == 500


class TestParseContentResponse:
    """End-to-end parsing pipeline via instance method."""

    @pytest.fixture()
    def analyzer(self):
        return ContentAnalyzer()

    def test_valid_json(self, analyzer: ContentAnalyzer):
        response = json.dumps(
            {
                "description": "Kids playing in the park",
                "emotion": "joyful",
                "interestingness": 0.9,
                "quality": 0.85,
            }
        )
        result = analyzer._parse_content_response(response)
        assert result.description == "Kids playing in the park"
        assert result.emotion == "joyful"
        assert result.interestingness == pytest.approx(0.9)
        assert result.quality == pytest.approx(0.85)
        assert result.confidence == pytest.approx(0.8)

    def test_json_with_markdown_fences(self, analyzer: ContentAnalyzer):
        response = '```json\n{"description": "Beach sunset", "emotion": "calm", "interestingness": 0.7, "quality": 0.6}\n```'
        result = analyzer._parse_content_response(response)
        assert result.description == "Beach sunset"
        assert result.emotion == "calm"

    def test_json_with_thinking_block(self, analyzer: ContentAnalyzer):
        response = '<think>I need to analyze this carefully</think>{"description": "Mountain view", "emotion": "peaceful", "interestingness": 0.8, "quality": 0.9}'
        result = analyzer._parse_content_response(response)
        assert result.description == "Mountain view"
        assert result.emotion == "peaceful"

    def test_numeric_emotion_converted(self, analyzer: ContentAnalyzer):
        response = json.dumps(
            {
                "description": "A happy scene",
                "emotion": 0.85,
                "interestingness": 0.7,
                "quality": 0.6,
            }
        )
        result = analyzer._parse_content_response(response)
        assert result.emotion == "joyful"

    def test_numeric_emotion_int(self, analyzer: ContentAnalyzer):
        response = json.dumps(
            {
                "description": "A scene",
                "emotion": 1,
                "interestingness": 0.5,
                "quality": 0.5,
            }
        )
        result = analyzer._parse_content_response(response)
        assert result.emotion == "joyful"

    def test_malformed_json_falls_back_to_regex(self, analyzer: ContentAnalyzer):
        # Completely broken — no valid JSON can be extracted
        response = '"description": "A dog running", "emotion": "happy", "interestingness": 0.6'
        result = analyzer._parse_content_response(response)
        assert result.description == "A dog running"
        assert result.emotion == "happy"
        assert result.interestingness == pytest.approx(0.6)
        assert result.confidence == pytest.approx(0.6)

    def test_empty_emotion_string(self, analyzer: ContentAnalyzer):
        response = json.dumps(
            {
                "description": "Some scene",
                "emotion": "",
                "interestingness": 0.5,
                "quality": 0.5,
            }
        )
        result = analyzer._parse_content_response(response)
        assert result.emotion == ""

    def test_none_emotion_becomes_empty(self, analyzer: ContentAnalyzer):
        response = json.dumps(
            {
                "description": "Scene",
                "emotion": None,
                "interestingness": 0.5,
                "quality": 0.5,
            }
        )
        result = analyzer._parse_content_response(response)
        assert result.emotion == ""

    def test_thinking_plus_fences(self, analyzer: ContentAnalyzer):
        response = (
            "<think>Analyzing...</think>\n"
            "Here is my answer:\n"
            "```json\n"
            '{"description": "City skyline", "emotion": "calm", "interestingness": 0.6, "quality": 0.7}\n'
            "```"
        )
        result = analyzer._parse_content_response(response)
        assert result.description == "City skyline"
        assert result.emotion == "calm"

    def test_truncated_json_recovered(self, analyzer: ContentAnalyzer):
        response = '{"description": "A test scene", "emotion": "happy", "interestingness": 0.7, "quality": 0.8'
        result = analyzer._parse_content_response(response)
        assert result.description == "A test scene"
        assert result.emotion == "happy"


class TestExtractPartialData:
    """Regex fallback for malformed responses."""

    @pytest.fixture()
    def analyzer(self):
        return ContentAnalyzer()

    def test_extracts_description(self, analyzer: ContentAnalyzer):
        text = 'blah "description": "A dog playing fetch" blah'
        result = analyzer._extract_partial_data(text)
        assert result.description == "A dog playing fetch"
        assert result.confidence == pytest.approx(0.6)

    def test_extracts_string_emotion(self, analyzer: ContentAnalyzer):
        text = '"emotion": "happy"'
        result = analyzer._extract_partial_data(text)
        assert result.emotion == "happy"
        assert result.confidence == pytest.approx(0.6)

    def test_extracts_numeric_emotion(self, analyzer: ContentAnalyzer):
        text = '"emotion": 0.85'
        result = analyzer._extract_partial_data(text)
        assert result.emotion == "joyful"

    def test_extracts_interestingness(self, analyzer: ContentAnalyzer):
        text = '"description": "x", "interestingness": 0.72'
        result = analyzer._extract_partial_data(text)
        assert result.interestingness == pytest.approx(0.72)

    def test_extracts_quality(self, analyzer: ContentAnalyzer):
        text = '"description": "x", "quality": 0.65'
        result = analyzer._extract_partial_data(text)
        assert result.quality == pytest.approx(0.65)

    def test_extracts_setting(self, analyzer: ContentAnalyzer):
        text = '"description": "x", "setting": "indoor office"'
        result = analyzer._extract_partial_data(text)
        assert result.setting == "indoor office"

    def test_nothing_found_returns_low_confidence(self, analyzer: ContentAnalyzer):
        text = "totally unparseable garbage with no json patterns"
        result = analyzer._extract_partial_data(text)
        assert result.confidence == pytest.approx(0.4)
        assert result.description == ""
        assert result.emotion == ""

    def test_description_with_escaped_quotes(self, analyzer: ContentAnalyzer):
        # The fallback regex truncates at the first escaped quote —
        # this is acceptable for a best-effort fallback path
        text = r'"description": "A \"special\" moment"'
        result = analyzer._extract_partial_data(text)
        assert result.description.startswith("A")
        assert result.confidence == pytest.approx(0.6)

    def test_description_with_escaped_newlines(self, analyzer: ContentAnalyzer):
        text = r'"description": "Line one\nLine two"'
        result = analyzer._extract_partial_data(text)
        assert result.description == "Line one Line two"

    def test_only_emotion_gives_0_6_confidence(self, analyzer: ContentAnalyzer):
        text = '"emotion": "excited"'
        result = analyzer._extract_partial_data(text)
        assert result.confidence == pytest.approx(0.6)


class TestExtractEmotionFromText:
    """Regex-based emotion extraction."""

    def test_string_emotion(self):
        text = '{"emotion": "peaceful"}'
        assert ContentAnalyzer._extract_emotion_from_text(text) == "peaceful"

    def test_numeric_emotion(self):
        text = '{"emotion": 0.75}'
        assert ContentAnalyzer._extract_emotion_from_text(text) == "happy"

    def test_no_emotion(self):
        text = '{"description": "something"}'
        assert ContentAnalyzer._extract_emotion_from_text(text) == ""

    def test_numeric_emotion_high(self):
        text = '"emotion": 0.95'
        assert ContentAnalyzer._extract_emotion_from_text(text) == "joyful"

    def test_numeric_emotion_low(self):
        text = '"emotion": 0.1'
        assert ContentAnalyzer._extract_emotion_from_text(text) == "subdued"


class TestExtractFloatField:
    """Numeric field extraction from raw text."""

    def test_extracts_interestingness(self):
        text = '"interestingness": 0.75'
        result = ContentAnalyzer._extract_float_field(text, "interestingness")
        assert result == pytest.approx(0.75)

    def test_extracts_quality(self):
        text = '"quality": 0.9'
        result = ContentAnalyzer._extract_float_field(text, "quality")
        assert result == pytest.approx(0.9)

    def test_missing_field_returns_none(self):
        text = '"other": 0.5'
        result = ContentAnalyzer._extract_float_field(text, "interestingness")
        assert result is None

    def test_integer_value(self):
        text = '"quality": 1'
        result = ContentAnalyzer._extract_float_field(text, "quality")
        assert result == pytest.approx(1.0)

    def test_with_spaces_around_colon(self):
        text = '"interestingness"  :  0.42'
        result = ContentAnalyzer._extract_float_field(text, "interestingness")
        assert result == pytest.approx(0.42)


class TestSessionStats:
    """Session-level token tracking."""

    def test_reset_session_stats(self):
        ContentAnalyzer.total_prompt_tokens = 100
        ContentAnalyzer.total_completion_tokens = 50
        ContentAnalyzer.total_images_analyzed = 3
        ContentAnalyzer.reset_session_stats()
        assert ContentAnalyzer.total_prompt_tokens == 0
        assert ContentAnalyzer.total_completion_tokens == 0
        assert ContentAnalyzer.total_images_analyzed == 0

    def test_log_analysis_result_accumulates(self):
        ContentAnalyzer.reset_session_stats()
        analyzer = ContentAnalyzer()
        ca = ContentAnalysis(description="test")
        analyzer._log_analysis_result(ca, prompt_tokens=100, completion_tokens=50)
        assert ContentAnalyzer.total_prompt_tokens == 100
        assert ContentAnalyzer.total_completion_tokens == 50
        assert ContentAnalyzer.total_images_analyzed == 1

        analyzer._log_analysis_result(ca, prompt_tokens=200, completion_tokens=80, num_images=2)
        assert ContentAnalyzer.total_prompt_tokens == 300
        assert ContentAnalyzer.total_completion_tokens == 130
        assert ContentAnalyzer.total_images_analyzed == 3
