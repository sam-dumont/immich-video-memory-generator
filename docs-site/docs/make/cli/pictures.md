---
sidebar_position: 6
title: pictures
---

import ThemedScreenshot from '@site/src/components/ThemedScreenshot';

# pictures

Reader: power user.

Your own word on one picture: clear what holds it, or never use it. It's the terminal side of the
**Clear hold** and **Never use** buttons in the media pool and on the storyboard, and writes the same
place, so either one sees what the other did. Every tier reads it in every later cut. Every flag is in
the [CLI reference](../../reference/cli-reference.md#pictures).

The asset id is the one `runs why`, `runs story` and Immich show.

```bash
$ immich-memories pictures show trip-swim-02
Held: a nudity detector flagged it.

$ immich-memories pictures clear-hold trip-swim-02
Held: a nudity detector flagged it.
Fine for which films (anyone, family, just-us) [family]: anyone
Once cleared, every film up to that level may use it, and nothing the app reads
later puts the hold back. `pictures undo` does.
Clear the hold on trip-swim-02? [y/N]: y
✓ Cleared for anyone: trip-swim-02 can play in the next cut.

$ immich-memories pictures never-use home-rain-window-01
✓ home-rain-window-01 won't be in any film from the next cut on.

$ immich-memories pictures list
home-rain-window-01  never use
trip-swim-02  hold cleared for anyone
```

The same picture in the media pool, after that `clear-hold`:

<ThemedScreenshot name="pictures-pool-cleared" alt="The pool card of the cleared picture: 'You cleared its hold for anyone (a nudity detector flagged it).'" />

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
immich-memories pictures clear-hold 3f1c9a2e-...                        # asks the level, then asks to clear
immich-memories pictures clear-hold 3f1c9a2e-... --level just-us --yes
```

Says what holds it, asks how far it may go (`--level anyone`, `family` or `just-us`; `family` when
you just press enter, or with `--yes` and no `--level`) and asks before clearing. The gate then gives
the picture that level's verdict (`share`, `family_only` or `just_us`) with nothing asked, and no
banked hold within that level comes back. It refuses a picture nothing holds, and doesn't
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
[Your word on a picture](../../how-it-chooses/overrule-it.md#your-word-on-a-picture).
