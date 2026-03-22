---
sidebar_position: 4
title: Duplicate Detection
---

# Duplicate Detection

If you imported the same video twice, or have near-identical clips from burst mode, you don't want both in your memory video. Duplicate detection catches these before they waste slots.

## Perceptual hashing

Each video gets a perceptual hash: a fingerprint based on what the video *looks like*, not its file contents. Two videos with different codecs, resolutions, or compression levels but the same visual content will produce similar hashes.

The hamming distance between hashes determines similarity. A distance of 0 means identical. Higher values mean more different.

## Configuration

```yaml
analysis:
  duplicate_hash_threshold: 8  # default
```

The threshold controls how aggressively duplicates are matched:

- **Lower values** (e.g., 4): only catches near-exact duplicates
- **Default (8)**: good balance, catches re-encodes and slight crops
- **Higher values** (e.g., 12): more aggressive, might false-positive on similar-but-different clips

## How duplicates are resolved

When a group of duplicates is found, they're ranked by quality (resolution, bitrate) and only the best version is kept for selection. The others are silently dropped: they still exist in your library, they just won't appear in the generated video.
