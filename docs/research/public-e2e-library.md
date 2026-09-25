# A public test library: households built once, restored for every run

Status: design approved 2026-09-25, household 1 in progress. Epic #1327, sub-issues #1328 to #1334.

## Why

Every tuning round so far was judged on one library: the owner's, one household at
one life stage. Rules drifted toward its misses (bathing, breastfeeding, newborn
scenes). The fix is a set of public libraries covering other kinds of lives, each
loaded into a real Immich once, with faces, places and people already worked out,
and restored in a couple of minutes for every run. The owner's library stays one
sanity cell. It is never the target, and nothing from it goes into this set.

## What exists today, and what it can't do

| Piece | What it gives | What it can't do |
| --- | --- | --- |
| `tests/e2e/fixtures/library` (138 CC0 stills, 31 MB) | one scripted June 2024, one household, trip detection pinned | one month; no faces (the faceless test is a rule); "videos" are pans over stills |
| Immich Gate (`make test-immich-gate`, #1223/#1268) | digest-pinned v2 and v3 server, Postgres, Valkey; seeding over HTTP; image-tar caching | no machine learning container; tmpfs only; people are hand-drawn face boxes; rebuilt from scratch on every run |
| `scripts/contact_sheet.py` | a sheet of what a cut keeps | reads a live run, not a stored report |
| `editorial_cut_invariants.py` + `immich-memories runs` | the finished-cut promises, recorded per attempt (`derived-decisions/cut-invariants.private.json`) | nothing aggregates them across films |

The new work reuses all four. The gate's compose file and pinned images are the base.
The fixture library's rules (sha256 per file, credits table, licence checked on the
file itself) carry over.

## Sources, measured

**CommonCatalog CC BY** (`common-canvas/commoncatalog-cc-by` on Hugging Face). This is
the CC BY slice of YFCC100M: 14,581,672 Flickr photos, 5,573 parquet files, 14.3 TB.
The dataset card says `license: cc-by-4.0` and each row carries its own `licensename`
(the shard I read was 100% "Attribution License"). Each row also has `uid` (the
photographer), `datetaken`, `latitude`/`longitude`, `capturedevice`, `usertags`,
`title`, raw `exif` and the Flickr `downloadurl`.

Probe (2026-09-25, 80 random shards, 157,324 rows, 33 s):

- Metadata is cheap. One shard holds 5.5 GB of JPEG and 12 MB of metadata, of which
  9 MB is raw EXIF. Reading the metadata columns only takes 2.5 s per shard, so the
  whole 14.6M-row index is about 30 min at 12 parallel reads and about 1.5 GB without
  EXIF. The image bytes average about 1 MB per picture.
- 54% of rows are geotagged.
- Capture years: 2007 to 2013 hold 95% of rows. The dates are shifted forward (below).
- Photographers extrapolated from the sample (scale ×92.7, so these are rough):

| Timeline shape | Photographers |
| --- | --- |
| ≥500 photos over ≥3 years | ~3,100 (1,435 mostly geotagged) |
| ≥1,500 photos over ≥3 years | ~1,470 (758 mostly geotagged) |
| ≥3,000 photos over ≥3 years | ~715 (419 mostly geotagged) |

Among the ≥1,500-photo photographers, with at least 300 photos tagged or titled
with the theme: dog 21, horse 9, wedding 46 (many are wedding pros, not households),
kids/family 42 to 45, cycling/running 46, travel 143, cat 9, 365/self-portrait 7.
So each theme has from a handful to a hundred-plus candidate timelines. That is enough to pick one
good household per theme by eye, and not enough to be careless.

**Wikimedia Commons video**. CommonCatalog has no video. Commons search hits for
`filetype:video` by licence (CC0 / CC BY 4.0 / CC BY 2.0): dog 12/42/30, horse
28/40/77, wedding 6/16/3, "family holiday" 24/69/9, cycling 56/651/211, beach
100/117/91. That's enough for 20 to 60 clips per household, but they come from other
people than the stills. A household's videos are theme-matched borrowings, stamped
into its episodes. They are not the same dog.

**Commons / Openverse stills (CC0, CC BY)**. These fill gaps: the young-family
household, clutter, and scenes a timeline lacks. The existing sourcing recipe is
`haswbstatement:P275=Q6938433` (CC0), and CC BY is Q20007257 (4.0) / Q19125117 (2.0).

**Licences allowed:** CC0 and CC BY only, as the corpus rulings of 09-23 require. No
BY-SA, NC, Pexels/Unsplash-licence or "free to use" terms. Each file's licence is read
from the row or the file page itself, never from a search filter alone.

**No Flickr API** (the owner declined a paid key). Image bytes come from the parquet
row group or the static `downloadurl` (no key needed). The licence is the one recorded
in the row. CC licences are irrevocable, and the manifest keeps a removal contact.

## The households

Each one spans 3 to 5 years, with scripted people, relations and films. The sizes are
targets. The first build sets the real numbers.

| # | Household | Main source | People with faces | Pictures / videos / Live pairs | Snapshot |
| --- | --- | --- | --- | --- | --- |
| 1 | Dog owner (couple, one dog through puppy to old age, walks, beach, one road trip a year) | one CommonCatalog timeline (≥300 dog photos) | 2 adults | ~2,000 / 30 / 15 | ~1.5 GB |
| 2 | Horse rider (stable, shows, a horse replaced mid-span) | one timeline (≥300 horse photos) | 1 to 2 adults | ~1,500 / 25 / 10 | ~1.2 GB |
| 3 | Young family (birth to age 4, birthdays, park, grandparents' visits) | a family CommonCatalog timeline, or CC0/CC BY stills with the June fixture's method | adults and children by role (`child-1`), clustered in the private snapshot; annotated identities as the fallback | ~2,500 / 60 / 30 | ~2 GB |
| 4 | Retired couple travelling (few home weeks, many trips abroad) | a travel-heavy, mostly geotagged timeline | 2 adults | ~2,500 / 30 / 10 | ~2 GB |
| 5 | Sports-heavy single (races, training, club trips) | a cycling/running timeline | 1 adult + club regulars | ~2,000 / 40 / 10 | ~1.6 GB |
| 6 | Big family (weddings, Christmas, reunions, many adults) | a family/wedding timeline | 6 to 12 adults, children by role | ~3,000 / 40 / 15 | ~2.4 GB |
| 7 | Light user (sparse weeks, screenshots, receipts, a few good days) | clutter layer + a small timeline | 1 adult | ~600 / 10 / 5 | ~0.4 GB |

Household 7 exists because "a week with no indicator goes short" and "a good film,
even from a thin month" need a thin library to be tested at all.

Per household, the repo holds `tests/public_e2e/households/<name>/`:

- `household.yaml`: the story (who, where home is, episodes), the source timelines,
  the date shift, the cast with seed pictures, relations, the films to run.
- `manifest.csv`: one row per shipped file with the key, source, source id and page,
  creator, licence, sha256 of the file as shipped, and which fields are scripted
  (date, GPS, camera) rather than facts about the photo.
- `CREDITS.md`, generated from the manifest, in the fixture library's format.
- `snapshot.lock`: artifact location, sha256 and size of each part, the Immich server
  and ML image digests, build date, the git sha the build ran from, and the API key
  secret of that throwaway local Immich.
- `golden/<film>.yaml`: the reviewed sheet (see Judging).

No pictures go in git. Pictures live in the snapshot, and anyone can rebuild them
from the manifest.

### Realism layers applied at build time

- **Date shift by whole years** (+12, owner decision). Christmas stays on the 25th and
  seasons stay put. The weekday changes, which nothing in the product reads.
- **Home snap.** The photographer's most frequent ~1 km cell is moved to its town
  centroid, so a published home is never pinpointed again. Trips keep their real GPS.
- **Clutter.** Flickr uploads are already the photographer's picks, and a camera roll
  is not. Each household gets a scripted clutter share (the target is 15 to 25%):
  screenshots rendered at build time, CC0 receipts/whiteboards/documents,
  near-duplicate bursts (crop plus small shifts of real frames), blurred and dark
  copies. Every clutter file is marked in the manifest, so "clutter in the cut" is
  countable.
- **Camera.** `capturedevice` becomes EXIF Make/Model (selection drops a still with no
  camera). Clutter gets a phone model.
- **Live Photo-like pairs.** A 2 to 3 s CC video clip, plus a still extracted at its
  key frame, both given the same Apple content identifier so Immich links them. A
  spike (sub-issue) first proves which tags Immich v3 reads for a JPEG/MOV pair written
  with exiftool. If Immich can't be made to link a non-Apple pair, the pair is marked
  "motion-only" and the Live Photo path keeps its own integration tests.
- **Resolution.** Long side ≤2048 px, JPEG q85, EXIF rewritten. Videos are H.264 1080p
  at ~6 Mbit/s, ≤20 s.

## Faces: what's realistic

Immich runs real face detection and clustering at build time. The question is whose
faces those are.

1. **Adults from one photographer's timeline: yes, this is the core.** A heavy
   photographer's partner, friends and the photographer themselves recur across years,
   in the messy way a real library does: side profiles, sunglasses, age. Immich
   clusters them and the build names the clusters from seed pictures chosen by eye.
   This is the test the product needs (merged and split clusters, strangers at a
   wedding), and the photographer chose to publish those pictures under CC BY.
2. **Children in CC BY timelines: allowed in the private snapshots only** (owner
   ruling, 2026-09-25). A family timeline's children recur and get clustered like
   anyone else, with guardrails:
   - snapshots are never published;
   - no picture of a child appears in anything public (docs, public reports, demo);
     a public report shows an adults-and-scenery sheet or none;
   - never used for training;
   - removal on request through the attribution manifest and its contact
     (sam@dropbars.be, the same as the Laya manifest);
   - children are named only by role (`child-1`, `child-2`), never by a real name.

   **Annotated identities** stay the fallback when a set has no usable recurring
   child: named people whose face boxes the build draws through `POST /faces`, the
   way the gate does.
3. **Synthetic identities: later, optional.** A generated family (an open image model
   with an identity adapter) would give recurring child faces with no real person
   behind them. That's research, not the first slice, and it needs the owner's call
   on uncanny-looking faces in a test set.
4. **Public figures across years:** not useful. Nobody's household is a politician.
5. **Pets:** Immich does not cluster animals. Recurring pets come free with the dog
   and horse timelines and are judged by the product's own animal reading.

**Where the snapshot lives.** Once Immich has clustered faces, the snapshot holds
face embeddings of real people, which is biometric data under the GDPR. So the
snapshot stays **private** (owner ruling, 2026-09-25): a release asset on the private
CI mirror (`sam-dumont/immich-memories-ci`) plus a copy on the maintainer machine and
the GPU runner's volume. The public repo gets the manifest, credits and build recipe,
so anyone can rebuild an equivalent snapshot. Reports that show pictures credit every
file they show, and stay local unless they are adults-and-scenery only.

## Build once

`make public-e2e-build HOUSEHOLD=dog-owner` (maintainer machine, not CI):

1. **Index** (once, shared with #1154): scan CommonCatalog metadata columns into
   a local parquet index (~30 min, ~1.5 GB).
2. **Pick**: candidate timelines per theme from the index (photo count, year span,
   geotag share, tag density, gaps). The maintainer picks one by eye on a sheet of
   one picture per episode, then marks the drop list (private scenes, anything a reviewer would not want in a film).
3. **Fetch**: the picked rows' JPEGs (~2 GB, minutes), then the Commons videos and
   CC0 fill. Each licence is checked on the file, and sha256 is recorded.
4. **Stamp**: date shift, home snap, EXIF, clutter, Live pairs, then the manifest and
   `CREDITS.md`.
5. **Load**: a pinned Immich **with** the ML container (server v3.2.2 and ML image
   pinned by digest) on docker volumes, not tmpfs. Upload, then wait for every queue:
   metadata, reverse geocoding (Immich's bundled GeoNames, no network), thumbnails,
   smart search, face detection, facial recognition. On a Mac in Docker on CPU, 2,000
   pictures should take 20 to 40 minutes. The build reports the real figure.
6. **Name**: for each cast member, find the clusters holding their seed pictures'
   faces, merge them, name the person, set the feature face, and hide the rest as
   strangers. Annotated identities get their boxes. Favourites and albums follow the
   script.
7. **Relate**: write `people.yaml` with `confirmed:` relations (partner, parent,
   grandparent, friend) and the household's `config.yaml` (rules tier, this Immich
   only).
8. **Snapshot**: `pg_dumpall` of the Immich database (gzip), plus a tar.zst of `/data`
   (library, thumbs, encoded-video, profile), split into parts of 1.9 GiB or less.
   Write sizes and hashes into `snapshot.lock`, then upload to the private release.

The snapshot pins the Immich version. Restoring a v3.2.2 snapshot into a newer server
runs Immich's own migrations, which is itself a test. A rebuild is needed only when
the manifest, the ML image or the cast changes.

## Restore many

`make test-e2e-public HOUSEHOLD=dog-owner [TIER=rules|model] [FILMS=...]`:

1. **Fetch** the parts named in `snapshot.lock` into a local cache (first run only)
   and verify the sha256s.
2. **Restore**: start Postgres, then `psql` the dump. Untar `/data` into a fresh
   volume, then start the server **without** ML (NAS-like). The restored DB already
   holds faces, embeddings and places. Target: under 3 minutes.
3. **Run** each film in `household.yaml` (two months, one year, one trip, one person
   film, plus a lifetime film for small households) with a fresh app HOME, so every
   run is cold and its time is measured. The rules tier always runs. The model tier
   runs only when an endpoint is configured.
4. **Judge** (below), write `report/<household>/<run>/index.html`, and fail on any hard
   invariant.
5. **Tear down** unless `PUBLIC_E2E_KEEP=1`.

It runs locally and on the GPU runner through a `workflow_dispatch` suite on the
private mirror, on demand only. It never runs on every PR or on public CI minutes.
Open question: the GPU runner is an ARC pod. If it has no Docker daemon, the same
flow runs as a Kubernetes Job with Immich deployed in a scratch namespace. The build
of household 1 settles this.

## Judging

**Hard (fail the run):**

- every `cut-invariants` record reports 0 violations;
- film rendered, playable, duration within the requested budget;
- chronological order;
- no clutter-layer file in a cut;
- no picture on the household's drop list in a cut;
- a person film shows only pictures where that person is present.

**Soft (metrics, compared with the last accepted run, flagged in the report, never
failing):** kept count, stills/videos/Live ratio, distinct days and episodes, the
trip's share, per-person screen time and the family seat, repeats (same asset twice,
near-duplicates by perceptual hash), empty weeks given a slot, cold wall time per
film, and model calls and seconds on the model tier.

**Golden set:** per film, a human-reviewed sheet in `golden/<film>.yaml`: a verdict
(good / okay / bad), must-keep and must-not keys (manifest keys, stable across
rebuilds), and a note. The report shows the cut next to the golden sheet with
must-keep recall and must-not hits. Overlap is not the score ("a good cut, not the
same cut"). A must-not hit is flagged for review, not failed, until the owner says a
household's golden set is mature.

**Report** (the 09-15 ruling): per film, a status taken from the artifacts (mp4
present, cut recorded), the tier and exact model ids, a contact sheet of the kept
pictures in order with golden marks, the film, the thesis, story labels and dropped
reasons, and the credits of every picture shown.

## Costs

| Item | Once | Per run |
| --- | --- | --- |
| Metadata index | ~30 min, ~1.5 GB download | none |
| Household build, compute | ~1 h (fetch + Immich ML on CPU) | none |
| Household build, maintainer time | 1 to 2 h picking and dropping by eye, then ~30 min per film for the golden review | none |
| Storage | 1.5 to 2.5 GB per household; ~11 GB for all seven | cache on the runner volume |
| Restore | none | ~3 min |
| Films, rules tier | none | ~20 to 30 min per household (estimated from the NAS measurements of 09-24: a cold month took 2.5 to 5 min and a cold year 46 min on a much larger library; to be measured) |
| Films, model tier | none | hosted readers: cents per film (EUR 0.02 to 0.12 per render measured earlier); local: GPU time only |
| Money | EUR 0 | EUR 0 on the rules tier |

## Order

1. The shared metadata index and timeline finder (also closes the #1154 "metadata-only
   yield" and "dataset licence verified" boxes).
2. Household 1 (dog owner) end to end: spec, fetch, stamp, build, snapshot, lock.
3. Restore + film matrix + `make test-e2e-public`.
4. Judging and report.
5. GPU runner wiring.
6. Households 2 to 7, one at a time, each with its golden review.
7. The Live pair spike runs alongside 2.

Deliverable 2 (after the owner's go) is 1 to 4 for household 1.

## Owner decisions (2026-09-25)

1. Snapshots are private. The file list, credits and recipe are public. The suite
   runs on demand only: a make target locally, and a manual `workflow_dispatch` on the
   private mirror or GPU runner. It never runs on every PR.
2. Real children in CC BY photos are allowed in the private snapshots, with the
   guardrails under Faces. Hand-drawn identities are the fallback.
3. The date shift is +12 years.
4. The light-user household stays.
5. Every fetch sends a generic User-Agent with no personal contact in it.
