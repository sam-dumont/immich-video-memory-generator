---
sidebar_label: "Built with AI"
sidebar_position: 4
---

# Built with AI

This project started as an experiment in building software with Claude: I chose the behaviour,
reviewed the results and used AI to write the code. It still uses AI assistance.

That has produced useful features and confident mistakes. Tests, type checks, security scans
and review catch some of them. They do not prove that a cut is good, that a deployment works on
your hardware, or that every failure has been covered.

The checks are in the repository's [Makefile](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/Makefile)
and [CI workflow](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/.github/workflows/ci.yml).
See [Testing](../contribute/testing.md) to run them and
[DISCLAIMER.md](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/DISCLAIMER.md)
for the project's development background.

The app is beta. Start with a small period, review the output, and report failures with the
command, version and relevant logs, after removing private information.
