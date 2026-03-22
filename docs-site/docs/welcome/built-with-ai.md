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

AI-generated code without guardrails is fast garbage. The project has 17 CI checks: lint, format, type checking, cyclomatic complexity limits, cognitive complexity limits, file length caps (800 lines max), dead code detection, security scanning, architectural boundary enforcement, dependency hygiene, and 1900+ tests.

These aren't decoration. They catch real bugs that Claude introduces confidently. The complexity gate alone has blocked dozens of over-engineered functions. The file length cap forced a composition-based architecture (every class under 800 lines, zero mixins) that turned out to be the right call anyway.

## What the velocity actually looks like

The project went from "I want to make a birthday video" to a shipped product with 64 merged PRs in about 84 days. That's nearly one PR per day, each one passing 17 CI gates and adding to a test suite that's now at 1,900+ tests.

A typical feature cycle: I decide on Tuesday morning that trip memories need animated satellite maps. I spend a few hours researching map rendering approaches with Claude.ai (tile providers, zoom interpolation, Van Wijk smooth zoom for long distances vs. linear pan for short hops). By Wednesday I've picked the approach. Claude Code implements it. Thursday it's in the pipeline with tests, passing CI, ready for review.

That cycle used to take me 2-3 weeks when I wrote code myself (I'm a platform/infra person, not a frontend or video processing specialist). The AI doesn't remove the research or the decisions. It removes the "now I have to learn how FFmpeg compositing works well enough to write 400 lines of filter graph code" part.

The hard problems still take time. The video assembly pipeline took 9 attempts over 2 months. Audio ducking needed 3 research rounds on stem separation. But the ratio of "thinking about the problem" to "typing code" shifted from maybe 30/70 to 80/20, which is where it should have been all along.

I'm planning a series of blog posts about the development process: the research conversations, the debugging sessions, and the architectural decisions. Links will go here when they're published.
