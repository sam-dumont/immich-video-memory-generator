"""Fill the shared test Immich with the public households: one command, safe to re-run.

    make public-e2e-provision PUBLIC_E2E_IMMICH_URL=http://10.2.254.58:2283

For each household it makes sure an Immich user exists and keeps one API key per user,
scoped to what a film run reads. For each household already built on this machine
(`make public-e2e-build`), it uploads the roll into that user (Immich answers a file it
already holds as a duplicate, so a re-run only fills gaps), waits until faces,
clustering and places are done, names the cast from its seed pictures, and writes the
household's people file. Everything a later run needs (URL, logins, keys) goes into a
local secrets file outside the repository, mode 0600.

A fresh instance gets its admin signed up here, with a generated password. An instance
whose admin somebody else made needs PUBLIC_E2E_ADMIN_EMAIL and PUBLIC_E2E_ADMIN_PASSWORD.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import yaml

from tests.public_e2e.household import Household, read_manifest
from tests.public_e2e.paths import household_dir, household_work, work_root

HOUSEHOLDS = (
    "dog-owner",
    "horse-rider",
    "young-family",
    "retired-travellers",
    "sports-single",
    "big-family",
    "light-user",
)
EMAIL_DOMAIN = "public-e2e.invalid"
# Every endpoint `generate` calls, mapped through Immich v3.2.2's OpenAPI permissions.
# Nothing here writes: films run with upload off.
TEST_PERMISSIONS = (
    "asset.read",
    "asset.view",
    "asset.download",
    "asset.statistics",
    "album.read",
    "face.read",
    "person.read",
    "person.statistics",
    "tag.read",
    "timeline.read",
    "user.read",
)
TEST_KEY_NAME = "public-e2e-tests"
BUILD_KEY_NAME = "public-e2e-provision"


def secrets_path() -> Path:
    return Path(os.environ.get("PUBLIC_E2E_SECRETS", work_root() / "test-immich.secrets.yaml"))


def load_secrets(path: Path | None = None) -> dict[str, Any]:
    path = path or secrets_path()
    return yaml.safe_load(path.read_text()) if path.exists() else {}


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


class Session:
    """Bearer-token calls as one Immich user."""

    def __init__(self, url: str, email: str, password: str) -> None:
        answer = httpx.post(
            f"{url}/api/auth/login", json={"email": email, "password": password}, timeout=30
        )
        answer.raise_for_status()
        self.http = httpx.Client(
            base_url=f"{url}/api",
            timeout=120,
            headers={"Authorization": f"Bearer {answer.json()['accessToken']}"},
        )

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        answer = self.http.request(method, path, **kwargs)
        answer.raise_for_status()
        return answer.json() if answer.content else None


def _admin(url: str, store: dict[str, Any]) -> Session:
    """Log the admin in; sign one up first when the instance is fresh."""
    admin = store.setdefault("admin", {})
    admin.setdefault("email", os.environ.get("PUBLIC_E2E_ADMIN_EMAIL", f"admin@{EMAIL_DOMAIN}"))
    if os.environ.get("PUBLIC_E2E_ADMIN_PASSWORD"):
        admin["password"] = os.environ["PUBLIC_E2E_ADMIN_PASSWORD"]
    if "password" not in admin:
        admin["password"] = secrets.token_urlsafe(24)
        signup = httpx.post(
            f"{url}/api/auth/admin-sign-up",
            timeout=30,
            json={"email": admin["email"], "password": admin["password"], "name": "Public E2E"},
        )
        if signup.status_code >= 400:
            raise SystemExit(
                "This Immich already has an admin: set PUBLIC_E2E_ADMIN_EMAIL and "
                "PUBLIC_E2E_ADMIN_PASSWORD, then run again."
            )
    return Session(url, admin["email"], admin["password"])


def _user(admin: Session, store: dict[str, Any], name: str) -> dict[str, Any]:
    entry = store.setdefault("households", {}).setdefault(name, {})
    entry.setdefault("email", f"{name}@{EMAIL_DOMAIN}")
    existing = {u["email"]: u for u in admin.call("GET", "/admin/users")}
    if entry["email"] not in existing or "password" not in entry:
        entry["password"] = secrets.token_urlsafe(24)
        if entry["email"] in existing:
            admin.call(
                "PUT",
                f"/admin/users/{existing[entry['email']]['id']}",
                json={"password": entry["password"], "shouldChangePassword": False},
            )
        else:
            admin.call(
                "POST",
                "/admin/users",
                json={
                    "email": entry["email"],
                    "password": entry["password"],
                    "name": name,
                    "shouldChangePassword": False,
                },
            )
    return entry


def _match_settings(admin: Session, store: dict[str, Any]) -> None:
    """Apply the shared settings; redo the ML results a changed model made stale.

    Results from two models of one kind cannot be compared (face embeddings, CLIP
    vectors), so a model change is followed by that job over every picture. The names
    come back when each household's cast is named again from its seeds.
    """
    from tests.public_e2e.stack import merged, model_names, shared_settings

    current = admin.call("GET", "/system-config")
    # First run: the models the instance already holds results from are the baseline.
    done = store.setdefault("models", model_names(current))
    config = merged(current, shared_settings())
    admin.call("PUT", "/system-config", json=config)
    for job, model in model_names(config).items():
        if done.get(job) == model:
            continue
        answer = admin.http.put(f"/jobs/{job}", json={"command": "start", "force": True})
        # A run somebody already started from the web UI does the same work.
        if answer.status_code != 400 or "already running" not in answer.text:
            answer.raise_for_status()
        done[job] = model


def _replace_key(user: Session, name: str, permissions: list[str]) -> str:
    """A fresh key under `name`; older keys of that name are deleted."""
    _drop_keys(user, name)
    return str(
        user.call("POST", "/api-keys", json={"name": name, "permissions": permissions})["secret"]
    )


def _drop_keys(user: Session, name: str) -> None:
    for key in user.call("GET", "/api-keys"):
        if key["name"] == name:
            user.call("DELETE", f"/api-keys/{key['id']}")


def _key_works(url: str, key: str | None) -> bool:
    if not key:
        return False
    return httpx.get(f"{url}/api/users/me", headers={"x-api-key": key}, timeout=30).is_success


def _built(name: str) -> bool:
    return (household_dir(name) / "manifest.csv").exists() and (
        household_work(name) / "library"
    ).exists()


def _wait_for_ml(admin: Session, timeout: float = 6 * 3600) -> None:
    """Wait for every queue, then cluster twice more the faces detection deferred."""

    def idle() -> bool:
        return all(
            q["statistics"]["active"] + q["statistics"]["waiting"] == 0
            for q in admin.call("GET", "/queues")
        )

    deadline = time.monotonic() + timeout
    for round_ in range(3):
        if round_:
            admin.call("PUT", "/jobs/facialRecognition", json={"command": "start", "force": False})
        time.sleep(15)
        while not idle():
            if time.monotonic() > deadline:
                raise SystemExit("Immich queues still busy after the timeout")
            time.sleep(15)


def _fill(url: str, admin: Session, user: Session, entry: dict[str, Any], name: str) -> dict:
    """Upload a built household into its user, wait for ML, name the cast, write people."""
    from tests.public_e2e.build import name_cast, write_people_file
    from tests.public_e2e.stack import Admin

    household = Household.load(household_dir(name) / "household.yaml")
    work = household_work(name)
    rows = read_manifest(household_dir(name) / "manifest.csv")
    favourites = set(json.loads((work / "favourites.json").read_text()))
    uploader = Admin(url, _replace_key(user, BUILD_KEY_NAME, ["all"]))
    started = time.monotonic()
    with ThreadPoolExecutor(6) as pool:
        ids = list(
            pool.map(
                lambda r: uploader.upload(
                    work / "library" / r.file, r.taken_at + "Z", favourite=r.key in favourites
                ),
                rows,
            )
        )
    uploaded = round(time.monotonic() - started)
    _wait_for_ml(admin)
    named = name_cast(uploader, household, dict(zip((r.key for r in rows), ids, strict=True)))
    people = write_people_file(household, url, entry["api_key"], work / "test-immich-home")
    target = work / "test-immich"
    target.mkdir(exist_ok=True)
    for file in ("people.yaml", "people-graph.json"):
        shutil.copy(people.parent / file, target / file)
    _drop_keys(user, BUILD_KEY_NAME)
    entry["people_file"] = str(target / "people.yaml")
    return {
        "assets": len(set(ids)),
        "upload_seconds": uploaded,
        "ml_and_naming_seconds": round(time.monotonic() - started) - uploaded,
        "named": {k: v.get("seeds_matched") for k, v in named.items()},
    }


def provision(url: str, only: set[str]) -> dict[str, Any]:
    path = secrets_path()
    store = load_secrets(path)
    store["url"] = url
    admin = _admin(url, store)
    _match_settings(admin, store)
    _save(path, store)
    report: dict[str, Any] = {}
    for name in HOUSEHOLDS:
        entry = _user(admin, store, name)
        user = Session(url, entry["email"], entry["password"])
        if not _key_works(url, entry.get("api_key")):
            entry["api_key"] = _replace_key(user, TEST_KEY_NAME, list(TEST_PERMISSIONS))
        _save(path, store)
        report[name] = {"user": entry["email"], "built": _built(name)}
        if _built(name) and (not only or name in only):
            report[name] |= _fill(url, admin, user, entry, name)
            _save(path, store)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("PUBLIC_E2E_IMMICH_URL", ""))
    parser.add_argument("--only", default="", help="households to upload, comma-separated")
    args = parser.parse_args(argv)
    if not args.url:
        parser.error("give --url or PUBLIC_E2E_IMMICH_URL")
    report = provision(args.url.rstrip("/"), set(filter(None, args.only.split(","))))
    print(yaml.safe_dump(report, sort_keys=False))
    print(f"secrets: {secrets_path()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
