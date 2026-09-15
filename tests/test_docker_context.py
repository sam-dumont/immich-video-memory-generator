"""The Docker build context stays small enough to send to a daemon."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

CONTEXT_CEILING_BYTES = 50 * 1024 * 1024
REPO_ROOT = Path(__file__).resolve().parents[1]


def _patterns() -> list[str]:
    lines = (REPO_ROOT / ".dockerignore").read_text().splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _ignored(relative: Path, patterns: list[str]) -> bool:
    text = relative.as_posix()
    return any(
        fnmatch(text, pattern) or fnmatch(relative.name, pattern.removeprefix("**/"))
        for pattern in patterns
    )


def _context_bytes(patterns: list[str]) -> int:
    total = 0
    stack = [REPO_ROOT]
    while stack:
        for entry in stack.pop().iterdir():
            if _ignored(entry.relative_to(REPO_ROOT), patterns):
                continue
            if entry.is_symlink():
                continue
            if entry.is_dir():
                stack.append(entry)
            elif entry.is_file():
                total += entry.stat().st_size
    return total


def test_the_context_excludes_every_tree_the_image_never_uses() -> None:
    assert {
        "tests",
        "docs-site",
        "output",
        "dist",
        ".venv",
        ".venv-acestep",
        ".worktrees",
        "*.private.*",
        ".git",
    } <= set(_patterns())


def test_the_context_stays_under_the_ceiling() -> None:
    assert _context_bytes(_patterns()) < CONTEXT_CEILING_BYTES
