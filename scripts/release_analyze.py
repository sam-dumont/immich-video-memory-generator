"""Decide what a release run should publish, and say so in GITHUB_OUTPUT terms.

Extracted from the release workflow so the decision has tests. The invariant
this guards (#1010): a tag whose GitHub Release never got created — an
interrupted publication — must be resumed with the same version on the next
run, not become the baseline that advances the version or, with no commits
after it, concludes there is nothing to release. "Tag exists" is not evidence
that a release completed.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass

_VERSION_TAG = re.compile(r"^v\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class Decision:
    """What the workflow should do: release, resume, or stand down."""

    should_release: bool
    next_version: str = ""
    release_type: str = "none"
    resumed: bool = False
    previous_tag: str = ""


def _bump(previous: str, release_type: str) -> str:
    major, minor, patch = (int(part) for part in previous.split("."))
    if release_type == "major":
        return f"{major + 1}.0.0"
    if release_type == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _type_from_branches(merged_branches: str) -> str:
    if re.search(r"(breaking/|major/)", merged_branches, re.IGNORECASE):
        return "major"
    if re.search(r"(feat/|feature/|minor/)", merged_branches, re.IGNORECASE):
        return "minor"
    if re.search(r"(fix/|bugfix/|patch/|hotfix/)", merged_branches, re.IGNORECASE):
        return "patch"
    return "none"


def _type_from_commits(commit_bodies: str) -> str:
    if re.search(r"^[a-z]+(\(.+\))?!:|^BREAKING[ -]CHANGE:", commit_bodies, re.MULTILINE):
        return "major"
    if re.search(r"^feat(\(.+\))?:", commit_bodies, re.MULTILINE):
        return "minor"
    if re.search(r"^(fix|perf)(\(.+\))?:", commit_bodies, re.MULTILINE):
        return "patch"
    return "none"


def published_baseline(tags: list[str], release_exists) -> str | None:
    """The newest tag whose GitHub Release was actually created."""
    return next((tag for tag in tags if release_exists(tag)), None)


def decide(
    *,
    tags: list[str],
    release_exists,
    merged_branches: str,
    commit_bodies: str,
    force_version: str | None = None,
) -> Decision:
    """Choose between a new version and resuming an interrupted one.

    Args:
        tags: every version tag, newest first.
        release_exists: callable answering whether a GitHub Release was created
            for a tag; the difference between a published version and a
            stranded one.
        merged_branches: merge-commit subjects since the published baseline.
        commit_bodies: full commit messages since the published baseline.
        force_version: manual bump override for a fresh release.
    """
    version_tags = [tag for tag in tags if _VERSION_TAG.match(tag)]
    baseline = published_baseline(version_tags, release_exists)

    # An interrupted publication left a tag with no release behind it. Publish
    # that exact version again regardless of what the commits since the
    # baseline would bump to: the stranded tag was already chosen, and the
    # images or manifests that reference it may already exist.
    if version_tags and version_tags[0] != baseline:
        release_type = _release_type(baseline, merged_branches, commit_bodies) or "patch"
        return Decision(
            should_release=True,
            next_version=version_tags[0].removeprefix("v"),
            release_type=release_type,
            resumed=True,
            previous_tag=baseline or "",
        )

    release_type = _release_type(baseline, merged_branches, commit_bodies)
    if force_version and force_version != "auto":
        release_type = force_version
    if release_type == "none":
        return Decision(should_release=False)
    previous = baseline.removeprefix("v") if baseline else "0.0.0"
    return Decision(
        should_release=True,
        next_version=_bump(previous, release_type),
        release_type=release_type,
        previous_tag=baseline or "",
    )


def _release_type(baseline: str | None, merged_branches: str, commit_bodies: str) -> str:
    release_type = _type_from_branches(merged_branches)
    if release_type == "none":
        release_type = _type_from_commits(commit_bodies)
    return release_type


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True)


def _release_exists(tag: str, repo: str) -> bool:
    command = ["gh", "release", "view", tag, "--json", "tagName"]
    if repo:
        command += ["--repo", repo]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise RuntimeError(
            "the gh CLI is required to tell a published release from a tag"
        ) from error
    return result.returncode == 0


def format_outputs(decision: Decision) -> dict[str, str]:
    """The GITHUB_OUTPUT lines the workflow's steps read."""
    outputs = {"should_release": str(decision.should_release).lower()}
    if decision.should_release:
        outputs["next_version"] = decision.next_version
        outputs["release_type"] = decision.release_type
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-version", default=None)
    parser.add_argument("--inference-only", action="store_true")
    args = parser.parse_args()

    if args.inference_only:
        sha = os.environ.get("GITHUB_SHA", "")
        for key, value in {
            "should_release": "false",
            "next_version": f"0+g{sha}",
        }.items():
            print(f"{key}={value}")
        return 0

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    try:
        _git("fetch", "--tags", "--force")
    except (subprocess.CalledProcessError, OSError):
        # A fresh or remote-less checkout still has its local tags to reason about.
        pass
    tags = [
        tag for tag in _git("tag", "--list", "v*", "--sort=-version:refname").splitlines() if tag
    ]
    baseline = published_baseline(tags, lambda tag: _release_exists(tag, repo))

    # Type and evidence come from the commits since the published baseline; a
    # stranded tag on HEAD must not shrink that range to nothing.
    if baseline:
        merged_branches = _git("log", f"{baseline}..HEAD", "--merges", "--pretty=format:%s")
        commit_bodies = _git("log", f"{baseline}..HEAD", "--pretty=format:%B")
    else:
        merged_branches = _git("log", "--merges", "--pretty=format:%s")
        commit_bodies = _git("log", "--pretty=format:%B")

    decision = decide(
        tags=tags,
        release_exists=lambda tag: _release_exists(tag, repo),
        merged_branches=merged_branches,
        commit_bodies=commit_bodies,
        force_version=args.force_version,
    )

    if decision.resumed:
        print(f"Resuming interrupted release: tag v{decision.next_version} has no GitHub Release")
    outputs = format_outputs(decision)
    destination = os.environ.get("GITHUB_OUTPUT")
    if destination:
        with open(destination, "a", encoding="utf-8") as stream:
            for key, value in outputs.items():
                stream.write(f"{key}={value}\n")
    else:
        for key, value in outputs.items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
