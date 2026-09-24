---
title: FAQ
---

# FAQ

Reader: newcomer. The questions self-hosters ask before and after the first film. For a run that stopped, see
[Troubleshooting](./troubleshooting.md).

## Before you install

**Does it change my Immich library?**

It reads. The one write is the finished film, and only when you ask for it (`--upload-to-immich`, or the upload
switch in the web UI): a new asset, tagged `immich-memories/generated`, optionally in an album. A re-render of
the same film into the same album moves the copy it replaces to Immich's trash, never a hard delete. It writes
nothing else.

**What leaves my network?**

On a default install, nothing: the app talks to your Immich server and that's it. Readers, caption servers,
place names in your language and the map fly-over are all opt-in, each one listed on
[Privacy](../run/privacy.md) with what it sends.

**Will it run on my NAS?**

Yes, it works on a plain NAS: the default install cuts films on a NAS CPU from dates, places, favourites,
people and what small local classifiers measure on each picture. A GPU or a model makes it better and faster
([What a model adds](../better/overview.md)). Sizes and the one Synology trap are on
[On a NAS](../run/nas.md) and [Requirements](../run/requirements.md).

**Do I need face recognition?**

No. Without a person, a period covers everyone. Named faces make people films possible and let it keep close
family in the film ([Teach it your family](../get-started/who-is-who.md)).

**iPhone only?**

Anything FFmpeg decodes. Live Photos are tested on iPhones; Samsung and Pixel motion photos are untested.

## After the first film

**Why is this picture in (or not)?**

`immich-memories runs why <asset id>` says where it passed or was dropped and why. The rules are on
[How it chooses](../how-it-chooses/overview.md).

**Can I pick pictures myself?**

Yes. Tick or untick on the web UI's pool and cut again, star it in Immich, or pass `--include` / `--exclude`.
A tick outranks the editor. See [Overrule it](../how-it-chooses/overrule-it.md).

**Why is the first cut slow and the second fast?**

The first cut measures each picture it can reach once and banks the result; the second is mostly the render.
`prepare` does a period ahead of time, overnight if you like.

**How much disk?**

Caches are capped by config: 10 GB of downloaded video (kept 7 days), 10 GB of Immich previews, plus the
annotation store. Films come on top, sized by length, resolution and codec.

**Can it make films on its own?**

Yes, one a day at most: [Automate it](../make/automate.md).

**Several people on one Immich server?**

The web UI is single-user, single-replica: one Immich API key, one library. Run one instance per library.

**Is it stable?**

Install, read-only Immich access and rendering are. Selection keeps improving, measured on real libraries, so
review a cut before you show it at a family party. The project is built with AI on purpose, as an experiment in
keeping a complex codebase clean that way: [Why this exists](../welcome/about.mdx).
