# A public test library: households built once, restored for every run

Status: design approved 2026-09-25; household 1 built and run the same day. Epic #1327, sub-issues #1328 to #1334.

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

The full metadata index (built 2026-09-25 by `make public-e2e-index`): 14,581,672 rows,
115,172 photographers, 1.7 GB of parquet, about 27 minutes at 12 parallel reads. Each shard
is resolved once to its CDN address: reading through the Hub's `/resolve` endpoint hits its
limit of 5,000 resolver calls per five minutes within the first few hundred shards.

- Metadata is cheap. One shard holds 5.5 GB of JPEG and 12 MB of metadata, of which
  9 MB is raw EXIF (not indexed). The image bytes average about 1 MB per picture; the
  build fetches Flickr's 1024 px rendition instead (about 200 KB).
- 41.5% of rows are geotagged.
- Capture years: 2007 to 2013 hold 95% of rows. The dates are shifted forward (below).
- Photographers, counted on the full index (capture years 2000 to 2014):

| Timeline shape | Photographers |
| --- | --- |
| ≥500 photos over ≥3 years | 4,369 (2,139 mostly geotagged) |
| ≥1,500 photos over ≥3 years | 1,448 (824 mostly geotagged) |
| ≥3,000 photos over ≥3 years | 645 (402 mostly geotagged) |

With the theme in at least 300 tags or titles, among the ≥1,500-photo photographers:
dog 12, horse 10, cycling/running 47, family/wedding 83 (many wedding pros, not
households), travel 149. With at least 100, among the ≥500-photo ones: dog 66, horse 49,
cycling 160, family 378, travel 492. So each theme has from ten to a few hundred
candidate timelines: enough to pick one good household per theme by eye, and not enough
to be careless.

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

`make public-e2e-build HOUSEHOLD=dog-owner` (maintainer machine, not CI). Steps, as built
for household 1 (`tests/public_e2e/build.py`, `--steps prepare,load,name,snapshot`):

1. **Index** (once, shared with #1154): `make public-e2e-index` scans CommonCatalog's
   metadata columns into a local parquet index (27 min, 1.7 GB, measured).
2. **Pick**: `python -m tests.public_e2e.timelines rank --theme dog` lists candidate
   timelines; `... sheet --uid <uid>` draws one picture per episode to pick by eye.
   Videos are picked the same way from Commons searches (`python -m
   tests.public_e2e.sources "beach waves"`) and listed by page id in `household.yaml`:
   a keyword search returns research-paper supplements, ads and cartoons next to
   home footage, so nothing is taken unseen.
3. **Prepare**: fetch the timeline at Flickr's 1024 px rendition (no API key), shift the
   dates, snap home, write EXIF (camera, time, GPS), render the clutter layer, re-encode
   the videos to H.264 with their scripted time and place, drop byte-identical files (a
   photographer's double uploads, which Immich would merge), and write `manifest.csv`
   and `CREDITS.md`.
4. **Load**: a pinned Immich with the ML container (server and ML v3.2.2 by digest) on
   docker volumes; upload; wait for every queue; run facial recognition twice more for
   the faces the first pass deferred.
5. **Name**: `--steps clusters` lists the biggest face clusters with a few pictures each;
   the maintainer names them by eye and writes a few seed pictures per person into
   `household.yaml`. Naming then takes, per person, the cluster most of their seeds
   share. It survives a rebuild: household 1's cluster ids all changed between two
   builds and every person was found again from 5 or 6 of 6 seeds. Unnamed clusters stay
   as they are, like strangers in a real library.
6. **Snapshot**: the product's own `people scan` writes `people.yaml` and its evidence
   graph, the script's relations are confirmed into it, then `pg_dumpall` (gzip) and a
   tar of `/data` split into parts of 1.9 GiB or less. `snapshot.lock` records sizes,
   hashes, image digests and counts; the parts go to a private release on the CI mirror.

The snapshot pins the Immich version. Restoring a v3.2.2 snapshot into a newer server
runs Immich's own migrations, which is itself a test. A rebuild is needed only when
the manifest, the ML image or the cast changes.

## Restore many

`make test-e2e-public HOUSEHOLD=dog-owner [TIER=rules|model] [FILMS=...]`:

1. **Fetch** the parts named in `snapshot.lock` into a local cache (first run only,
   `gh release download` from the private mirror) and verify the sha256s.
2. **Restore**: fresh volumes, Postgres, `psql` of the dump, the library untarred into
   its volume, then the server **without** ML (NAS-like). The restored DB already holds
   faces, embeddings and places. Household 1: 87 s.
3. **Run** each film in `household.yaml` with a fresh app home, so every film is cold
   and its time is measured. The rules tier mirrors a NAS install (`no_captions`
   preparation, rules reader). A film can declare `expect: no_film` when the right
   outcome is no film at all.
4. **Judge** (below), write `runs/<stamp>-<tier>/index.html` in the maintainer's work
   folder, and exit 1 on any hard failure.
5. **Tear down** unless `PUBLIC_E2E_KEEP=1`.

It runs locally and, on demand only, on the GPU runner. It never runs on every PR or on
public CI minutes.

## Hosting: a shared test Immich (owner, 2026-09-25)

The owner wants the households to live in a second Immich in the home cluster: seven
users, one per household, each with its own API key, sharing the family instance's ML.

- **A separate instance, never the family one.** It keeps the blast radius and the
  biometric data of public people away from the family library. It has its own
  Postgres, Valkey and library PVC; its server points `IMMICH_MACHINE_LEARNING_URL` at the
  family `immich-ml` service (stateless).
- **Internal only.** A MetalLB address on the couronne pool (10.2.254.58 was free on
  2026-09-25), no ingress, certificate or DNS.
- **Manifests**: `50-internal-services/immich-test.tf` in rancher-cluster (applied
  2026-09-25, commit c92825e), from the draft kept here as
  [`public-e2e/immich-test.tf`](public-e2e/immich-test.tf). Server, Postgres and Valkey
  are pinned to the Immich Gate's digests; the family ML runs v3.1.0 against the v3.2.2
  test server (owner: fine). Terraform needs no secret.
- **One command after `terraform apply`**:

      make public-e2e-provision PUBLIC_E2E_IMMICH_URL=http://10.2.254.58:2283 [ONLY=dog-owner]

  It signs up the admin on a fresh instance, creates the seven users, keeps one scoped
  key per user, and for every household built on this machine uploads the roll into its
  user (Immich answers a file it already holds as a duplicate, so a re-run only fills
  gaps), waits for faces, clustering and places, names the cast, and writes the
  household's people file. URL, logins and keys go to
  `~/.immich-memories-public-e2e/test-immich.secrets.yaml` (mode 0600, outside the repo;
  `PUBLIC_E2E_SECRETS` moves it). Re-running it is safe.
- **The owner's settings.** `tests/public_e2e/immich-settings.json` is the family
  Immich's own settings export, cut down to what shapes a library: the ML models and
  thresholds (CLIP `ViT-L-14-quickgelu__dfn2b`, faces `antelopev2`, OCR, duplicate
  detection), previews, transcoding (hardware acceleration off), metadata, geocoding,
  job concurrency and storage layout. Local builds and provisioning apply it, so a
  household is read the way the owner's library is, and the shared ML pod holds one
  model of each kind instead of two (two face models plus CLIP and OCR ran its 8 GB GPU
  out of memory on 2026-09-25). When a model changes, provisioning re-runs that ML job
  over every picture, because results from two models cannot be compared.
- **Scoped keys.** A film run reads, it never writes (upload is off). The per-household
  key carries only `asset.read`, `asset.view`, `asset.download`, `asset.statistics`,
  `album.read`, `face.read`, `person.read`, `person.statistics`, `tag.read`,
  `timeline.read` and `user.read`: every endpoint `generate` calls, mapped through
  Immich v3.2.2's OpenAPI permissions. Uploading and naming use a temporary full key per
  user, deleted at the end.
- **Runner.** `make test-e2e-public HOUSEHOLD=dog-owner TARGET=test-immich` skips the
  restore and runs the films with the household's scoped key and people file.
- **Reproducibility stays.** The per-household snapshot remains the portable artifact:
  anyone can restore it locally or in CI without the cluster.

## Household 1, as built (2026-09-25)

`dog-owner`: one Flickr photographer's CC BY timeline (a couple in a coastal city, a
chocolate labrador, two cats, friends, a wedding, ball games, long trips abroad),
June 2005 to December 2009, shifted +12 years to August 2017 to December 2021.

| | |
| --- | --- |
| Library | 3,093 files: 2,691 photos, 24 Commons videos, 378 clutter files (screenshots 135, bursts 81, documents 54, blurred 54, dark 54); 13 double uploads dropped; 80% geotagged; home snapped to 32.725, -117.175 |
| Licences | CC BY 2.0 (the photographer) and CC0 / CC BY 2.0 to 4.0 (videos); `CREDITS.md` has 24 creators |
| Faces | 111 clusters; six named (two partners, three friends, the partner's parent) from 6 seed pictures each; the biggest unnamed clusters are strangers, including a convention panel |
| Build | prepare 7 min (first fetch), load 29 min (69 s upload, 28 min ML on 8 CPUs in Colima) |
| Snapshot | 1.13 GB: `db.sql.gz` 35.6 MB, library 1.10 GB; private release `public-e2e-dog-owner-20260925` on the CI mirror (upload 1 min 52 s) |
| Restore | 87 to 115 s |

First full rules-tier run, every film cold, all six passing the hard checks:

| Film | Shots | Film | Wall | Notes |
| --- | --- | --- | --- | --- |
| month 2018-08 (220 pictures) | 13 | 57 s | 193 s | 9 of 13 favourites; ends on the trip |
| month 2019-04 | 13 | 57 s | 185 s | one burst frame kept in place of its source |
| month 2019-02 (3 pictures of a floor being laid) | none, as expected | | 4 s | the CLI exits 1 with an ERROR line for an honest empty month |
| year 2019 | 152 | 9 min 8 s | 938 s | 46 days; one video of 24 borrowed ones |
| trip, Christmas 2019 abroad | 36 | 2 min 18 s | 169 s | the visit to the partner's parent, 9 days |
| person, Kim 2018 | 50 | 3 min 26 s | 218 s | every shot has Kim |

Total: about 30 minutes for a restore plus six cold films on the M-series Mac. Soft
findings for the owner, not failures: the months are short against their material (13
shots from 220 pictures), and borrowed videos with none of the cast in them rarely make a
cut.

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
| Metadata index | 27 min, 1.7 GB (measured) | none |
| Household build, compute | household 1: 36 min (7 min fetch and stamp, 29 min Immich ML on 8 CPUs) | none |
| Household build, maintainer time | 1 to 2 h picking and dropping by eye, then ~30 min per film for the golden review | none |
| Storage | household 1: 1.13 GB (1024 px photos); ~8 GB for all seven at that rate | cache on the maintainer machine or runner volume |
| Restore | none | 87 to 115 s (household 1) |
| Films, rules tier | none | household 1: 28.5 min for six cold films (measured 2026-09-25) |
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
