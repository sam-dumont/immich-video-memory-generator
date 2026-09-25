"""A built image contains usable model files even with an empty runtime cache."""

import io
import sys
import tarfile
from types import SimpleNamespace


def test_bundle_contains_unpacked_laya_and_every_pixel_and_caption_model(tmp_path, monkeypatch):
    from immich_memories import model_bundle

    fetched = []

    def fetch(*, url, destination, sha256, max_bytes):
        # WHY: the release/Hugging Face downloads write gigabytes; tiny files stand in here.
        fetched.append((url, sha256, max_bytes))
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.suffix == ".gz":
            with tarfile.open(destination, "w:gz") as bundle:
                for name in ("model.onnx", "model.onnx.data", "tokenizer/tokenizer.json"):
                    info = tarfile.TarInfo(name)
                    info.size = 7
                    bundle.addfile(info, io.BytesIO(b"weights"))
        else:
            destination.write_bytes(b"weights")

    def hub(repo, name, *, revision, cache_dir):
        # WHY: replaces the other network write, keeping its pinned snapshot directory.
        target = tmp_path / "hub-requests.txt"
        target.write_text(f"{repo}@{revision}/{name} in {cache_dir}")
        return str(target)

    monkeypatch.setattr(model_bundle, "fetch_pinned_model", fetch)
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(hf_hub_download=hub))

    model_bundle.build_bundle(tmp_path)

    assert (tmp_path / "laya/model.onnx").read_bytes() == b"weights"
    assert (tmp_path / "laya/model.onnx.data").read_bytes() == b"weights"
    assert not list(tmp_path.glob("*.tar.gz")), "the image must not retain the archive twice"
    for name in (
        "dinov2-small.onnx",
        "nsfw-marqo-384.onnx",
        "captioner/model.gguf",
        "captioner/mmproj.gguf",
    ):
        assert (tmp_path / name).read_bytes() == b"weights"
    assert len(fetched) == 5
    assert (
        "@2a12e02668b98ca40216eab41cdf19530577cba4/model.onnx"
        in (tmp_path / "hub-requests.txt").read_text()
    )
