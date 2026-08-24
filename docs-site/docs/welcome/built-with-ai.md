---
sidebar_label: "Built with AI"
sidebar_position: 4
---

# Built with AI

The entire codebase was written by Claude (Anthropic). This was a deliberate choice, and the process turned out to be more interesting than the output.

## How it works in practice

I don't write code. I make decisions, test results, and debug problems. Claude writes the code. The cycle for every feature looks like this:

1. I research the problem in conversation (3-6 rounds before any code)
2. I pick the approach
3. Claude implements it
4. I test it, it doesn't work
5. Back to research, then implementation, repeat

The music pipeline went through 6 research rounds before a single line of code. The video assembly pipeline took 9 attempts over 2 months, each one failing differently, before I figured out that encoder non-determinism across separate FFmpeg invocations was the root cause (by looking at individual frames side-by-side and noticing pixel differences).

## The quality infrastructure

AI-generated code without guardrails is fast garbage. Every PR passes 20 gates before a test runs: 15 static checks (lint, format, type checking, cyclomatic and cognitive complexity limits, the 800-line file cap, dead code, duplication, modernization, architectural boundaries, dependency hygiene, CLI and config reference drift, conventional commits, an AI-smell audit) and 5 security scans (Bandit, Semgrep, pip-audit, Gitleaks, Hadolint). Count them in `.github/workflows/ci.yml`. Then 5,000+ tests run across four Python and OS combinations, followed by a package build, two Docker builds, a docs build and a hermetic launch check.

These aren't decoration. They catch real bugs that Claude introduces confidently. The complexity gate alone has blocked dozens of over-engineered functions. The file length cap forced a composition-based architecture (every class under 800 lines, zero mixins) that turned out to be the right call anyway.

## What the velocity actually looks like

The project went from "I want to make a birthday video" at the end of December 2025 to a shipped v0.1.0 on 6 March 2026: about ten weeks. The repository has been public ever since, and 356 PRs have merged in the 171 days to 24 August 2026. Two a day, each one through the same 20 gates, each one adding to a test suite now past 5,000 tests.

A typical feature cycle: I decide on Tuesday morning that trip memories need animated satellite maps. I spend a few hours researching map rendering approaches with Claude.ai (tile providers, zoom interpolation, Van Wijk smooth zoom for long distances vs. linear pan for short hops). By Wednesday I've picked the approach. Claude Code implements it. Thursday it's in the pipeline with tests, passing CI, ready for review.

That cycle used to take me 2-3 weeks when I wrote code myself (I'm a platform/infra person, not a frontend or video processing specialist). The AI doesn't remove the research or the decisions. It removes the "now I have to learn how FFmpeg compositing works well enough to write 400 lines of filter graph code" part.

The hard problems still take time. The video assembly pipeline took 9 attempts over 2 months. Audio ducking needed 3 research rounds on stem separation. But the ratio of "thinking about the problem" to "typing code" shifted from maybe 30/70 to 80/20, which is where it should have been all along.

The research conversations, debugging sessions and architectural decisions behind all of this are being written up as a series of blog posts; the [GitHub repo](https://github.com/sam-dumont/immich-video-memory-generator) README will link them as they go out.
