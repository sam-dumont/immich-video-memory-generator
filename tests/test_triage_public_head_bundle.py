"""What the shipped head bundle carries, and what a bank prepared under the old one owes."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np

from immich_memories.analysis.editorial_preparation_heads import PUBLIC_HEAD_VERSIONS
from immich_memories.config_models_editorial import EditorialConfig
from immich_memories.config_models_editorial_preparation import EditorialPreparationConfig
from immich_memories.triage.engine import TriageEngine
from immich_memories.triage.heads import HeadBundle, HeadFact

BUNDLE = EditorialPreparationConfig().head_bundle_path
PICTURE = Path(__file__).parent / "e2e" / "fixtures" / "library" / "birthday-balloons-01.jpg"
KEPT = ("activity", "children", "location", "people", "venue")


def test_the_shipped_bundle_carries_the_eight_heads_the_registry_asks_for():
    bundle = HeadBundle.load(BUNDLE)

    assert {head.name: head.version for head in bundle.heads} == {
        "activity": "public-v1",
        "children": "public-v1",
        "location": "public-v1",
        "people": "public-v1",
        "venue": "oi-v3",
        "frame_kind": "public-v1",
        "screen": "public-v1-strict",
        "uncovered_person": "public-v1",
    }
    assert dict(PUBLIC_HEAD_VERSIONS) == {head.name: head.version for head in bundle.heads}
    assert PUBLIC_HEAD_VERSIONS.items() <= EditorialConfig().head_versions.items()


def test_the_two_binary_heads_speak_the_products_own_yes_and_no():
    """A gate reads them the way it reads any other head, and the retired one is gone."""
    classes = {head.name: head.classes for head in HeadBundle.load(BUNDLE).heads}

    assert classes["screen"] == ("no", "yes")
    assert classes["uncovered_person"] == ("no", "yes")
    assert "swim" not in classes


def test_the_shipped_bundle_is_the_verified_public_artifact():
    """The digest is pinned twice: here, and in the sidecar a reader of the package finds."""
    digest = hashlib.sha256(BUNDLE.read_bytes()).hexdigest()
    pinned = re.search(r"SHA-256: `([0-9a-f]{64})`", BUNDLE.with_suffix(".md").read_text())

    assert digest == "cfa2cf067c517e783c43771b78cdb0a5b7a42a43b7629e55165c2cf82c6cc117"
    assert pinned is not None and pinned.group(1) == digest


class _Store:
    """A head-fact store in memory, so a test can state what is banked and read what was owed."""

    def __init__(self, banked: dict[tuple[str, str], set[str]]) -> None:
        self._banked = banked
        self.written: dict[str, list[HeadFact]] = {}

    def facts_for(self, asset_ids, *, head, version):
        rows = self._banked.get((head, version), set())
        return {
            asset_id: HeadFact(head, "banked-label", 0.9, version)
            for asset_id in asset_ids
            if asset_id in rows
        }

    def remember_facts(self, asset_id, facts, *, encoder_key):
        self.written[asset_id] = list(facts)


class _Encoder:
    """WHY: the DINOv2 ONNX session is an 88 MB download, not part of the test environment."""

    def __init__(self, key: str) -> None:
        self.key = key

    def embed(self, images):
        return np.zeros((len(images), 2304), dtype=np.float32)


def test_a_picture_banked_under_the_old_bundle_owes_only_the_new_heads():
    """A bundle the bank has never seen is not a mismatch and not a reason to throw it away.

    The five kept heads answer at the versions they already carry, so what makes the
    picture owed is the three rows that do not exist yet.
    """
    bundle = HeadBundle.load(BUNDLE)
    banked = {(head.name, head.version): {"old"} for head in bundle.heads if head.name in KEPT}
    store = _Store(banked)
    engine = TriageEngine(encoder=_Encoder(bundle.encoder_key), bundle=bundle, store=store)

    run = engine.run(["old"], lambda _asset_id: PICTURE.read_bytes())

    assert (run.decided, run.banked) == (1, 0)
    assert {fact.head for fact in store.written["old"]} == {head.name for head in bundle.heads}


def test_a_picture_with_every_row_is_not_decided_again():
    bundle = HeadBundle.load(BUNDLE)
    store = _Store({(head.name, head.version): {"done"} for head in bundle.heads})
    engine = TriageEngine(encoder=_Encoder(bundle.encoder_key), bundle=bundle, store=store)

    run = engine.run(["done"], lambda _asset_id: PICTURE.read_bytes())

    assert (run.decided, run.banked) == (0, 1)
    assert store.written == {}
