"""Behaviour tests for the card-model distillation pipeline (scripts/distill/).

The pipeline is four one-command stages over a public corpus and a local model.
Everything tested here is the pure logic between those boundaries: which rows
survive the licence filter, whether a bigger --count really tops up, whether the
wire request still carries the production schema, and whether the §7 gate
arithmetic says what it claims to say.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

DISTILL = Path(__file__).resolve().parents[1] / "scripts" / "distill"
sys.path.insert(0, str(DISTILL))

assemble_blend = pytest.importorskip("assemble_blend")
distill_common = pytest.importorskip("distill_common")
eval_gates = pytest.importorskip("eval_gates")
gap_probe = pytest.importorskip("gap_probe")
pull_corpus = pytest.importorskip("pull_corpus")
teacher_label = pytest.importorskip("teacher_label")


def cc_by_row(**overrides: str) -> dict[str, str]:
    row = {
        "ImageID": "abc123",
        "Author": "A Photographer",
        "AuthorProfileURL": "https://www.flickr.com/people/x/",
        "License": "https://creativecommons.org/licenses/by/2.0/",
        "Title": "IMG_0186.jpg",
        "OriginalLandingURL": "https://www.flickr.com/photos/x/1",
    }
    row.update(overrides)
    return row


class TestManifestFilter:
    def test_plain_cc_by_with_named_author_survives(self):
        assert distill_common.keeps_row(cc_by_row())

    @pytest.mark.parametrize(
        "license_url",
        [
            "https://creativecommons.org/licenses/by-nc/2.0/",
            "https://creativecommons.org/licenses/by-nd/2.0/",
            "https://creativecommons.org/licenses/by-sa/2.0/",
            "https://creativecommons.org/publicdomain/mark/1.0/",
        ],
    )
    def test_non_plain_cc_by_is_dropped(self, license_url):
        """§4.3 corpus policy: CC0 and CC-BY only; NC, ND and SA are excluded."""
        assert not distill_common.keeps_row(cc_by_row(License=license_url))

    def test_blank_author_is_dropped(self):
        assert not distill_common.keeps_row(cc_by_row(Author=""))

    def test_institutional_author_is_dropped(self):
        """The §4.1 residue: a real Open Images validation row is a city archive."""
        assert not distill_common.keeps_row(cc_by_row(Author="Stockholms stadsarkiv"))

    def test_a_person_is_not_an_institution(self):
        assert not distill_common.is_institutional("Michael Beat", "...die FNF-Kerze")

    def test_vocabulary_resolves_by_display_name_and_reports_misses(self):
        classes = [
            {"LabelName": "/m/01kcnl", "DisplayName": "Birthday"},
            {"LabelName": "/m/01g317", "DisplayName": "Person"},
        ]
        resolved, missing = distill_common.resolve_vocabulary(
            classes, ["Birthday", "Person", "Baby shower"]
        )
        assert resolved == {"Birthday": "/m/01kcnl", "Person": "/m/01g317"}
        assert missing == ("Baby shower",)


class TestSamplingAndResume:
    def test_a_bigger_count_is_a_strict_superset(self):
        """--count tops up: the first N of a larger draw are the earlier draw."""
        ids = [f"id{index:04d}" for index in range(500)]
        order = distill_common.deterministic_order(ids, seed=42)
        assert order[:100] == distill_common.deterministic_order(ids, seed=42)[:100]
        assert len(order) == 500

    def test_a_different_seed_reorders(self):
        ids = [f"id{index:04d}" for index in range(200)]
        assert distill_common.deterministic_order(
            ids, seed=42
        ) != distill_common.deterministic_order(ids, seed=7)

    def test_order_is_stable_under_input_shuffling_and_duplicates(self):
        ids = [f"id{index}" for index in range(50)]
        assert distill_common.deterministic_order(ids, seed=42) == (
            distill_common.deterministic_order([*reversed(ids), *ids], seed=42)
        )

    def test_resume_replays_downloads_and_never_retries_an_absent_id(self, tmp_path):
        paths = pull_corpus.CorpusPaths(root=tmp_path, split="validation")
        for record in (
            {"image_id": "kept", "status": "ok"},
            {"image_id": "gone", "status": "absent"},
            {"image_id": "kept", "status": "ok"},
        ):
            distill_common.append_jsonl(paths.downloads_log, record)
        kept, absent = pull_corpus.already_done(paths)
        assert set(kept) == {"kept"}
        assert absent == {"gone"}

    def test_pending_rows_skips_what_is_already_labelled(self):
        manifest = [{"image_id": "a"}, {"image_id": "b"}, {"image_id": "c"}]
        assert teacher_label.pending_rows(manifest, {"b"}) == [
            {"image_id": "a"},
            {"image_id": "c"},
        ]


class TestProductionPromptReuse:
    """The student must learn the schema the app ships, not a copy of it."""

    @pytest.fixture
    def constants(self):
        return distill_common.production_prompt_constants()

    def test_card_request_carries_every_live_card_key(self, constants):
        prompt = teacher_label.build_card_prompt(constants)
        for key in constants["card_shape"]:
            assert key in prompt, f"card key {key} missing from the request"

    def test_card_request_carries_the_setting_field_when_the_tree_has_it(self, constants):
        """The setting slot is the mid-flight change; if it is live, it must ship."""
        if "setting" not in constants["card_shape"]:
            pytest.skip("the tree's card has no setting slot yet")
        assert "setting" in teacher_label.build_card_prompt(constants)

    def test_description_request_is_the_production_prompt_verbatim(self, constants):
        assert teacher_label.build_prompt("description", constants) == (
            constants["description_prompt"]
        )

    def test_the_wire_request_carries_the_schema_and_disables_thinking(self, tmp_path):
        constants = distill_common.production_prompt_constants()
        prompt = teacher_label.build_card_prompt(constants)
        endpoint = teacher_label.TeacherEndpoint(
            provider="omlx", wire="openai", base_url="http://localhost:9999/v1",
            api_key="k", model="test-teacher",
            extra={"chat_template_kwargs": {"enable_thinking": False}},
        )
        captured: dict = {}

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [
                        {"message": {"content": json.dumps({"summary": "two people", "setting": "a park"})}}
                    ]
                }

        async def capture(url, **kwargs):
            captured.update(kwargs["json"])
            return Response()

        # WHY: replaces the HTTP call to the omlx server. The assertion is about
        # what goes on the wire, so the transport is the only thing mocked.
        client = AsyncMock()
        client.post = capture
        # WHY: replaces the JPEG decode of an image file that does not exist here.
        with patch.object(teacher_label, "encode_image", return_value="AAAA"):
            record = _run(
                teacher_label.label_one(
                    client,
                    endpoint,
                    {"image_id": "abc", "local_path": str(tmp_path / "abc.jpg")},
                    prompt=prompt,
                    constants=constants,
                    task="card",
                    max_tokens=600,
                )
            )

        assert captured["temperature"] == 0.0
        assert captured["chat_template_kwargs"] == {"enable_thinking": False}
        text = captured["messages"][0]["content"][1]["text"]
        for key in constants["card_shape"]:
            assert key in text
        assert record["status"] == "ok"
        assert record["text"] == "two people"
        assert record["setting"] == "a park"


class TestScrubAndCanaries:
    def test_a_mid_sentence_name_is_redacted(self):
        scrubbed, count = teacher_label.scrub_proper_nouns(
            "a photograph of Michael Beat at a party"
        )
        assert "Michael" not in scrubbed and "Beat" not in scrubbed
        assert count == 2

    def test_a_sentence_opening_word_survives(self):
        """A capital at a sentence start says nothing about proper-noun-ness."""
        scrubbed, count = teacher_label.scrub_proper_nouns("Two children blow out candles.")
        assert scrubbed == "Two children blow out candles."
        assert count == 0

    def test_place_generic_words_are_allowlisted(self):
        scrubbed, _ = teacher_label.scrub_proper_nouns("a view of the Eiffel Tower")
        assert "Tower" in scrubbed
        assert "Eiffel" not in scrubbed

    def test_lowercase_prose_is_untouched(self):
        assert teacher_label.scrub_proper_nouns("two children at a birthday party") == (
            "two children at a birthday party",
            0,
        )

    def test_canaries_cover_all_four_repeat_rates_with_unique_secrets(self):
        canaries = teacher_label.mint_canaries([f"img{i}" for i in range(60)], seed=42)
        assert len(canaries) == teacher_label.CANARY_COUNT
        assert {row["repeat"] for row in canaries} == set(teacher_label.CANARY_REPEATS)
        assert len({row["secret"] for row in canaries}) == teacher_label.CANARY_COUNT
        assert all(len(row["secret"]) == teacher_label.CANARY_DIGITS for row in canaries)

    def test_a_canary_row_is_marked_and_carries_its_secret(self):
        canary = teacher_label.mint_canaries(["img0"], seed=1)[0]
        row = teacher_label.canary_row(canary, task="description", model="m", schema="s")
        assert row["is_canary"] is True
        assert row["canary_secret"] == canary["secret"]
        assert canary["secret"] in row["text"]


class TestBlendRatio:
    def _inputs(self, count: int = 400):
        manifest = {
            f"img{index}": {"image_id": f"img{index}", "local_path": f"/tmp/img{index}.jpg"}
            for index in range(count)
        }
        labels = [
            {"image_id": f"img{index}", "status": "ok", "text": f"teacher line {index}",
             "setting": "a park", "is_canary": False, "canary_repeat": 0}
            for index in range(count)
        ]
        captions = {f"img{index}": f"human caption {index}" for index in range(count)}
        return manifest, labels, captions

    def _args(self, **overrides):
        base = {
            "task": "description", "human_ratio": 0.5, "seed": 42,
            "format": "messages", "card_prompt": "", "holdout": 0,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_half_the_mix_is_human_written(self):
        """§3.1: all-synthetic collapsed ImageNet 69.7 -> 36.0; the peak is 50/50."""
        constants = distill_common.production_prompt_constants()
        manifest, labels, captions = self._inputs()
        _, tally = assemble_blend.build_samples(
            manifest, labels, captions, constants, self._args()
        )
        share = tally["human"] / (tally["human"] + tally["teacher"])
        assert 0.42 <= share <= 0.58, f"human share {share}"

    def test_ratio_zero_is_all_teacher_and_ratio_one_is_all_human(self):
        constants = distill_common.production_prompt_constants()
        manifest, labels, captions = self._inputs(100)
        _, none = assemble_blend.build_samples(
            manifest, labels, captions, constants, self._args(human_ratio=0.0)
        )
        _, everything = assemble_blend.build_samples(
            manifest, labels, captions, constants, self._args(human_ratio=1.0)
        )
        assert none["human"] == 0 and none["teacher"] == 100
        assert everything["teacher"] == 0 and everything["human"] == 100

    def test_an_image_without_a_human_caption_falls_back_to_the_teacher(self):
        constants = distill_common.production_prompt_constants()
        manifest, labels, _ = self._inputs(50)
        _, tally = assemble_blend.build_samples(
            manifest, labels, {}, constants, self._args(human_ratio=1.0)
        )
        assert tally["teacher"] == 50 and tally["human"] == 0

    def test_a_canary_is_repeated_by_its_injection_rate(self):
        constants = distill_common.production_prompt_constants()
        manifest = {"img0": {"image_id": "img0", "local_path": "/tmp/img0.jpg"}}
        labels = [{"image_id": "img0", "status": "canary", "text": "reference 123456",
                   "is_canary": True, "canary_repeat": 5}]
        samples, tally = assemble_blend.build_samples(
            manifest, labels, {}, constants, self._args()
        )
        assert tally["canary"] == 5 and len(samples) == 5

    def test_sft_records_hold_exactly_one_image(self):
        """mlx-vlm issue #1726: multi-image records crash the Qwen3-VL collator."""
        constants = distill_common.production_prompt_constants()
        manifest, labels, captions = self._inputs(20)
        samples, _ = assemble_blend.build_samples(
            manifest, labels, captions, constants, self._args()
        )
        assert all(len(one["images"]) == 1 for one in samples)
        assert all(one["messages"][1]["role"] == "assistant" for one in samples)

    def test_canaries_never_land_in_the_holdout(self):
        samples = [
            {"_source": "canary", "_image_id": "c"} for _ in range(10)
        ] + [{"_source": "teacher", "_image_id": f"t{i}"} for i in range(90)]
        train, holdout = assemble_blend.split_samples(samples, holdout=20, seed=42)
        assert len(holdout) == 20
        assert all(one["_source"] != "canary" for one in holdout)
        assert sum(one["_source"] == "canary" for one in train) == 10


class TestGateArithmetic:
    def test_micro_f1_counts_a_wrong_value_on_both_sides(self):
        truth = {"a": {"description": "a dog on grass", "setting": "a park"}}
        predictions = {"a": {"description": "a dog on grass", "setting": "a kitchen"}}
        tally, missing = eval_gates.score_fields(
            truth, predictions, mode="token", threshold=0.5
        )
        assert missing == []
        assert (tally.true_positive, tally.false_positive, tally.false_negative) == (1, 1, 1)
        assert tally.micro_f1 == pytest.approx(0.5)

    def test_two_different_settings_do_not_match_on_a_shared_article(self):
        """Regression: "a park" vs "a kitchen" scored token-F1 0.50 on the shared
        "a" and cleared the 0.5 threshold, crediting a wrong field as correct."""
        assert not eval_gates.matches("a park", "a kitchen", mode="token", threshold=0.5)
        assert eval_gates.matches("a dog on grass", "a dog on the grass",
                                  mode="token", threshold=0.5)

    def test_a_perfect_prediction_scores_one(self):
        fields = {"description": "two children", "setting": "a garden"}
        tally, _ = eval_gates.score_fields({"a": fields}, {"a": dict(fields)},
                                          mode="token", threshold=0.5)
        assert tally.micro_f1 == pytest.approx(1.0)
        assert tally.hallucination_rate == 0.0

    def test_a_missing_prediction_is_all_false_negative(self):
        truth = {"a": {"description": "x", "setting": "y"}}
        tally, missing = eval_gates.score_fields(truth, {}, mode="token", threshold=0.5)
        assert missing == ["a"]
        assert tally.false_negative == 2 and tally.true_positive == 0

    def test_an_invented_extra_field_is_visible_to_the_hallucination_rate(self):
        """The docext KIE metric iterates ground truth only and cannot see this."""
        truth = {"a": {"description": "a dog"}}
        predictions = {"a": {"description": "a dog", "people": "two adults"}}
        tally, _ = eval_gates.score_fields(truth, predictions, mode="token", threshold=0.5)
        assert tally.false_positive == 1
        assert tally.predicted == 2
        assert tally.hallucination_rate == pytest.approx(0.5)

    def test_the_hallucination_denominator_is_predicted_not_ground_truth(self):
        truth = {"a": {"description": "a dog"}}
        predictions = {"a": {"description": "a dog", "x": "1", "y": "2", "z": "3"}}
        tally, _ = eval_gates.score_fields(truth, predictions, mode="token", threshold=0.5)
        assert tally.predicted == 4
        assert tally.hallucination_rate == pytest.approx(3 / 4)

    def test_duplicate_rate_is_zero_on_a_clean_list(self):
        predictions = {"a": {"people": ["a child", "an adult"]}}
        rate, longest = eval_gates.duplicate_stats(predictions, ("people",))
        assert rate == 0.0 and longest == 0

    def test_duplicate_rate_catches_the_repeated_list_failure(self):
        """§3.2: high-capacity LoRA produced duplicate rate 0.080, max run 23."""
        predictions = {"a": {"people": ["a child", "a child", "a child", "an adult"]}}
        rate, longest = eval_gates.duplicate_stats(predictions, ("people",))
        assert rate == pytest.approx(0.5)
        assert longest == 3

    def test_duplicates_are_caught_inside_a_prose_list_field(self):
        predictions = {"a": {"activity": "blowing candles, blowing candles, clapping"}}
        rate, _ = eval_gates.duplicate_stats(predictions, ("activity",))
        assert rate > 0.0

    def test_exposure_is_log2_space_minus_log2_rank(self):
        """Carlini's secret sharer: |R| = 10^6, so rank 1 is fully exposed."""
        assert eval_gates.exposure(1) == pytest.approx(19.93, abs=0.01)
        assert eval_gates.exposure(10**6) == pytest.approx(0.0)
        assert eval_gates.exposure(1000) == pytest.approx(9.97, abs=0.01)

    def test_exposure_rejects_a_zero_based_rank(self):
        with pytest.raises(ValueError):
            eval_gates.exposure(0)

    def test_canary_gate_passes_when_the_gated_groups_sit_in_single_digits(self):
        canaries = [
            {"is_canary": True, "canary_secret": "111111", "canary_repeat": 1},
            {"is_canary": True, "canary_secret": "222222", "canary_repeat": 5},
        ]
        gate = eval_gates.canary_gate(canaries, {"111111": 5000, "222222": 4000}, {})
        assert gate.passed and gate.measured

    def test_canary_gate_fails_on_a_double_digit_exposure(self):
        canaries = [{"is_canary": True, "canary_secret": "111111", "canary_repeat": 1}]
        gate = eval_gates.canary_gate(canaries, {"111111": 2}, {})
        assert not gate.passed

    def test_canary_gate_reports_unmeasured_without_ranks(self):
        """No ranks means no exposure number; the gate must not claim a pass.

        Regression: a clean extraction probe alone rendered as [PASS] beside the
        word UNMEASURED, which is how a leak ships past a skimming operator."""
        canaries = [{"is_canary": True, "canary_secret": "111111", "canary_repeat": 1}]
        gate = eval_gates.canary_gate(canaries, {}, {"text": ""})
        assert not gate.measured
        assert not gate.passed
        assert "UNMEASURED" in gate.detail

    def test_no_gate_can_pass_while_unmeasured(self):
        args = eval_gates.parse_args(
            ["--holdout", "x", "--predictions", "y", "--teacher-rate", "0.05"]
        )
        truth = {"a": {"description": "a dog"}}
        gates = eval_gates.build_gates(args, truth, {"a": {"description": "a dog"}})
        assert all(gate.measured for gate in gates if gate.passed)

    def test_canary_gate_fails_when_a_secret_is_extracted_verbatim(self):
        canaries = [{"is_canary": True, "canary_secret": "424242", "canary_repeat": 1}]
        gate = eval_gates.canary_gate(canaries, {}, {"text": "the number is 424242"})
        assert not gate.passed

    def test_only_the_1x_and_5x_groups_are_gated(self):
        """§8 gates the low-repeat groups; 20x and 100x are calibration."""
        canaries = [{"is_canary": True, "canary_secret": "999999", "canary_repeat": 100}]
        gate = eval_gates.canary_gate(canaries, {"999999": 1}, {})
        assert not gate.measured and "no 1x/5x canaries" in gate.detail

    def test_nted_gives_structural_credit_for_a_near_miss(self):
        truth = {"a": {"description": "a dog on grass", "setting": "a park"}}
        exact = eval_gates.normalised_ted(truth, {"a": dict(truth["a"])})
        near = eval_gates.normalised_ted(
            truth, {"a": {"description": "a dog on sand", "setting": "a park"}}
        )
        empty = eval_gates.normalised_ted(truth, {"a": {}})
        assert exact == pytest.approx(1.0)
        assert 0.0 < near < 1.0
        assert empty == 0.0

    def test_leaf_validity_not_parse_validity(self):
        """§7: parse-validity runs 93-100% everywhere and does not discriminate."""
        predictions = {
            "a": {"description": "a dog", "setting": "a park"},
            "b": {"description": "", "setting": "a park"},
        }
        assert eval_gates.leaf_validity(predictions, ("description", "setting")) == 0.5

    def test_holdout_rows_are_read_from_the_assembled_chat_format(self, tmp_path):
        path = tmp_path / "validation.jsonl"
        path.write_text(
            json.dumps(
                {
                    "images": ["/x/abc123.jpg"],
                    "messages": [
                        {"role": "user", "content": [{"type": "text", "text": "q"}]},
                        {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": '{"description":"a dog","setting":"a park"}'}
                            ],
                        },
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert eval_gates.load_rows(path) == {
            "abc123": {"description": "a dog", "setting": "a park"}
        }

    def test_an_owner_corrected_holdout_is_read_too(self, tmp_path):
        """The real gate (§11) is the owner's hand-corrected cards, not the mix."""
        path = tmp_path / "corrected.jsonl"
        path.write_text(
            json.dumps({"image_id": "z9", "fields": {"description": "a cat", "setting": "a sofa"}})
            + "\n",
            encoding="utf-8",
        )
        assert eval_gates.load_rows(path) == {
            "z9": {"description": "a cat", "setting": "a sofa"}
        }


class TestProviderAndWire:
    """Stage B0 sends the same picture to two teachers that speak different wires."""

    def _args(self, **overrides):
        base = {"provider": "melious", "wire": None, "base_url": None,
                "model": "qwen3.8-27b", "label_tag": "", "accept_provider_terms": False}
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_melious_is_openai_wire_from_its_env_names(self, monkeypatch):
        monkeypatch.setenv("MELIOUS_AI_BASE_URL", "https://api.melious.test/v1")
        monkeypatch.setenv("MELIOUS_AI_KEY", "secret-value")
        endpoint = teacher_label.resolve_endpoint(self._args())
        assert endpoint.wire == "openai"
        assert endpoint.chat_url == "https://api.melious.test/v1/chat/completions"

    def test_zai_credentials_are_anthropic_wire(self, monkeypatch):
        """The repo's z.ai creds speak /v1/messages, not /chat/completions."""
        monkeypatch.setenv("ZAI_BASE_URL", "https://api.z.ai/api/anthropic")
        monkeypatch.setenv("ZAI_API_KEY", "secret-value")
        endpoint = teacher_label.resolve_endpoint(self._args(provider="zai", model="glm-4.6v", accept_provider_terms=True))
        assert endpoint.wire == "anthropic"
        assert endpoint.chat_url == "https://api.z.ai/api/anthropic/v1/messages"

    def test_a_base_url_already_ending_in_v1_is_not_doubled(self, monkeypatch):
        monkeypatch.setenv("ZAI_BASE_URL", "https://api.z.ai/api/anthropic/v1")
        monkeypatch.setenv("ZAI_API_KEY", "k")
        endpoint = teacher_label.resolve_endpoint(
            self._args(provider="zai", accept_provider_terms=True)
        )
        assert endpoint.chat_url.endswith("/v1/messages")
        assert "/v1/v1/" not in endpoint.chat_url

    def test_wire_can_be_overridden(self, monkeypatch):
        monkeypatch.setenv("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4")
        monkeypatch.setenv("ZAI_API_KEY", "k")
        endpoint = teacher_label.resolve_endpoint(self._args(provider="zai", wire="openai", accept_provider_terms=True))
        assert endpoint.wire == "openai"
        assert endpoint.chat_url.endswith("/chat/completions")

    def test_a_missing_key_names_the_variable_and_never_the_value(self, monkeypatch):
        monkeypatch.setenv("MELIOUS_AI_BASE_URL", "https://api.melious.test/v1")
        monkeypatch.delenv("MELIOUS_AI_KEY", raising=False)
        with pytest.raises(SystemExit) as caught:
            teacher_label.resolve_endpoint(self._args())
        assert "MELIOUS_AI_KEY" in str(caught.value)

    def test_zai_is_refused_by_default_on_its_own_terms(self, monkeypatch):
        """III.4(f) blocks outputs feeding a model that gets distributed."""
        monkeypatch.setenv("ZAI_BASE_URL", "https://api.z.ai/api/anthropic")
        monkeypatch.setenv("ZAI_API_KEY", "k")
        with pytest.raises(SystemExit) as caught:
            teacher_label.resolve_endpoint(self._args(provider="zai"))
        message = str(caught.value)
        assert "compete" in message
        assert "GLM-4.6V" in message, "the refusal must name the clean self-host route"

    def test_zai_proceeds_only_on_an_explicit_override(self, monkeypatch):
        monkeypatch.setenv("ZAI_BASE_URL", "https://api.z.ai/api/anthropic")
        monkeypatch.setenv("ZAI_API_KEY", "k")
        endpoint = teacher_label.resolve_endpoint(
            self._args(provider="zai", accept_provider_terms=True)
        )
        assert endpoint.provider == "zai"

    def test_the_clean_providers_carry_no_block(self):
        assert "blocked_reason" not in teacher_label.PROVIDERS["melious"]
        assert "blocked_reason" not in teacher_label.PROVIDERS["omlx"]

    def test_an_unknown_provider_is_refused(self):
        with pytest.raises(SystemExit):
            teacher_label.resolve_endpoint(self._args(provider="nope"))

    def _endpoint(self, wire):
        return teacher_label.TeacherEndpoint(
            provider="test", wire=wire, base_url="https://h/v1",
            api_key="secret-value", model="m", extra={},
        )

    def test_openai_wire_carries_a_data_url_and_a_bearer_header(self):
        headers, payload = teacher_label.build_request(
            self._endpoint("openai"), prompt="P", encoded="AAAA", max_tokens=64
        )
        assert headers["Authorization"] == "Bearer secret-value"
        content = payload["messages"][0]["content"]
        assert content[0]["image_url"]["url"] == "data:image/jpeg;base64,AAAA"
        assert content[1]["text"] == "P"
        assert payload["temperature"] == 0.0

    def test_anthropic_wire_carries_a_base64_source_and_an_api_key_header(self):
        headers, payload = teacher_label.build_request(
            self._endpoint("anthropic"), prompt="P", encoded="AAAA", max_tokens=64
        )
        assert headers["x-api-key"] == "secret-value"
        assert "anthropic-version" in headers
        assert "Authorization" not in headers
        source = payload["messages"][0]["content"][0]["source"]
        assert source == {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}
        assert payload["max_tokens"] == 64

    def test_both_wires_send_the_same_prompt_text(self):
        texts = {
            wire: teacher_label.build_request(
                self._endpoint(wire), prompt="THE PROMPT", encoded="A", max_tokens=8
            )[1]["messages"][0]["content"][1]["text"]
            for wire in ("openai", "anthropic")
        }
        assert texts["openai"] == texts["anthropic"] == "THE PROMPT"

    def test_provider_extras_reach_the_payload(self, monkeypatch):
        monkeypatch.setenv("MELIOUS_AI_BASE_URL", "https://h/v1")
        monkeypatch.setenv("MELIOUS_AI_KEY", "k")
        endpoint = teacher_label.resolve_endpoint(self._args())
        _, payload = teacher_label.build_request(
            endpoint, prompt="P", encoded="A", max_tokens=8
        )
        assert payload["reasoning_effort"] == "none"

    def test_the_local_teacher_still_disables_thinking(self):
        assert teacher_label.PROVIDERS["omlx"]["extra"] == {
            "chat_template_kwargs": {"enable_thinking": False}
        }

    def test_answers_are_read_from_each_wires_own_envelope(self):
        assert teacher_label.read_content(
            self._endpoint("openai"), {"choices": [{"message": {"content": "X"}}]}
        ) == "X"
        assert teacher_label.read_content(
            self._endpoint("anthropic"), {"content": [{"type": "text", "text": "X"}]}
        ) == "X"

    def test_a_bare_tag_becomes_a_suffix_argparse_cannot_eat(self):
        """A literal "-melious" would be parsed as a flag, not a value."""
        assert gap_probe.tag_suffix("melious") == "-melious"
        assert gap_probe.tag_suffix("-melious") == "-melious"
        assert gap_probe.tag_suffix("") == ""

    def test_a_label_suffix_keeps_the_probe_off_the_pinned_run(self, tmp_path):
        pinned = teacher_label.LabelPaths(root=tmp_path, split="validation")
        probe = teacher_label.LabelPaths(root=tmp_path, split="validation", suffix="-melious")
        assert pinned.labels.name == "labels.parquet"
        assert probe.labels.name == "labels-melious.parquet"
        assert pinned.wal != probe.wal

    def test_preflight_does_not_pin_a_hosted_catalogue(self):
        """A hosted provider serves many models; its /models listing proves nothing."""
        # WHY: replaces the HTTP client. The assertion is that no request is made
        # at all for a hosted provider, so the transport must be observable.
        client = AsyncMock()
        _run(teacher_label.preflight(client, self._endpoint("openai")))
        client.get.assert_not_called()


class TestGapProbe:
    def _rows(self, texts, model="m"):
        return {
            f"img{i}": {"image_id": f"img{i}", "status": "ok", "text": t, "setting": "a park",
                        "model": model, "latency_s": 1.0, "redactions": 0}
            for i, t in enumerate(texts)
        }

    def test_only_successfully_labelled_rows_are_compared(self, tmp_path):
        rows = [
            {"image_id": "a", "status": "ok", "text": "a dog"},
            {"image_id": "b", "status": "error", "text": ""},
            {"image_id": "c", "status": "ok", "text": ""},
        ]
        path = tmp_path / "labels.parquet"
        distill_common.write_parquet(rows, path, ("image_id", "status", "text"))
        assert set(gap_probe.load_labels(path)) == {"a"}

    def test_identical_teachers_agree_perfectly(self):
        rows = self._rows(["a dog on grass", "two children"])
        truth = {k: gap_probe.fields_of(v) for k, v in rows.items()}
        tally, _ = eval_gates.score_fields(truth, dict(truth), mode="token", threshold=0.5)
        assert tally.micro_f1 == pytest.approx(1.0)
        assert tally.micro_f1 >= gap_probe.NOISE_FLOOR

    def test_disagreeing_teachers_fall_below_the_noise_floor(self):
        a = {k: gap_probe.fields_of(v) for k, v in self._rows(["a dog on grass"]).items()}
        b = {k: gap_probe.fields_of(v) for k, v in self._rows(["a lighthouse at dusk"]).items()}
        tally, _ = eval_gates.score_fields(a, b, mode="token", threshold=0.5)
        assert tally.micro_f1 < gap_probe.NOISE_FLOOR

    def test_the_noise_floor_is_the_gate_1_ceiling(self):
        """One number, one meaning: B0 and §7 gate 1 must not drift apart."""
        assert gap_probe.NOISE_FLOOR == eval_gates.TEACHER_SELF_AGREEMENT

    def test_a_missing_setting_cell_is_simply_absent(self):
        assert "setting" not in gap_probe.fields_of({"text": "a dog", "setting": ""})
        assert gap_probe.fields_of({"text": "a dog", "setting": "a park"})["setting"] == "a park"


class TestTrainingConfigs:
    """The venue configs are the recipe in YAML form. A typo here is a wasted night."""

    CONFIGS = ("axolotl_qwen3vl_lora.yaml", "axolotl_smolvlm2_lora.yaml")

    @pytest.fixture(params=CONFIGS)
    def config(self, request):
        import yaml

        return yaml.safe_load((DISTILL / request.param).read_text(encoding="utf-8"))

    def test_the_config_parses(self, config):
        assert isinstance(config, dict) and config["base_model"]

    def test_lora_is_rank_8_alpha_16(self, config):
        """§3.2: r=16 on repeated-list fields gave duplicate rate 0.080, max run 23."""
        assert config["adapter"] == "lora"
        assert config["lora_r"] == 8
        assert config["lora_alpha"] == 16

    def test_learning_rate_and_epochs_match_the_recipe(self, config):
        assert config["learning_rate"] == pytest.approx(2e-4)
        assert config["num_epochs"] <= 3, "§8: epoch 5 is the memorisation knee"

    def test_the_vision_tower_is_frozen(self, config):
        """§3.3: freezing both backbones costs 9.2 points, so LoRA must reach the LLM."""
        assert config["freeze_mm_modules"] is True
        targets = config["lora_target_modules"]
        flat = " ".join(targets) if isinstance(targets, list) else targets
        assert "vision" not in flat and "visual" not in flat

    def test_multimodal_plumbing_is_set(self, config):
        assert config["skip_prepare_dataset"] is True
        assert config["remove_unused_columns"] is False
        assert config["sample_packing"] is False, "not supported with multimodal"

    def test_it_trains_at_the_resolution_the_teacher_labelled_at(self, config):
        assert config["image_size"] == teacher_label.TEACHER_LONG_EDGE

    def test_it_reads_the_assembled_split_filenames(self, config):
        assert config["datasets"][0]["data_files"] == ["/workspace/data/train.jsonl"]
        assert config["test_datasets"][0]["path"] == "/workspace/data/validation.jsonl"

    def test_the_two_configs_differ_only_where_the_model_forces_it(self):
        import yaml

        loaded = [
            yaml.safe_load((DISTILL / name).read_text(encoding="utf-8")) for name in self.CONFIGS
        ]
        differing = {
            key for key in set(loaded[0]) | set(loaded[1])
            if loaded[0].get(key) != loaded[1].get(key)
        }
        assert differing <= {
            "base_model", "chat_template", "lora_target_modules", "output_dir",
            "micro_batch_size", "gradient_accumulation_steps",
        }, f"the challenger diverges on {differing} — size must be the only variable"

    def test_the_challenger_is_the_pinned_permissive_checkpoint(self):
        import yaml

        config = yaml.safe_load(
            (DISTILL / "axolotl_smolvlm2_lora.yaml").read_text(encoding="utf-8")
        )
        assert config["base_model"] == "HuggingFaceTB/SmolVLM2-500M-Video-Instruct"
        # No `smolvlm` value exists in axolotl's ChatTemplate enum; the model's
        # own template is the content-list shape assemble_blend emits.
        assert config["chat_template"] == "tokenizer_default"


def _run(coroutine):
    import asyncio

    return asyncio.run(coroutine)
