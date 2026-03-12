---
sidebar_position: 2
title: "Step 1: Configuration"
---

# Step 1: Configuration

This is where you tell the tool what to work with.

![Step 1: Configuration](/img/screenshots/step1-config-connected.png)

## Immich Connection

Enter your Immich server URL and API key. If you've already set these in your `config.yaml` or environment variables, they'll be pre-filled.

## Person Selection

A dropdown populated from Immich's face recognition data. Pick the person you want the memory video to focus on. The tool will only pull videos that contain this person.

You can also skip this and generate from all videos in the time period.

![Person dropdown](/img/screenshots/step1-person-dropdown.png)

## Time Period

Three options:

- **Year**: All videos from a specific year (e.g., 2024).
- **Birthday**: A birthday-year range (e.g., Jul 21, 2024 to Jul 20, 2025). Good for birthday party compilations.
- **Date range**: Any custom start and end date.

## Analysis Cache

Optional but worth enabling for large libraries. Previously analyzed clips get loaded from cache on subsequent runs, so you don't re-analyze hundreds of videos every time.

![Cache management panel](/img/screenshots/step1-cache-panel.png)
