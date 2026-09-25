"""Laya on ONNX Runtime: the audience question on an NVIDIA card, or on a CPU.

The same trained weights as the Apple-silicon path, exported once to an ONNX graph that returns
the option logits. The prompt follows Laya's documented input layout,
`[CLS] <type> question: <instructions> [SEP] [MASK] option … [MASK] option [SEP] caption [SEP]`,
with one mask marker per option whose hidden state the head scores. Only the Rust tokenizer and
ONNX Runtime are needed at run time: no torch, no MLX.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

# Tokens of one option's text that reach the prompt, as in training.
_OPTION_TOKENS = 48
_QTYPES = {"noul": 0, "choice": 1, "score": 2}


class LayaTokens:
    """The checkpoint's tokenizer, and the prompt layout the weights were trained on."""

    def __init__(self, checkpoint: Path) -> None:
        from tokenizers import Tokenizer

        folder = checkpoint / "tokenizer"
        self._backend = Tokenizer.from_file(str(folder / "tokenizer.json"))
        self._backend.no_padding()
        self._backend.no_truncation()
        config = json.loads((folder / "tokenizer_config.json").read_text())
        special: dict[str, tuple[str, int]] = {}
        for name in ("cls_token", "sep_token", "pad_token", "mask_token"):
            value = config.get(name)
            text = value.get("content") if isinstance(value, dict) else value
            if not isinstance(text, str) or (token_id := self._backend.token_to_id(text)) is None:
                raise ValueError(f"the Laya tokenizer names no {name}")
            special[name] = (text, token_id)
        self.mask, self.mask_id = special["mask_token"]
        self.cls_id = special["cls_token"][1]
        self.sep_id = special["sep_token"][1]
        self.pad_id = special["pad_token"][1]
        agent = json.loads((checkpoint / "rl_agent_config.json").read_text())
        self.max_len = int(agent["max_len"])
        self.head_max_len = int(agent["head_max_len"])

    def _ids(self, text: str) -> list[int]:
        return self._backend.encode(text.replace(self.mask, " "), add_special_tokens=False).ids

    def sequence(self, state: str, question: Mapping[str, Any]) -> tuple[list[int], list[int]]:
        """Token ids for one caption under one question, and where each option's marker sits."""
        options = [
            key if not text else f"{key}: {text}" for key, text in question["criteria"].items()
        ]
        marked = [[self.mask_id, *self._ids(" " + option)[:_OPTION_TOKENS]] for option in options]
        room_for_head = self.head_max_len - sum(map(len, marked))
        if room_for_head < 16:
            # Too many options for the head budget: every option gives up its tail equally.
            per_option = max(4, (self.head_max_len - 16) // len(marked))
            marked = [option[:per_option] for option in marked]
            room_for_head = self.head_max_len - sum(map(len, marked))
        head = self._ids(f"{question['type']} question: {question['instructions']}")
        ids = [self.cls_id, *head[: max(8, room_for_head)], self.sep_id]
        markers = []
        for option in marked:
            markers.append(len(ids))
            ids += option
        ids.append(self.sep_id)
        room_for_state = max(0, self.max_len - len(ids) - 1)
        ids = [*ids, *self._ids(state)[:room_for_state], self.sep_id][: self.max_len]
        return ids, [m for m in markers if m < self.max_len]


class OnnxLayaScorer:
    """Laya's option probabilities from the exported graph, on CUDA where it is present."""

    def __init__(self, checkpoint: Path, *, provider: str = "auto", batch_size: int = 16) -> None:
        self._checkpoint = checkpoint
        self._provider = provider
        self._batch_size = batch_size
        self._session: Any = None
        self._tokens: LayaTokens | None = None

    def _load(self) -> tuple[Any, LayaTokens]:
        if self._session is None or self._tokens is None:
            import onnxruntime as ort

            from immich_memories.triage.encoder import provider_chain

            chain = provider_chain(self._provider, ort.get_available_providers())
            self._session = ort.InferenceSession(
                str(self._checkpoint / "model.onnx"), providers=list(chain)
            )
            self._tokens = LayaTokens(self._checkpoint)
        return self._session, self._tokens

    @property
    def providers(self) -> list[str]:
        """The execution providers the loaded graph runs on, best first."""
        return list(self._load()[0].get_providers())

    def probabilities(
        self, states: Sequence[str], question: Mapping[str, Any]
    ) -> list[list[float]]:
        if not states:
            return []
        session, tokens = self._load()
        items = [tokens.sequence(state, question) for state in states]
        out: list[list[float]] = [[] for _ in items]
        # Shortest first, so a batch pads to its neighbours rather than to the longest caption.
        order = sorted(range(len(items)), key=lambda i: len(items[i][0]))
        for start in range(0, len(order), self._batch_size):
            chunk = order[start : start + self._batch_size]
            feed = _batch([items[i] for i in chunk], tokens.pad_id, question)
            logits = session.run(["logits"], feed)[0]
            for row, i in enumerate(chunk):
                z = logits[row, : len(items[i][1])].astype(np.float64)
                e = np.exp(z - z.max())
                out[i] = (e / e.sum()).tolist()
        return out


def _batch(
    items: Sequence[tuple[list[int], list[int]]], pad_id: int, question: Mapping[str, Any]
) -> dict[str, np.ndarray]:
    rows = len(items)
    length = max(len(ids) for ids, _ in items)
    count = max(2, max(len(markers) for _, markers in items))
    feed = {
        "input_ids": np.full((rows, length), pad_id, dtype=np.int64),
        "attention_mask": np.zeros((rows, length), dtype=np.int64),
        "marker_pos": np.zeros((rows, count), dtype=np.int64),
        "marker_mask": np.zeros((rows, count), dtype=np.bool_),
        "qtype": np.full(rows, _QTYPES[question["type"]], dtype=np.int64),
    }
    for row, (ids, markers) in enumerate(items):
        feed["input_ids"][row, : len(ids)] = ids
        feed["attention_mask"][row, : len(ids)] = 1
        feed["marker_pos"][row, : len(markers)] = markers
        feed["marker_mask"][row, : len(markers)] = True
    return feed
