---
sidebar_position: 2
title: pip
---

# Install with pip

Works fine, just slower than uv. Use a virtual environment: don't install into your system Python.

## From PyPI

```bash
pip install immich-memories
```

## From Source

```bash
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
pip install -e .
```

## Extras

```bash
# Face recognition
pip install immich-memories[face]

# macOS Apple Vision framework
pip install immich-memories[mac]

# Audio metadata support
pip install immich-memories[audio]

# ML audio analysis
pip install immich-memories[audio-ml]

# GPU-accelerated rendering
pip install immich-memories[gpu]

# Everything (cross-platform)
pip install immich-memories[all]

# Everything on macOS
pip install immich-memories[all-mac]
```

## Verify

```bash
immich-memories --help
```
