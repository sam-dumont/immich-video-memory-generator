---
title: Teach it your family
---

# Teach it your family

Reader: newcomer.

Two steps, about ten minutes, once. The no-model editor cuts from dates, places, favourites and
people. It can read the dates and the favourites off Immich. It can't guess where home is, or which
of the forty named faces in your library are your partner and your kids. These two steps tell it,
and they make the biggest difference to a no-model cut.

## 1. Where home is

Every day in a film is measured against your home base:

| Where the day was | What the editor does with it |
|---|---|
| Within 10 km of home | At home: the days are grouped by calendar week, one story per week |
| Further than that | Away: the whole stretch of days stays one story however long it lasts, and it counts as an occasion even with no favourite in it |

A stretch 50 km or more from home that lasts two days or more is also detected as a trip, named
after where it was.

Without a home base no day is away from home, so no story is a trip. A three-week holiday arrives
as three weekly stories competing with the weeks around it, instead of one trip that owns its part
of the film.

On Docker, set it in `.env` (decimal degrees; right-click your house in any map app to copy them):

```bash
IMMICH_MEMORIES_TRIPS__HOMEBASE_LATITUDE=50.8503
IMMICH_MEMORIES_TRIPS__HOMEBASE_LONGITUDE=4.3517
```

then `docker compose up -d` to recreate the container. On pip, in `~/.immich-memories/config.yaml`:

```yaml
trips:
  homebase_latitude: 50.8503
  homebase_longitude: 4.3517
```

`immich-memories preflight` says `Home coordinates configured` once it is set. The coordinates stay
in your config; nothing is looked up online unless you turn on `network.geocoding`.

## 2. Who's who

The people the editor knows come from Immich's face recognition, so name the faces that matter in
Immich first. Then, in this app, open **Settings > People**:

1. Press **Rescan the library**. It reads each named person's picture count and month curve from
   Immich (never a pixel) and writes `~/.immich-memories/people.yaml`. The roster lists the inner
   circle first.
2. On the cards of your partner, your children and your parents, set **Role** to `partner`,
   `child` or `parent`. It saves the moment you pick it.
3. Under **Relationships**, confirm the links the scan proposed (the check) or reject them (the
   cross). **Add relationship** records one it missed.

Only what you confirm counts. The scan's own guesses (its tiers and proposed links) never make
someone close family, and a later rescan never touches what you confirmed.

### What it changes in a cut

"Close family" here is a partner or spouse, a child, or a parent. Siblings and grandparents are
family, and they don't trigger these rules.

| Rule | What happens |
|---|---|
| **The family seat** | A close family member on at least 20 of the period's pictures (or 5 % of them) who ended up in none of its shots gets one: their best frame, in the story that holds most of their pictures |
| **Big stories** | A story on days far busier than usual (twice the median photographed day) where at least 30 % of the pictures show close family is weighed as a major story, even without three favourites |
| **Quiet weeks** | A week at home with close family in it is not a quiet week, so it can get a shot |
| **With a model** (optional) | The polish never drops a close relative's only shot |

A person's film widens the circle: in a film about your partner, their parents count as close
family too.

The numbers are keys under `advanced.editorial.people` (`seat_min_pictures`, `seat_min_share`,
`big_story_density`, `big_story_family_share`), in the
[config reference](../reference/config-reference.md).

### From the CLI

```bash
im people scan                    # the same as Rescan the library
im people scan --owner "Alex"     # when the scan picked the wrong library owner
im people show                    # what the file says now
```

(`im` is the `docker compose exec immich-memories immich-memories` alias from
[Your first film](./first-film.mdx#the-same-on-the-cli).) The file is plain YAML and meant to be
edited: everything under `confirmed:` is yours. The page itself is described on
[The web UI](../make/web-ui.mdx#people-confirming-whos-who).

## Then cut again

Both steps apply to the next cut. The home base and the roles are read at the start of every cut,
and the banked picture facts don't depend on them, so nothing is read again. Cut the same month
again and compare.
