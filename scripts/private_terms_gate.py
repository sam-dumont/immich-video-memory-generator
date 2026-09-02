#!/usr/bin/env python3
"""Mechanically block owner-defined private terms from entering diffs, commit
messages, or PR text.

The denylist (family names, birth dates, fine GPS, etc.) lives OUTSIDE this
repo -- a local file, an env var, or a CI secret -- and this script never
echoes it back: every reported hit is masked to its first character. Most
contributors have no denylist configured at all, so that is not a failure --
the gate prints a notice and exits clean. Only the owner's own automation
passes --require-terms to turn "nothing configured" into a hard error.

Usage:
    python scripts/private_terms_gate.py --staged
    python scripts/private_terms_gate.py --range origin/main..HEAD
    python scripts/private_terms_gate.py --commit-msg .git/COMMIT_EDITMSG
    python scripts/private_terms_gate.py --text-file - < pr-body.txt
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

DEFAULT_TERMS_ENV_VAR = "IMMICH_MEMORIES_PRIVATE_TERMS"
DEFAULT_TERMS_PATH = Path("~/.config/immich-memories/private-terms.txt")

EXIT_OK = 0
EXIT_HITS_FOUND = 1
EXIT_MISCONFIGURED = 2

# A commit message read from a file (not from `git log`) has no sha to label
# it with -- it is always the sole message being scanned, so index 0.
_UNCOMMITTED_MESSAGE_LABEL = "commit message 0"

_COMMIT_RECORD_SEP = "\x1e"
_COMMIT_FIELD_SEP = "\x1f"


@dataclass(frozen=True)
class Term:
    """One compiled denylist entry. `index` is its 1-based position in the file."""

    index: int
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Hit:
    """A masked match, plus where it was found. `location` is filled in by the caller."""

    term_index: int
    masked: str
    location: str = ""


def mask(term_or_match: str) -> str:
    """First character plus `***` -- the reported form never contains the term itself."""
    return f"{term_or_match[0]}***" if term_or_match else "***"


def _compile_term(index: int, raw_line: str) -> Term:
    if raw_line.startswith("re:"):
        pattern = re.compile(raw_line[len("re:") :], re.IGNORECASE)
    else:
        pattern = re.compile(rf"(?<!\w){re.escape(raw_line)}(?!\w)", re.IGNORECASE)
    return Term(index=index, pattern=pattern)


def _parse_terms(content: str) -> list[Term]:
    terms = []
    index = 1
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        terms.append(_compile_term(index, stripped))
        index += 1
    return terms


def load_terms(terms_file: str | None = None, terms_env: str | None = None) -> list[Term]:
    """Resolve the denylist through the priority chain and parse it.

    Priority: --terms-file > --terms-env > $IMMICH_MEMORIES_PRIVATE_TERMS path >
    the default config path. An explicit --terms-file that can't be read raises;
    every other tier resolving to nothing just means "no denylist here" -- the
    caller decides whether that is fatal (--require-terms) or a clean skip.
    """
    if terms_file:
        return _parse_terms(Path(terms_file).read_text(encoding="utf-8"))
    if terms_env:
        value = os.environ.get(terms_env, "")
        return _parse_terms(value) if value.strip() else []
    configured_path = os.environ.get(DEFAULT_TERMS_ENV_VAR, "")
    path = Path(configured_path) if configured_path else DEFAULT_TERMS_PATH.expanduser()
    return _parse_terms(path.read_text(encoding="utf-8")) if path.is_file() else []


def scan_text(text: str, terms: Sequence[Term]) -> list[Hit]:
    """Every match of every term in `text`, masked. Caller fills in `location`."""
    return [
        Hit(term_index=term.index, masked=mask(match.group(0)))
        for term in terms
        for match in term.pattern.finditer(text)
    ]


def added_lines(diff_text: str) -> Iterator[tuple[str, str]]:
    """Yield (path, line) for each `+` line of a unified diff, skipping `+++` headers."""
    current_path = "?"
    for raw_line in diff_text.splitlines():
        if raw_line.startswith("+++ "):
            current_path = _diff_target_path(raw_line)
        elif raw_line.startswith("+") and not raw_line.startswith("+++ "):
            yield current_path, raw_line[1:]


def _diff_target_path(header: str) -> str:
    target = header[len("+++ ") :].strip()
    return target[2:] if target.startswith("b/") else target


def _located(hits: Iterable[Hit], location: str) -> list[Hit]:
    return [replace(hit, location=location) for hit in hits]


def scan_diff(diff_text: str, terms: Sequence[Term]) -> list[Hit]:
    """Every hit on an added line of a unified diff, each located to its file."""
    hits = []
    for path, line in added_lines(diff_text):
        hits.extend(_located(scan_text(line, terms), f"{path} (added line)"))
    return hits


def _run_git(args: list[str]) -> str:
    # Fixed argv, no shell -- CI passes a caller-controlled --range value straight
    # through as one argument, never through a shell where it could be injected.
    # GIT_* vars are stripped: this script's own pre-commit hook runs it from
    # inside a `git commit`, which sets GIT_DIR/GIT_WORK_TREE for that commit's
    # repo. Inheriting them would let a nested git call be redirected there
    # instead of discovering the repo from the current working directory.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result = subprocess.run(["git", *args], check=True, capture_output=True, text=True, env=env)
    return result.stdout


def _commit_messages_in_range(range_spec: str) -> list[tuple[str, str]]:
    """(short_sha, body) for every commit in A..B, using ASCII separators a message can't contain."""
    output = _run_git(["log", f"--format=%h{_COMMIT_FIELD_SEP}%B{_COMMIT_RECORD_SEP}", range_spec])
    pairs = []
    for record in output.split(_COMMIT_RECORD_SEP):
        if not record.strip("\n"):
            continue
        sha, _, body = record.partition(_COMMIT_FIELD_SEP)
        pairs.append((sha, body))
    return pairs


def _scan_staged(terms: Sequence[Term]) -> list[Hit]:
    diff_text = _run_git(["diff", "--cached", "-U0", "--no-color"])
    return scan_diff(diff_text, terms)


def _scan_range(range_spec: str, terms: Sequence[Term]) -> list[Hit]:
    diff_text = _run_git(["diff", range_spec, "-U0", "--no-color"])
    hits = scan_diff(diff_text, terms)
    for sha, body in _commit_messages_in_range(range_spec):
        hits.extend(_located(scan_text(body, terms), f"commit message {sha}"))
    return hits


def _scan_commit_msg_file(path_str: str, terms: Sequence[Term]) -> list[Hit]:
    text = Path(path_str).read_text(encoding="utf-8")
    return _located(scan_text(text, terms), _UNCOMMITTED_MESSAGE_LABEL)


def _scan_text_file(path_str: str, terms: Sequence[Term]) -> list[Hit]:
    if path_str == "-":
        return _located(scan_text(sys.stdin.read(), terms), "stdin")
    text = Path(path_str).read_text(encoding="utf-8")
    return _located(scan_text(text, terms), path_str)


def _format_hit(hit: Hit) -> str:
    return f"private-terms: term#{hit.term_index} ({hit.masked}) found in {hit.location}"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="scan `git diff --cached -U0`")
    parser.add_argument(
        "--range", metavar="A..B", help="scan the diff and every commit message between two refs"
    )
    parser.add_argument("--commit-msg", metavar="PATH", help="scan a commit message file")
    parser.add_argument("--text-file", metavar="PATH", help="scan a text file ('-' for stdin)")
    parser.add_argument("--terms-file", metavar="PATH", help="denylist file (highest priority)")
    parser.add_argument(
        "--terms-env", metavar="NAME", help="env var holding newline-separated denylist terms"
    )
    parser.add_argument(
        "--require-terms",
        action="store_true",
        help="exit 2 instead of skipping when no denylist can be found",
    )
    return parser.parse_args(argv)


def _collect_hits(args: argparse.Namespace, terms: Sequence[Term]) -> list[Hit]:
    hits: list[Hit] = []
    if args.staged:
        hits.extend(_scan_staged(terms))
    if args.range:
        hits.extend(_scan_range(args.range, terms))
    if args.commit_msg:
        hits.extend(_scan_commit_msg_file(args.commit_msg, terms))
    if args.text_file:
        hits.extend(_scan_text_file(args.text_file, terms))
    return hits


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        terms = load_terms(terms_file=args.terms_file, terms_env=args.terms_env)
    except OSError as exc:
        print(f"private-terms: could not read --terms-file: {exc}")
        return EXIT_MISCONFIGURED

    if not terms:
        print("private-terms: no denylist found, skipping")
        return EXIT_MISCONFIGURED if args.require_terms else EXIT_OK

    hits = _collect_hits(args, terms)
    for hit in hits:
        print(_format_hit(hit))
    print(f"private-terms: {len(hits)} hit(s) found")
    return EXIT_HITS_FOUND if hits else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
