#!/usr/bin/env python3
"""Fail when the docs or the README drift back into chatbot English.

The owner's writing rules are short: no em dashes (a colon does the job), none
of the words that mark machine-written prose, no "not X, it's Y" construction.
Every sentence that breaks one of them gets rewritten by hand; this gate only
finds them. A line that must keep a flagged word (a quoted error message, a
product name) carries an HTML comment on the same line or the line above:

    <!-- voice: allow -->

Usage:
    python scripts/docs_voice_gate.py            # README.md + docs-site/docs
    python scripts/docs_voice_gate.py PATH ...   # specific files or directories
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DEFAULT_TARGETS = ("README.md", "docs-site/docs")
ALLOW_MARKER = "<!-- voice: allow -->"
EXTENSIONS = {".md", ".mdx"}

BANNED_WORDS = (
    # adjectives
    "actionable",
    "bespoke",
    "bustling",
    "captivating",
    "commendable",
    "cutting-edge",
    "daunting",
    "ever-evolving",
    "game-changing",
    "groundbreaking",
    "holistic",
    "impactful",
    "innovative",
    "insightful",
    "intricate",
    "invaluable",
    "meticulous",
    "multifaceted",
    "noteworthy",
    "nuanced",
    "paramount",
    "pivotal",
    "profound",
    "revolutionary",
    "seamless",
    "state-of-the-art",
    "tailored",
    "thought-provoking",
    "transformative",
    "unparalleled",
    "unwavering",
    "vibrant",
    "whimsical",
    # nouns
    "beacon",
    "enigma",
    "interplay",
    "intricacies",
    "kaleidoscope",
    "linchpin",
    "paradigm",
    "plethora",
    "synergy",
    "tapestry",
    "testament",
    "touchpoint",
    "treasure trove",
    "value proposition",
    # verbs
    "delve",
    "delving",
    "delves",
    "bolster",
    "elevate",
    "embark",
    "empower",
    "encompass",
    "facilitate",
    "foster",
    "harness",
    "leverage",
    "pioneer",
    "resonate",
    "showcase",
    "spearhead",
    "supercharge",
    "transcend",
    "unleash",
    "unlock",
    "unpack",
    "unravel",
    "unveil",
    "utilize",
    "utilise",
    # adverbs
    "furthermore",
    "holistically",
    "meticulously",
    "moreover",
    "seamlessly",
    "undeniably",
    "undoubtedly",
)

BANNED_PHRASES = (
    "in today's",
    "in the ever-evolving",
    "in a world where",
    "as technology continues",
    "it's important to note",
    "it is important to note",
    "it's worth noting",
    "it is worth noting",
    "it should be noted",
    "cannot be overstated",
    "navigate the complexities",
    "embark on a journey",
    "unlock the secrets",
    "unlock the potential",
    "unleash the power",
    "harness the power",
    "pave the way",
    "revolutioniz",
    "foster a culture",
    "whether you're a beginner",
    "a testament to",
    "a rich tapestry",
    "a treasure trove",
    "a plethora of",
    "a unique blend",
    "great question",
    "i hope this helps",
    "feel free to reach out",
    "don't hesitate",
    "picture this",
    "buckle up",
    "here's the kicker",
    "but here's the thing",
    "that being said",
    "in light of this",
    "in essence",
)

# "It's not about X, it's about Y" and "Not X. But Y." are the same tic.
NOT_X_BUT_Y = re.compile(
    r"\b(?:it'?s |this is )not (?:about |just |only )?[^.,;]{1,60},\s*(?:it'?s|it is|rather)\b",
    re.I,
)
EM_DASH = "—"

_word_patterns = {w: re.compile(rf"(?<![\w-]){re.escape(w)}(?![\w-])", re.I) for w in BANNED_WORDS}


def _targets(args: list[str]) -> list[Path]:
    roots = [Path(a) for a in args] if args else [Path(t) for t in DEFAULT_TARGETS]
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(p for p in sorted(root.rglob("*")) if p.suffix in EXTENSIONS)
        elif root.is_file():
            files.append(root)
    return files


def _strip_code(lines: list[str]) -> list[str]:
    """Blank out fenced code blocks and inline code: commands and error text are not prose."""
    out: list[str] = []
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else re.sub(r"`[^`]*`", "", line))
    return out


def scan_text(text: str) -> list[tuple[int, str]]:
    """Return (line number, what) for every hit; allow-marked lines are skipped."""
    raw = text.splitlines()
    prose = _strip_code(raw)
    hits: list[tuple[int, str]] = []
    for index, line in enumerate(prose):
        allowed = ALLOW_MARKER in raw[index] or (index > 0 and ALLOW_MARKER in raw[index - 1])
        if allowed or not line.strip():
            continue
        lowered = line.lower()
        if EM_DASH in line:
            hits.append((index + 1, "em dash"))
        for word, pattern in _word_patterns.items():
            if pattern.search(line):
                hits.append((index + 1, f"banned word: {word}"))
        for phrase in BANNED_PHRASES:
            if phrase in lowered:
                hits.append((index + 1, f"banned phrase: {phrase}"))
        if NOT_X_BUT_Y.search(line):
            hits.append((index + 1, "not X, it's Y"))
    return hits


def main(argv: list[str]) -> int:
    total = 0
    for path in _targets(argv):
        for line_no, what in scan_text(path.read_text(encoding="utf-8")):
            print(f"{path}:{line_no}: {what}")
            total += 1
    if total:
        print(
            f"docs-voice: {total} hit(s). Rewrite the sentence, or mark a quoted term with {ALLOW_MARKER}."
        )
        return 1
    print("docs-voice: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
