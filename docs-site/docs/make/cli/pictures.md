---
sidebar_position: 6
title: pictures
---

# pictures

Reader: power user.

Your own word on one picture: clear what holds it, or never use it. It's the terminal side of the
**Clear hold** and **Never use** buttons in the media pool and on the storyboard, and writes the same
place, so either one sees what the other did. Every tier reads it in every later cut. Every flag is in
the [CLI reference](../../reference/cli-reference.md#pictures).

The asset id is the one `runs why`, `runs story` and Immich show.

## pictures show

```bash
immich-memories pictures show 3f1c9a2e-...
```

```text
Held: a nudity detector flagged it.
```

What holds the picture (a detector on it or its Live clip, a hold an earlier cut banked) and what you
decided, if anything.

## pictures clear-hold

```bash
immich-memories pictures clear-hold 3f1c9a2e-...
immich-memories pictures clear-hold 3f1c9a2e-... --yes
```

Says what holds it and asks before clearing. A cleared picture is `share` for the family-viewing gate,
with nothing asked, and no banked hold comes back. It refuses a picture nothing holds, and doesn't
lift a carrier rule (a screenshot stays a screenshot). One picture per call: there's no bulk clear.

## pictures never-use

```bash
immich-memories pictures never-use 3f1c9a2e-...
```

Out of every film from the next cut on. A tick or `--include` doesn't bring it back; `undo` does.

## pictures undo and list

```bash
immich-memories pictures undo 3f1c9a2e-...   # the app's own holds apply again
immich-memories pictures list                # every picture you cleared or ruled out
```

A Live Photo is one picture: a decision on its still covers its clip too. Why these rules:
[Family, audience and duplicates](../../how-it-chooses/family-audience-duplicates.md#your-word-on-a-picture).
