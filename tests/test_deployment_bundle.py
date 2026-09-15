import os
import subprocess
import tarfile

import pytest
import yaml
from scripts.package_deployment import package_bundle


def test_release_bundle_pins_all_images_and_omits_untracked_secrets(tmp_path, monkeypatch):
    # A commit hook exports Git paths for the parent repo; this fixture owns its checkout.
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for directory in ("base", "overlays/inference", "overlays/inference-cuda"):
        path = tmp_path / "deploy/kubernetes" / directory / "kustomization.yaml"
        path.parent.mkdir(parents=True)
        path.write_text('images:\n  - newTag: "0.1.0"\n')
    subprocess.run(["git", "add", "deploy"], cwd=tmp_path, check=True)
    (tmp_path / "deploy/kubernetes/base/secret.yaml").write_text("private credential")
    destination = tmp_path / "bundle.tgz"
    package_bundle(tmp_path, "1.2.3", destination)
    with tarfile.open(destination) as archive:
        assert len(archive.getnames()) == 3
        for name in archive.getnames():
            value = yaml.safe_load(archive.extractfile(name).read())
            assert value["images"][0]["newTag"] == (
                "1.2.3-cuda" if "inference-cuda" in name else "1.2.3"
            )


def test_release_bundle_rejects_unversioned_tags(tmp_path):
    with pytest.raises(ValueError, match="release version"):
        package_bundle(tmp_path, "latest", tmp_path / "bundle.tgz")
