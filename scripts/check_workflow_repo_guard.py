#!/usr/bin/env python3
"""Fail when a workflow job could run in the private GPU mirror.

mirror.yml force-pushes main to a private repo that exists only to run
integration.yml on the self-hosted gpu runner. Every other workflow rides
along with that push, and GitHub bills private-repo minutes. So every job
outside integration.yml must carry the public-repo guard as a top-level `&&`
term of its `if:`, where no `||` can route around it.

Usage:
    python scripts/check_workflow_repo_guard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

WORKFLOWS = Path(".github/workflows")
GUARD = "github.repository == 'sam-dumont/immich-video-memory-generator'"
MIRROR_ONLY = {"integration.yml"}


def _and_terms(expression: str) -> list[str]:
    terms: list[str] = []
    depth = 0
    quoted = False
    start = 0
    for i, char in enumerate(expression):
        if char == "'":
            quoted = not quoted
        elif not quoted and char in "()":
            depth += 1 if char == "(" else -1
        elif not quoted and depth == 0 and expression.startswith("&&", i):
            terms.append(expression[start:i])
            start = i + 2
    terms.append(expression[start:])
    return [" ".join(term.split()) for term in terms]


def _is_guarded(condition: object) -> bool:
    if not isinstance(condition, str):
        return False
    expression = condition.strip()
    if expression.startswith("${{") and expression.endswith("}}"):
        expression = expression[3:-2]
    return GUARD in _and_terms(expression)


def unguarded_jobs(workflow_dir: Path) -> list[str]:
    """Return `file: job` for every job outside integration.yml that lacks the public-repo guard."""
    missing: list[str] = []
    for path in sorted([*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")]):
        if path.name in MIRROR_ONLY:
            continue
        jobs = (yaml.safe_load(path.read_text()) or {}).get("jobs") or {}
        missing.extend(
            f"{path.name}: {name}" for name, job in jobs.items() if not _is_guarded(job.get("if"))
        )
    return missing


def main() -> int:
    missing = unguarded_jobs(WORKFLOWS)
    if not missing:
        print("Every workflow job is guarded to the public repo")
        return 0
    print("These jobs would also run, and bill minutes, in the private GPU mirror:")
    for entry in missing:
        print(f"  {entry}")
    print(f"Add `{GUARD} && (...)` to each job's `if:`.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
