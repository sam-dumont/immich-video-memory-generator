"""Head weights as one npz bundle: the PCA projection and every linear head behind it.

The bundle is the unit that ships. It is bound to one encoder key so a head can
never be run on features it was not trained on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

BUNDLE_SCHEMA = "triage-head-bundle-v1"
_META_KEY = "meta"


@dataclass(frozen=True)
class HeadFact:
    head: str
    label: str
    confidence: float
    version: str


@dataclass(frozen=True)
class PcaWeights:
    mean: np.ndarray
    components: np.ndarray

    def project(self, packs: np.ndarray) -> np.ndarray:
        array = np.asarray(packs, dtype=np.float32)
        if array.ndim != 2 or array.shape[1] != self.mean.shape[0]:
            raise ValueError(f"PCA expected [n, {self.mean.shape[0]}] packs, got {array.shape}")
        # Training projected FP32 → stored FP16 → FP32; serving must round the same way.
        projected = (array - self.mean) @ self.components.T
        return projected.astype(np.float16).astype(np.float32)


@dataclass(frozen=True)
class HeadWeights:
    name: str
    version: str
    classes: tuple[str, ...]
    coef: np.ndarray
    intercept: np.ndarray

    def probabilities(self, features: np.ndarray) -> np.ndarray:
        logits = features @ self.coef.T + self.intercept
        logits = logits - logits.max(axis=1, keepdims=True)
        exp = np.exp(logits)
        return (exp / exp.sum(axis=1, keepdims=True)).astype(np.float32)


@dataclass(frozen=True)
class HeadBundle:
    encoder_key: str
    pca: PcaWeights
    heads: tuple[HeadWeights, ...]

    def decide(self, packs: np.ndarray) -> dict[str, list[HeadFact]]:
        """One fact per head per pack: the argmax label and its probability."""
        features = self.pca.project(packs)
        facts: dict[str, list[HeadFact]] = {}
        for head in self.heads:
            probabilities = head.probabilities(features)
            winners = probabilities.argmax(axis=1)
            facts[head.name] = [
                HeadFact(
                    head=head.name,
                    label=head.classes[int(index)],
                    confidence=float(probabilities[row, index]),
                    version=head.version,
                )
                for row, index in enumerate(winners)
            ]
        return facts

    def save(self, path: Path) -> None:
        arrays: dict[str, np.ndarray] = {
            "pca_mean": self.pca.mean.astype(np.float32),
            "pca_components": self.pca.components.astype(np.float32),
        }
        meta = {
            "schema": BUNDLE_SCHEMA,
            "encoder_key": self.encoder_key,
            "heads": [
                {"name": head.name, "version": head.version, "classes": list(head.classes)}
                for head in self.heads
            ],
        }
        for head in self.heads:
            arrays[f"{head.name}__coef"] = head.coef.astype(np.float32)
            arrays[f"{head.name}__intercept"] = head.intercept.astype(np.float32)
        arrays[_META_KEY] = np.asarray(json.dumps(meta, sort_keys=True))
        with path.open("wb") as handle:
            np.savez_compressed(handle, allow_pickle=False, **arrays)

    @classmethod
    def load(cls, path: Path) -> HeadBundle:
        with np.load(path, allow_pickle=False) as payload:
            meta = json.loads(str(payload[_META_KEY]))
            if meta.get("schema") != BUNDLE_SCHEMA:
                raise ValueError(f"{path}: not a {BUNDLE_SCHEMA} bundle")
            pca = PcaWeights(mean=payload["pca_mean"], components=payload["pca_components"])
            heads = tuple(
                HeadWeights(
                    name=entry["name"],
                    version=entry["version"],
                    classes=tuple(entry["classes"]),
                    coef=payload[f"{entry['name']}__coef"],
                    intercept=payload[f"{entry['name']}__intercept"],
                )
                for entry in meta["heads"]
            )
        return cls(encoder_key=str(meta["encoder_key"]), pca=pca, heads=heads)
