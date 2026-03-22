---
sidebar_label: "Built with AI"
sidebar_position: 3
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

AI-generated code without guardrails is fast garbage. The project has 17 CI checks: lint, format, type checking, cyclomatic complexity limits, cognitive complexity limits, file length caps (800 lines max), dead code detection, security scanning, architectural boundary enforcement, dependency hygiene, and 1900+ tests.

These aren't decoration. They catch real bugs that Claude introduces confidently. The complexity gate alone has blocked dozens of over-engineered functions. The file length cap forced a composition-based architecture (every class under 800 lines, zero mixins) that turned out to be the right call anyway.

## The numbers

- 1900+ tests across unit and integration tiers
- 17 CI gates, all enforced on every commit
- 80+ source files, all under 800 lines
- Composition over inheritance everywhere: zero mixins
- Full transcripts of every research conversation available in the [dev history archive](https://github.com/sam-dumont/immich-video-memory-generator)
