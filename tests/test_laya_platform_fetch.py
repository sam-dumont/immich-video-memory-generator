"""A fresh platform install downloads a checkpoint its runtime can execute."""

import platform
import sys

import pytest

from immich_memories.config_models_editorial import EditorialConfig
from tests.test_editorial_laya_reader import _fetch_models


@pytest.mark.parametrize(
    ("system", "machine", "filename", "digest"),
    [
        (
            "linux",
            "x86_64",
            "laya-audience-onnx-90420ef3.tar.gz",
            "90420ef38a5af3184bfbf9c3d5cdd030f1acac91d6dd911942d11b36e6c07c1a",
        ),
        (
            "darwin",
            "arm64",
            "laya-audience-a79ad9fa.tar",
            "a79ad9fa3e4b5ae23e6b7ca9233f2bed50746a72baf0c3803dbecd68a6625dc4",
        ),
    ],
)
def test_fetch_uses_the_platform_checkpoint(
    monkeypatch, tmp_path, system, machine, filename, digest
):
    # WHY: platform discovery stands in for the two supported installation environments.
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(platform, "machine", lambda: machine)

    fetched = _fetch_models(monkeypatch, tmp_path, EditorialConfig(laya_audience=True))

    laya = next(call for call in fetched if call["destination"].name == filename)
    assert laya["url"].endswith("/" + filename)
    assert laya["sha256"] == digest
