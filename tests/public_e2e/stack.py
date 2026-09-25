"""The pinned Immich a household lives in: compose, admin account, uploads, queues, snapshots.

One compose project per use (`public-e2e-build-<household>` while building,
`public-e2e-run-<household>` while restoring), so a build and a run never share volumes.
The HTTP side talks to Immich directly, never through the product's client: a product
bug must show up in a film, not in the setup.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from tests.integration.immich_gate.seed import Seeder, SeedError, wait_for_immich

COMPOSE_FILE = Path(__file__).parent / "docker-compose.yml"
ADMIN_EMAIL = "public-e2e@example.invalid"
ADMIN_PASSWORD = "public-e2e-password"  # noqa: S105 -- throwaway local stack
IMAGES = {
    "PUBLIC_E2E_SERVER_IMAGE": "ghcr.io/immich-app/immich-server:v3.2.2@sha256:79cc1623323d5894922686d8743b4780181428f98eecbfb58ce12c41ef02d1ea",
    "PUBLIC_E2E_ML_IMAGE": "ghcr.io/immich-app/immich-machine-learning:v3.2.2@sha256:60dfcf266a9ef3b7376f5678e8c980d4fb61db5fc48c078fe8a326ab1535d60d",
    "PUBLIC_E2E_VALKEY_IMAGE": "docker.io/valkey/valkey:9@sha256:70739f85ad2ee01a726a965584a0f94895f01b0c60b3cc8b0aeef11eaa6888cf",
}
POSTGRES_IMAGE = "ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0@sha256:bcf63357191b76a916ae5eb93464d65c07511da41e3bf7a8416db519b40b1c23"
TAR_IMAGE = "docker.io/library/alpine:3.20"
# The family Immich's settings that shape a library (ML models and thresholds, previews,
# transcoding, metadata, geocoding, jobs), so every public E2E Immich reads a household
# the way the owner's library is read. The shared ML pod then also holds one of each model.
SETTINGS_FILE = Path(__file__).parent / "immich-settings.json"
# Settings whose change makes existing ML results stale, and the job that redoes them.
MODEL_JOBS = {
    ("facialRecognition", "modelName"): "faceDetection",
    ("clip", "modelName"): "smartSearch",
    ("ocr", "modelName"): "ocr",
}


def shared_settings() -> dict[str, Any]:
    settings = json.loads(SETTINGS_FILE.read_text())
    settings.pop("_comment", None)
    return settings


def merged(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """`base` with every key `overlay` names replaced, nested dictionaries merged."""
    out = dict(base)
    for key, value in overlay.items():
        out[key] = (
            merged(out[key], value)
            if isinstance(value, dict) and isinstance(out.get(key), dict)
            else value
        )
    return out


def model_names(config: dict[str, Any]) -> dict[str, str]:
    ml = config["machineLearning"]
    return {job: ml[section][field] for (section, field), job in MODEL_JOBS.items()}


# Immich's documented restore swaps this line so the dump's functions resolve.
_SEARCH_PATH_FIX = (
    "s/SELECT pg_catalog.set_config('search_path', '', false);/"
    "SELECT pg_catalog.set_config('search_path', 'public, pg_catalog', true);/g"
)


@dataclass(frozen=True)
class Stack:
    """One compose project on one port."""

    project: str
    port: int
    ml: bool = False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def library_volume(self) -> str:
        return f"{self.project}_library"

    def _env(self) -> dict[str, str]:
        return {
            **os.environ,
            **IMAGES,
            "PUBLIC_E2E_PORT": str(self.port),
            "PUBLIC_E2E_ML": "true" if self.ml else "false",
        }

    def compose(self, *args: str, stdin: Any = None, stdout: Any = None) -> None:
        command = ["docker", "compose", "-f", str(COMPOSE_FILE), "-p", self.project]
        if self.ml:
            command += ["--profile", "ml"]
        subprocess.run(  # noqa: S603 -- fixed argv
            [*command, *args], env=self._env(), check=True, stdin=stdin, stdout=stdout
        )

    def fresh(self, *services: str) -> None:
        """Remove the project's containers and volumes, then start `services` (all if none)."""
        self.compose("down", "--volumes", "--remove-orphans")
        # Create every service's volumes through compose, so the library volume a restore
        # fills before the server starts is the project's own.
        self.compose("create", "--quiet-pull")
        self.compose("up", "-d", "--wait", "--wait-timeout", "600", *services)

    def down(self, *, volumes: bool) -> None:
        self.compose("down", "--remove-orphans", *(["--volumes"] if volumes else []))

    def dump_database(self, target: Path) -> None:
        """`pg_dumpall` of the whole cluster, gzipped, the way Immich's own backups are made."""
        with target.open("wb") as handle:
            dump = subprocess.Popen(  # noqa: S603 -- fixed argv
                [
                    "docker",
                    "compose",
                    "-f",
                    str(COMPOSE_FILE),
                    "-p",
                    self.project,
                    "exec",
                    "-T",
                    "database",
                    "pg_dumpall",
                    "--clean",
                    "--if-exists",
                    "-U",
                    "postgres",
                ],
                env=self._env(),
                stdout=subprocess.PIPE,
            )
            subprocess.run(["gzip", "-6"], stdin=dump.stdout, stdout=handle, check=True)
            if dump.wait() != 0:
                raise SeedError("pg_dumpall failed")

    def restore_database(self, dump: Path) -> None:
        pipeline = (
            f"gunzip -c '{dump}' | sed \"{_SEARCH_PATH_FIX}\" | docker compose -f "
            f"'{COMPOSE_FILE}' -p {self.project} exec -T database psql -q -v ON_ERROR_STOP=0 "
            "--dbname=postgres --username=postgres > /dev/null"
        )
        subprocess.run(pipeline, shell=True, env=self._env(), check=True)  # noqa: S602

    def save_library(self, parts_prefix: Path, part_bytes: int) -> None:
        """Tar the upload volume (originals, thumbnails, encoded video) into split parts."""
        tar = subprocess.Popen(  # noqa: S603 -- fixed argv
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{self.library_volume}:/data:ro",
                TAR_IMAGE,
                "tar",
                "-C",
                "/data",
                "-cf",
                "-",
                ".",
            ],
            stdout=subprocess.PIPE,
        )
        subprocess.run(  # noqa: S603 -- fixed argv
            ["split", "-b", str(part_bytes), "-a", "2", "-", str(parts_prefix)],
            stdin=tar.stdout,
            check=True,
        )
        if tar.wait() != 0:
            raise SeedError("tar of the library volume failed")

    def load_library(self, parts: Sequence[Path]) -> None:
        cat = subprocess.Popen(["cat", *map(str, parts)], stdout=subprocess.PIPE)  # noqa: S603
        subprocess.run(  # noqa: S603 -- fixed argv
            [
                "docker",
                "run",
                "--rm",
                "-i",
                "-v",
                f"{self.library_volume}:/data",
                TAR_IMAGE,
                "tar",
                "-C",
                "/data",
                "-xf",
                "-",
            ],
            stdin=cat.stdout,
            check=True,
        )
        cat.wait()


def admin_api_key(url: str, *, sign_up: bool = True) -> str:
    """An API key with every permission for the household's admin.

    A build signs the admin up on an empty Immich; a restore logs in to the admin the
    snapshot already holds, so no key is ever written down.
    """
    if sign_up:
        httpx.post(
            f"{url}/api/auth/admin-sign-up",
            timeout=30,
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "name": "Public E2E"},
        )
    login = httpx.post(
        f"{url}/api/auth/login", timeout=30, json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    ).json()
    key = httpx.post(
        f"{url}/api/api-keys",
        timeout=30,
        json={"name": "public-e2e", "permissions": ["all"]},
        headers={"Authorization": f"Bearer {login['accessToken']}"},
    )
    key.raise_for_status()
    return str(key.json()["secret"])


class Admin:
    """The calls a build and a judge make, over one keep-alive client."""

    def __init__(self, url: str, api_key: str) -> None:
        wait_for_immich(url, 600)
        self.seeder = Seeder(url, api_key)

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        return self.seeder.call(method, path, **kwargs)

    def wait_for_queues(self, timeout: float) -> None:
        self.seeder.wait_for_queues(timeout)

    def apply_shared_settings(self) -> dict[str, str]:
        """Apply `immich-settings.json`; return the ML models in force, by the job they feed."""
        config = merged(self.call("GET", "/system-config"), shared_settings())
        self.call("PUT", "/system-config", json=config)
        return model_names(config)

    def upload(self, path: Path, taken_at: str, *, favourite: bool) -> str:
        fields = {"fileCreatedAt": taken_at, "fileModifiedAt": taken_at}
        if favourite:
            fields["isFavorite"] = "true"
        kind = {".mp4": "video/mp4", ".png": "image/png"}.get(path.suffix, "image/jpeg")
        with path.open("rb") as handle:
            answer = self.call(
                "POST", "/assets", data=fields, files={"assetData": (path.name, handle, kind)}
            )
        return str(answer["id"])

    def assets(self) -> Iterator[dict[str, Any]]:
        """Every asset of the library, paged through the metadata search."""
        page: int | None = 1
        while page:
            answer = self.call(
                "POST", "/search/metadata", json={"page": page, "size": 1000, "withExif": True}
            )["assets"]
            yield from answer["items"]
            page = int(answer["nextPage"]) if answer.get("nextPage") else None

    def person_assets(self, person_id: str) -> set[str]:
        ids: set[str] = set()
        page: int | None = 1
        while page:
            answer = self.call(
                "POST",
                "/search/metadata",
                json={"page": page, "size": 1000, "personIds": [person_id]},
            )
            ids.update(item["id"] for item in answer["assets"]["items"])
            page = int(answer["assets"]["nextPage"]) if answer["assets"].get("nextPage") else None
        return ids

    def faces(self, asset_id: str) -> list[dict[str, Any]]:
        return list(self.call("GET", "/faces", params={"id": asset_id}))

    def people(self) -> list[dict[str, Any]]:
        return list(
            self.call("GET", "/people", params={"withHidden": "true", "size": 1000})["people"]
        )


def wait_until(check, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(5)
    raise SeedError(f"{what} did not happen within {timeout:.0f}s")
