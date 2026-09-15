---
sidebar_label: "Built with AI"
sidebar_position: 4
---

# Built with AI

The entire codebase was written by Claude (Anthropic). This was a deliberate choice, and the process turned out to be more interesting than the output.

## The loop

I don't write code. I make decisions, test results, and debug problems. Claude writes the code. The cycle for every feature looks like this:

1. I research the problem in conversation, several rounds before any code
2. I pick the approach
3. Claude implements it
4. I test it, it doesn't work
5. Back to research, then implementation, repeat

The music pipeline went through rounds of that before a single line of code. Video assembly went through more of them, each attempt failing differently, before I worked out that encoder non-determinism across separate FFmpeg invocations was the root cause (by putting individual frames side by side and noticing pixel differences). The assembler is a single streaming pass today because of it.

## The quality infrastructure

AI-generated code without guardrails is fast garbage. Every PR passes 21 gates before a test runs: 16 static checks (lint, format, type checking, cyclomatic and cognitive complexity limits, the 800-line file cap, dead code, duplication, modernization, architectural boundaries, dependency hygiene, CLI and config reference drift, the compose file parsing alone, conventional commits, an AI-smell audit) and 5 security scans (Bandit, Semgrep, pip-audit, Gitleaks, Hadolint). Count them in `.github/workflows/ci.yml`. Then the unit suite runs across four Python and OS combinations on a pull request and six on `main`, followed by a package build, two Docker builds (amd64 and arm64), a docs build and a hermetic launch check. The integration and E2E suites run locally and on the GPU runner. `uv run pytest tests/ --collect-only -q` prints the current split; no count is written down here, because it moves every week.

The gates live in the `Makefile` and the build stays red until they pass ([DISCLAIMER.md](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/DISCLAIMER.md) lists them one by one), so none of this needs taking on trust. They catch real bugs that Claude introduces confidently: the complexity gate alone has blocked dozens of over-engineered functions, and the file length cap forced a composition-based architecture (every class under 800 lines, zero mixins) that turned out to be the right call anyway.

## The pace, as the repository records it

First public commit 6 March 2026. 465 pull requests merged to `main` by 8 September, each one
through the gates above. `git log origin/main` counts them if you want to check.
