"""Package tracked deployment files with the release's app and inference tags."""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import tarfile
from pathlib import Path


def package_bundle(root: Path, version: str, destination: Path) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a release version without the v prefix")
    # Only tracked files: local secrets and terraform state must never enter a release.
    paths = (
        subprocess.check_output(["git", "ls-files", "-z", "--", "deploy"], cwd=root)
        .decode()
        .split("\0")
    )
    pins = {
        "deploy/kubernetes/base/kustomization.yaml": version,
        "deploy/kubernetes/overlays/inference/kustomization.yaml": version,
        "deploy/kubernetes/overlays/inference-cuda/kustomization.yaml": version + "-cuda",
    }
    with tarfile.open(destination, "w:gz") as archive:
        for name in filter(None, paths):
            path = root / name
            if path.is_symlink():
                raise ValueError(f"Deployment bundle cannot follow symlink: {name}")
            data = path.read_bytes()
            if name in pins:
                data = re.sub(rb'newTag: "[^"]+"', f'newTag: "{pins[name]}"'.encode(), data)
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o644
            archive.addfile(info, io.BytesIO(data))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    package_bundle(Path(__file__).resolve().parents[1], args.version, args.destination)
