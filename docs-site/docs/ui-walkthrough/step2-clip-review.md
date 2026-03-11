---
sidebar_position: 3
title: "Step 2: Clip Review"
---

# Step 2: Clip Review

Once you hit "Analyze" (or if analysis runs automatically), this step shows everything the pipeline found.

![Step 2: Clip Review](/img/screenshots/step2-clip-review.png)

## Clip Grid

You get a grid of thumbnails for every detected scene. Each clip shows:

- A representative frame
- Duration
- Score (how "interesting" the scene is, based on motion, faces, audio content)

## Duplicate Detection

Clips flagged as near-duplicates are grouped and highlighted. This happens a lot if you have burst recordings or multiple takes of the same moment. The tool uses perceptual hashing to catch these — it's not pixel-perfect comparison, it's "does this look basically the same."

## Selecting and Deselecting

Click any clip to view its details. Deselect clips you don't want in the final video. The tool pre-selects the best clips to fit your target duration, but you have full control.

![Refine Moments view](/img/screenshots/step2-refine-moments.png)

## Resume Support

If you've previously analyzed these videos (and have caching enabled), the cached results load instantly. You don't re-download or re-analyze anything. This makes iterating on clip selection fast even with large libraries.

