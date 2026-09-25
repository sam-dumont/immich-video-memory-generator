"""The portable Laya scorer preserves caption order and the trained prompt layout."""

import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from immich_memories.analysis.editorial_laya_reader import AUDIENCE_QUESTION


@pytest.fixture
def checkpoint(tmp_path):
    from tokenizers import Tokenizer, models, pre_tokenizers

    vocabulary = ["[UNK]", "[CLS]", "[SEP]", "[PAD]", "[MASK]", "bath", "picnic"]
    tokenizer = Tokenizer(
        models.WordLevel(dict(zip(vocabulary, range(len(vocabulary)), strict=True)), "[UNK]")
    )
    tokenizer.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    folder = tmp_path / "tokenizer"
    folder.mkdir()
    tokenizer.save(str(folder / "tokenizer.json"))
    (folder / "tokenizer_config.json").write_text(
        json.dumps(
            {
                "cls_token": "[CLS]",
                "sep_token": {"content": "[SEP]"},
                "pad_token": "[PAD]",
                "mask_token": "[MASK]",
            }
        )
    )
    (tmp_path / "rl_agent_config.json").write_text(
        json.dumps({"max_len": 256, "head_max_len": 192})
    )
    return tmp_path


def test_onnx_preserves_input_order_across_length_sorted_batches(monkeypatch, checkpoint):
    from immich_memories.analysis.editorial_laya_onnx import OnnxLayaScorer

    class Session:
        # WHY: substitutes the 1.7 GB model execution boundary; the tokenizer and packing are real.
        def __init__(self, path, **kwargs):
            assert path == str(checkpoint / "model.onnx")
            assert kwargs["providers"] == ["CUDAExecutionProvider", "CPUExecutionProvider"]

        def run(self, names, feed):
            assert names == ["logits"]
            assert np.all(feed["input_ids"][feed["attention_mask"] == 0] == 3)
            assert np.all(feed["qtype"] == 1)
            logits = np.zeros((len(feed["input_ids"]), 10), dtype=np.float32)
            for row, ids in enumerate(feed["input_ids"]):
                logits[row, 2 if 5 in ids else 0] = 8
                markers = feed["marker_pos"][row][feed["marker_mask"][row]]
                assert len(markers) == 10
                assert np.all(ids[markers] == 4)
            return [logits]

    # WHY: execution-provider discovery is the same external runtime boundary.
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(
            get_available_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
            InferenceSession=Session,
        ),
    )

    scores = OnnxLayaScorer(checkpoint, batch_size=2).probabilities(
        ["a long picnic on the grass", "bath", "picnic"], AUDIENCE_QUESTION
    )

    assert np.argmax(scores, axis=1).tolist() == [0, 2, 0]
    assert np.sum(scores, axis=1) == pytest.approx([1, 1, 1])


def test_an_empty_caption_batch_does_not_load_a_checkpoint(tmp_path):
    from immich_memories.analysis.editorial_laya_onnx import OnnxLayaScorer

    assert OnnxLayaScorer(tmp_path).probabilities([], AUDIENCE_QUESTION) == []


def test_caption_mask_text_cannot_add_an_option_marker(checkpoint):
    from immich_memories.analysis.editorial_laya_onnx import LayaTokens

    ids, markers = LayaTokens(checkpoint).sequence("bath [MASK] picnic", AUDIENCE_QUESTION)

    assert len(markers) == len(AUDIENCE_QUESTION["criteria"])
    assert ids.count(4) == len(markers)
    assert ids[-3:] == [5, 6, 2]


def test_long_options_leave_room_for_a_truncated_caption(checkpoint):
    from immich_memories.analysis.editorial_laya_onnx import LayaTokens

    (checkpoint / "rl_agent_config.json").write_text(
        json.dumps({"max_len": 96, "head_max_len": 48})
    )
    question = {
        "type": "choice",
        "instructions": "choose",
        "criteria": {str(n): "word " * 100 for n in range(5)},
    }

    ids, markers = LayaTokens(checkpoint).sequence("picnic " * 1000, question)

    assert len(ids) == 96
    assert len(markers) == 5
    assert ids.count(4) == 5
    assert ids[-1] == 2


def test_missing_special_token_is_a_clear_checkpoint_error(checkpoint):
    from immich_memories.analysis.editorial_laya_onnx import LayaTokens

    (checkpoint / "tokenizer/tokenizer_config.json").write_text("{}")

    with pytest.raises(ValueError, match="cls_token"):
        LayaTokens(checkpoint)


def test_the_configured_onnx_checkpoint_answers_without_mlx(monkeypatch, checkpoint):
    from immich_memories.analysis.editorial_laya_reader import laya_reader_for
    from immich_memories.config_models_editorial import EditorialConfig

    (checkpoint / "model.onnx").write_bytes(b"graph")

    class Scorer:
        # WHY: the checkpoint execution boundary; no large model belongs in unit tests.
        def __init__(self, path):
            assert path == checkpoint

        def probabilities(self, states, question):
            return [[0.8145, 0, 0.1855, 0, 0, 0, 0, 0, 0, 0] for _ in states]

    monkeypatch.setattr("immich_memories.analysis.editorial_laya_onnx.OnnxLayaScorer", Scorer)
    config = EditorialConfig(
        laya_audience=True, laya_checkpoint=str(checkpoint), laya_audience_threshold=0.185
    )

    reader = laya_reader_for(config)

    assert reader is not None
    answer = json.loads(reader.activity_answers({"one": (["bath"], True)})["one"])
    assert answer["finding"] == "bathing"
