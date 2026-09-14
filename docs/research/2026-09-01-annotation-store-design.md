---
date: 2026-09-01
status: planned — after stabilization, required before beta and announcement
builds-on: docs/designs/2026-08-31-the-per-asset-index.md (the five queries, ownership classes)
           docs/designs/2026-08-27-the-annotation-layer.md (layers, units, lifetimes)
           docs/research/2026-08-31-triage-heads-architecture.md (EmbeddingStore protocol)
supersedes: "Knowledge store migration (design-later)" — next-steps item 14
---

# The annotation store

> Postgres holds the facts. The dump holds the decisions. Your backup is 16 MB.
>
> The 2026-09-14 decision below is the implementation contract. Earlier sections are
> retained as research and inventory; conflicting backend, schema and sequencing choices
> in those sections are superseded.

## Decision 2026-09-14: stabilize first, PostgreSQL before beta

Implementation is deferred until Claude's 2026-09-14 finishing work has produced a stable
system with verified timings. Complete this migration before beta and the announcement.
Updating this plan does not start a database rollout or move the current library.
The owner keeps control of merge and release order; each merge currently cuts a release.

Sequence:

1. Finish the current stability fixes, timing instrumentation and measurements. Preserve
   the accepted editorial replay results and timings as the pre-migration baseline.
2. Implement and verify PostgreSQL in small PRs from the resulting stable main. Prepare
   import, deployment, backup/restore and replay before switching production persistence.
3. Validate the migrated system, including timing comparison, then proceed to beta and
   announcement. Do not treat a schema-only PR as completion of this prerequisite.

### One backend, standard models and migrations

Use **PostgreSQL only, SQLAlchemy 2 and Alembic**, with psycopg as the driver. The repository
owns connections, bounded pooling and transactions. ORM models describe persistent entities;
SQLAlchemy Core handles bulk fact operations where appropriate. Callers receive the existing
application values rather than depending on ORM sessions or lazy relationships.

There is no intermediate project to consolidate files into SQLite, no supported SQLite
runtime option, and no second backend test matrix. SQLite remains a read-only import source
after cutover. Existing files and stores continue serving the current release until that
cutover is ready; this is sequencing, not a runtime fallback or dual-write scheme.

Alembic owns ordered schema revisions and its version ledger. Replace the older proposal
for a custom `schema_migrations` runner and one hand-written `0001_initial.sql` containing
every table. Apply migrations at startup under a database lock so simultaneous starts do
not race. Check server and required extension compatibility before migrating. Failed
migrations roll back and refuse generation with a redacted, actionable error.

Derive models from the shipped stores and their consumers. The old schema sketch below
is not executable migration input: for example, existing manual people have `manual:...`
IDs, merged people have several aliases, and neither can be forced into one UUID column.
Preserve canonical references, confirmed facts, relationship links and content keys.
Reserve the embedding storage needed by later duplicate and place-matching work in a
versioned domain migration. Keep encoder identity and dimensions explicit; creating that
storage does not add vector consumers or an index before a measured need exists.

### Three deployment choices, the same application

| Deployment | Database location | Our objects |
| --- | --- | --- |
| Default separate PostgreSQL service | Our database in our service | Dedicated `immich_memories` schema |
| Reuse the existing Immich PostgreSQL instance | Our separate database in that instance | Dedicated `immich_memories` schema |
| Opt into the actual existing Immich database | The same database Immich uses | Dedicated `immich_memories` schema |

The third option is required support, not shorthand for another database in the same
container. The connection URL and schema are bootstrap settings, supplied before settings
can be read from the store. The default schema is `immich_memories`; its configuration
must be validated and identifiers quoted through SQLAlchemy rather than interpolated.

For a shared database:

- An administrator provisions our schema and a dedicated role. The application role has
  access to its own objects and required extension objects, with no grants to modify
  Immich's tables. Do not use Immich's superuser credential as our normal connection.
- Qualify our table names. Put the Alembic ledger in our schema and restrict migration
  reflection/autogeneration to our objects. Do not alter database-wide defaults or Immich's
  search path. No foreign keys or direct application queries into Immich's private schema;
  photo and people access continues through its API.
- Extensions are shared at database scope. Discover and validate existing vector types
  and their schema. Do not install, upgrade, relocate or drop Immich's extensions during
  our startup. Missing or incompatible requirements produce an explicit setup error.
  Provision required extensions separately for a fresh dedicated database too.
- Own-schema backup/restore must account for extension dependencies and roles. Test it in
  a database containing another application's schema and verify that schema remains intact.
  Do not assume Immich's backup includes us or that our schema is isolated from a restore
  of the whole shared database. Document both recovery paths for operators choosing it.

These requirements follow PostgreSQL's distinction between
[schemas and privileges](https://www.postgresql.org/docs/current/ddl-schemas.html) and
[database-level extensions](https://www.postgresql.org/docs/current/sql-createextension.html).
Shared-database support needs migration and restore tests alongside Immich before release.
It must work with the supported installed Immich version, without requiring 3.2 clustering,
an Immich upgrade, re-ingestion, retagging or a face-recognition reset.

The default service follows the existing decision to use Immich's PostgreSQL image.
Recheck and pin a compatible digest during implementation; the older issue text naming
`pgvector/pgvector:pg17` is not the production-image decision. Keep tests on real PostgreSQL
with required extensions. Do not silently change an operator's existing database image.

### Deliverable order and acceptance

Keep the P identifiers used by #871 and dependent plans. Split oversized slices into small
PRs; each domain's schema, importer and tests should be designed together.

| Order | Existing slice | Deliverable |
| --- | --- | --- |
| 1 | P1a | SQLAlchemy connection/transaction boundary, URL redaction, schema configuration, isolated real-PostgreSQL tests behind `make test-store`. |
| 2 | P1b | Alembic revisions and scoped ledger, startup locking, compatibility checks, rollback and shared-schema isolation tests. Domain tables arrive with their consumers. |
| 3 | P6 | Existing people model and editor through the repository, preserving aliases and manual IDs; verified YAML import/export and confirmations. No second identity registry. |
| 4 | P4/P5 | Settings repository and UI, env > config file > database > default, source labels/locks, encrypted stored secrets. |
| 5 | P2/P3/P7 | Annotation facts and banks, operational history and phase timings, automation, notifications, special days and remaining small caches. Preserve exact content keys. |
| 6 | P8 | Finish the resumable, idempotent import command across all domains; teach the replay harness to read PostgreSQL and verify imported facts as well as plans. |
| 7 | P10 | Prepare deployment and backup/restore for all three modes, including scoped shared-database recovery, Docker/Kubernetes and measured resource costs. |
| 8 | P9 / cutover | Activate mandatory PostgreSQL only after import, replay, deployment and recovery pass together. UI stays available to explain a missing store; generation refuses. |

The people slice can follow the foundation without waiting for annotation storage, but
must not activate a partially migrated production state. Household groups (#717/#718/#720)
remain later work on those same people records. This migration does not add their features.

Before cutover, test repeat imports, interrupted imports, concurrent startup, rollback,
people round trips, and equivalent editorial results on the accepted routes. Compare stage
durations and total runtime with the stabilization baseline; report measured differences.
Prove schema isolation with unrelated tables present and complete a backup/restore drill.
After cutover, SQLite imports are confined to the legacy importer, with no fallback writes.
This work touches Claude's deployment and preflight files only after the current finishing
work is complete and ownership has been coordinated.

## Historical baseline: reassessed 2026-09-13

The design below was written on 2026-09-01 against the probe-era tree: six JSONL
write-ahead logs, a triage-heads `embeddings.db`, a `judgments.db` fed by the retired
scorer. Two weeks later the story-first release replaced that tree. This section says
what actually persists on `main` today, what the owner ruled since, what Immich does
that we copy, and what changes in the plan. Everything under it that this section
contradicts is history.

### Why one Postgres, in the owner's words

"It has to run in Docker and Kubernetes. SQLite and files are a pain. Postgres is easy
to run."

The deployment targets are Docker Compose and Kubernetes. State in files inside
containers is what hurts: volumes and their permissions, backups that miss half the
files, a second replica that cannot share a SQLite file, an inference service that
needs the same facts the app has. Postgres is a service every self-hoster already runs,
because Immich needs one. So the store is PostgreSQL with pgvector, in its own
container, shipped by default, and the bare-metal install points at any Postgres.

### What the owner ruled (2026-09-13)

- No SQLite. One store, one repository layer. No second backend, no fallback.
- Configuration lives in the database and is edited from the UI. Environment variables
  and the config file are the fast bootstrap path (first run, Docker, Kubernetes) and
  act as overrides. The UI shows, per setting, where the value comes from and marks
  values set by an env var or the file as locked, with the reason.
- The same for the people graph and companion (edited in the UI, stored in the
  database, importable and exportable as YAML), automation state, run history and
  notification state.
- Take inspiration from Immich itself.
- The byte-identical replay of the ten editorial routes stays the acceptance test for
  the move (`tests/test_replay_editorial_routes.py`, `make parity`).

### What Immich does, and what we copy

Read on 2026-09-13 from `immich-app/immich` at `main` and `immich-app/immich-charts`.

| Immich | Where | We copy | We do not |
|---|---|---|---|
| Four services in compose: `immich-server`, `immich-machine-learning`, `redis` (Valkey), `database` on `ghcr.io/immich-app/postgres:14-vectorchord…`; `POSTGRES_INITDB_ARGS: --data-checksums`, `shm_size: 128mb`, a bind mount for the data directory, healthchecks on every service, `depends_on` from the server to redis and database, `DB_PASSWORD`/`DB_USERNAME`/`DB_DATABASE_NAME` from `.env` | `docker/docker-compose.yml`, `docker/example.env` | the shape and the image: an app service, an inference service, a `database` service on `ghcr.io/immich-app/postgres` with data checksums, healthchecks, `depends_on`, `.env` for the password | Redis (we have no queue); separate `DB_*` variables for the app itself (compose assembles one `IMMICH_MEMORIES_DATABASE_URL`, §5 explains the redaction it needs) |
| Migrations run on boot: `DatabaseService.onBootstrap` checks the Postgres version range, checks and creates or updates the vector extension within its accepted range, then `runMigrations()`; a nightly or out-of-range extension version refuses to start with the sentence that fixes it | `server/src/services/database.service.ts` | migrations at start, not a separate command; a version gate on Postgres and pgvector with the fixing sentence in the error | the extension update path (pgvector upgrades are the container image's job) |
| ML facts flow over HTTP: the server calls `immich-machine-learning` (`IMMICH_MACHINE_LEARNING_URL`), and the server, not the ML service, writes the result | `server/src/services/smart-info.service.ts` | exactly our #857 shape: the inference service is stateless, the app banks the facts | nothing |
| Vectors: `smart_search.embedding vector(512)` with `clip_index` (`vector_cosine_ops`), the dimension checked against the configured model and the table cleared when the model changes | `server/src/schema/tables/smart-search.table.ts`, `smart-info.service.ts` | one `embeddings` table keyed by asset and encoder key, dimensions recorded per row and checked against the encoder at config change, on the same extension path (`vector` types from pgvector, a `vchord` index when a consumer needs one) | a cosine index before a consumer measures the need (§1 point 6) |
| Settings in the database: `system_metadata (key varchar primary key, value jsonb)` holds the whole config under one key; edited from the admin UI; `IMMICH_CONFIG_FILE` makes every update throw "Cannot update configuration while IMMICH_CONFIG_FILE is in use"; `IMMICH_LOG_LEVEL` overrides the stored level and the log says which one set it | `server/src/schema/tables/system-metadata.table.ts`, `server/src/services/system-config.service.ts` | the pattern, named here "the locked-source rule": database-held settings, file and env as overrides, the UI read-only for what they set, the source shown | one jsonb blob for all settings: we store one row per key, so the UI can lock and label each field on its own instead of the whole page |
| Backups from the server container: an in-process cron (`backup.database.cronExpression`, `keepLastAmount`) runs `pg_dump` through the server's own repository, with `pg_dumpall` and `psql` as the other two bins it knows | `server/src/services/database-backup.service.ts` | the in-process nightly `pg_dump`, rotation by count, restore through `psql` (§7 already had this shape) | `pg_dumpall` (cluster-wide; it is what would sweep our database into their dump on a shared cluster) |
| Kubernetes: the Helm chart ships the server and the machine-learning deployment; Postgres is external by default (`postgresql.enabled: false`, the server env hints at a CloudNativePG `-rw` service and a user Secret) | `immich-charts/charts/immich/values.yaml` | a StatefulSet in our manifests by default, an external URL in a Secret when the cluster already has Postgres (CloudNativePG or Immich's own) | nothing |

### What persists on main today

Measured on the reference library (sizes rounded; counts kept relative on purpose).

| Store | File | Size | What it holds | Class | Fate |
|---|---|---|---|---|---|
| Annotation facts and banks | `cache/annotations.sqlite` (`store/`) | ~200 MB | `assets`, `descriptions` (per model), `description_fields`, `head_facts` (label, confidence, encoder key: 90 % of the rows), `pixel_facts`, `flags`, `asset_people`, `motion_bursts`, `editorial_episode_readings`, `editorial_period_insights`, `editorial_verdicts`, `judgments`, `visual_judgments`, `run_costs` | 2 | imported once, then Postgres only |
| Legacy judgments | `cache/judgments.db` | ~46 MB | `visual_judgments` from the retired scorer's era; still opened by `cache/judgment_cache.py` for the text gateway | 2, aging | imported once, then the file is dead |
| Operational cache | `cache.db` (schema v23) | ~20 MB | live: `pipeline_runs`, `phase_stats`, `automation_attempts`, `notification_health`; retired: `video_analysis`, `video_segments`, `video_metadata`, `asset_scores`, `asset_look_failures`, `hash_index`, `thumbnails` | live rows 1 (audit trail) | live tables imported once; retired tables dropped; the file is dead |
| Small derived caches | `thumbnail-hashes.sqlite`, `sampled-preview-hashes.sqlite`, `demanded-motion.sqlite`, `text-judgments.sqlite` | KB to MB | phash per preview, sampled-pair confirmations, demanded motion facts, text answers | 3 | imported once (cheap to recompute, free to keep) |
| People | `people.yaml` (56 KB), `people-graph.json` (312 KB) | 370 KB | confirmations and roles (human), inferred evidence graph (machine) | 1 | tables; YAML and JSON become import and export |
| Special days | `special-days.json` | 16 KB | curated catalogue the auto detector reads | 1 | table |
| Configuration | `config.yaml` (+ env) | 4 KB | every setting, Tier 1 flat and Tier 2 under `advanced:`; secrets as `${VAR}` | 1 | `settings` table; file and env stay as bootstrap and overrides |
| Per-attempt records | `cache/editorial-runs/<run>/attempts/<id>/*.private.json` (`status`, `plan`, `render-projection`, `selection-trace`, `stage-progress`), `run.private.json` index | MBs per run | the cut as planned, the storyboard, the decision log, live progress | 2 per run, disposable after retention | files, indexed in `pipeline_runs`, pruned by retention |
| Thumbnails, previews, video cache, editorial frames | `cache/*` files | ~17 GB | Immich renditions and derived frames | 3 | files, never in the database |
| Parity reference | `~/.immich-memories-matrix/*.private.json` | small | the owner's graded reference plans | private, owner-only | private file |

Two facts change the plan:

1. **No vectors are stored today.** `head_facts` keeps the label, the confidence and
   the encoder key; the DINOv2 vector itself is computed and dropped, locally or in the
   inference service. Nothing on `main` does a similarity search: near-duplicates go
   through perceptual hashes (`thumbnail_hashes`), place matching stayed a side project.
   The `embeddings` table is created anyway (below), so the day a consumer ships, the
   home exists. §1's finding that 2,304-dim packs cannot be HNSW-indexed as `vector`
   stays true: the column is `halfvec(2304)` or a `vector(256)` projection, decided by
   the first consumer.
2. **The WAL layer of §4 does not exist on main.** The pipeline writes through `store/`
   with `ON CONFLICT DO NOTHING` on content keys (`producer_key`, `evidence_key`, model,
   version). That idempotence carries over unchanged into the Postgres repository.

### Decisions

**One store, Postgres only.** `src/immich_memories/store/` becomes one repository layer
over PostgreSQL (`psycopg[binary]`, a small pool, forward-only SQL migrations in
`store/migrations/` applied at boot inside one transaction against `schema_migrations`,
after a version gate on Postgres (`>= 14, < 20`, Immich's own range) and on the `vector`
type's extension (pgvector `>= 0.7`; VectorChord present or not) that refuses to start
with the sentence that fixes it, the way Immich's `DatabaseService.onBootstrap` does).
No `sqlite3` import survives outside the one-shot importer. The annotation facts, the
banks, the operational tables, people, special days, settings and the reserved
`embeddings` table live in one database, one schema.

**Compose and Kubernetes are the primary path.** `docker-compose.yml` ships a `database`
service on Immich's own image (`ghcr.io/immich-app/postgres:17-vectorchord1.1.1-pgvector0.8.5`,
SHA-pinned) with `POSTGRES_INITDB_ARGS: --data-checksums`, `shm_size: 128mb`, a named
volume, a `pg_isready` healthcheck, and the app service `depends_on` it with
`condition: service_healthy`; compose assembles `IMMICH_MEMORIES_DATABASE_URL` from
`DB_PASSWORD` in `.env`. The file is reproduced in full below. `deploy/kubernetes` ships a StatefulSet with a
PersistentVolumeClaim and a Secret for the URL, readiness of the app gated on the
database; an existing cluster (CloudNativePG, or Immich's own) is one URL in the Secret
instead. `deploy/terraform` the same two shapes. The bare-metal install (`uv tool
install`) is the exception: it documents that Postgres is required, gives the two-line
compose for just the database and the `brew`/`apt` one-liner, and what `store import`
does on first run.

**Immich's image, VectorChord included: the reuse story.** The owner asked why not
VectorChord, since Immich uses it and using the same thing allows reuse. Re-decided: we
use Immich's Postgres image, not `pgvector/pgvector`. What that buys, exactly:

- *Same image.* `ghcr.io/immich-app/postgres` bundles VectorChord (`vchord`) with
  pgvector kept installed for its types, so a self-hoster runs one more instance of an
  image they already pull and trust, or none at all (next point). We pin the tag by
  digest and follow Immich's tag policy: their compose default is still
  `14-vectorchord0.4.3-pgvectors0.2.0` (the pgvecto.rs bridge image, PG 14, unchanged
  since 2026-06), their docs support Postgres `>= 14, < 20`, and their registry
  publishes every major from 14 to 18 with the current VectorChord (`1.1.1`) and
  pgvector (`0.8.5`). For a second container we pin `17-vectorchord1.1.1-pgvector0.8.5`
  (a supported major, current extensions, no pgvecto.rs bridge) and bump when Immich's
  compose moves; for a shared cluster we run whatever they run, which is why our SQL uses
  only the `vector` types and adds a `vchord` index only when a consumer needs one.
- *Same extension path.* `CREATE EXTENSION IF NOT EXISTS vector` is a no-op on their
  image (VectorChord requires it); `CREATE EXTENSION IF NOT EXISTS vchord` is optional
  and attempted, not required. The reserved `embeddings` table works on either.
- *Sharing the Immich cluster.* `IMMICH_MEMORIES_DATABASE_URL` pointing at their
  `database` service, with our own database and our own role inside it, our own
  migrations, and never a statement against Immich's schema (§1 point 4). Two things to
  say plainly: their image runs as superuser, so `CREATE DATABASE` and `CREATE EXTENSION`
  work with their credentials but a dedicated role is the documented path; and their
  `pg_dumpall` template would include our database in their dump, while their in-app
  backup (`pg_dump` of their database) would not, so our own nightly `pg_dump` runs in
  both shapes.
- *Or a second container of the same image.* The default compose below. One more
  process of something already on the box.

The reasons the 2026-09-01 design gave against VectorChord (an ANN engine we do not need,
a startup version gate, `shared_preload_libraries`) are costs of the extension, not of the
image, and they are Immich's to carry: the image ships preloaded and gated already, and we
simply do not create a `vchord` index until something needs one. Licence, checked on
2026-09-13 in `tensorchord/VectorChord/LICENSE`: dual AGPLv3 or Elastic License v2. We
run it as a database server the operator deploys; we do not link it, modify it or
redistribute it, and the app stays MIT, the same position Immich (AGPL itself) and every
self-hoster of it are in. pgvector is PostgreSQL-licensed. No reason not to use the image
survives.

**The NAS runs the container too.** Postgres 17 idles at roughly 60 to 90 MB resident
with the image's default `shared_buffers` of 128 MB allocated on demand, and reaches a
few hundred MB under a cut; these are the image's documented defaults, not a number
measured in this repository yet (slice P10 measures it on the DS423+ next to the
existing `preset: fast` benchmark). It replaces the page cache the app process held for
the SQLite files, so the cost on a 2 GB box is the container's baseline, not the sum.
The NAS page sets `shared_buffers=64MB` and `max_connections=20` in the compose
override.

**Configuration lives in the store, edited from the UI: the locked-source rule.** A
`settings` table (`key`, `value_json`, `updated_at`, `updated_by`) holds every Tier 1
and Tier 2 key, one row per key. Precedence, the same in the UI and the CLI:

    environment variable  >  config file  >  settings table  >  built-in default

Env and file win because they are deployment-time intent (compose, Kubernetes
manifests, `-c path`), and infrastructure-as-code must beat a click, or a restart could
quietly undo what the operator wrote. This is Immich's rule (`IMMICH_CONFIG_FILE` makes
the admin UI refuse updates; `IMMICH_LOG_LEVEL` wins over the stored level and the log
says so), applied per key instead of per page: the UI shows every setting with its
source (`database`, `env IMMICH_MEMORIES_LLM__ENDPOINT`, `file /config/config.yaml`)
and locks the field when the source is env or file, saying which one.
`immich-memories config show --sources` prints the same table. Bootstrap-only keys,
never in the database: `store.database_url` (you cannot read the database to learn
where it is) and the log level.

**Secrets.** Never in the store in clear. Either the value comes from an env var or the
file (`${VAR}` as today), or the UI stores it in the `settings` table encrypted with a
key from `IMMICH_MEMORIES_SECRET_KEY` (Fernet). Without that env key the UI cannot store
a secret and says so; the field stays locked to env or file. Log redaction becomes
value-based for every secret (the six-line fix of §5 plus the parsed password of
`database_url`).

**People.** `people` (identity, roles, confirmations) and `person_evidence` (the
inferred graph) are tables. The UI edits the tables. `people.yaml` and
`people-graph.json` stop being canonical and become the import and export format
(`people export`, `people import`): a human-editable, diffable copy of class 1 data is
worth keeping, and it is how a library moves between installs. The `confirmed:` block
round-trips byte-identical (property test, §10 risk 5).

**Vectors, reserved now.** `0001_initial.sql` runs `CREATE EXTENSION IF NOT EXISTS
vector` (and tries `vchord`, optional) and creates `embeddings (asset_id, encoder_key, dims, vector halfvec(2304),
projected vector(256), computed_at, PRIMARY KEY (asset_id, encoder_key))`, empty. The
dimensions are checked against the encoder at config change, the way Immich checks
`smart_search` against its CLIP model. No index until a consumer measures that a
sequential scan is too slow (§1 point 6). The near-duplicate pass beyond phash and the
place-match work write here when they ship; this program ships the table, not a
consumer, and says so.

**Per-attempt records stay files, indexed in the store.** The plan, the projection and
the decision log are per run, megabytes, read by the storyboard and by `runs story` /
`runs why`, and disposable after a retention window. `pipeline_runs` gains the attempt
directory and the storyboard summary; retention prunes the directories.

**Thumbnails, previews and frames stay files.** 17 GB of renditions never belong in a
database.

**Degraded mode is a refusal, and the UI stays up.** `IMMICH_MEMORIES_DATABASE_URL`
missing or the database unreachable: `preflight` fails with the sentence to fix it, the
CLI refuses to run a cut and prints the host and the error, the UI boots to a page that
says the store is missing or unreachable and how to set it, `/health/ready` says the
same, and nothing that would write is offered. No fallback store exists to write to, by
design.

**The old files are imported once.** `immich-memories store import --from
~/.immich-memories [--verify]` reads `annotations.sqlite`, `judgments.db`, `cache.db`
(live tables only), the four small caches, `people.yaml`, `people-graph.json`,
`special-days.json` and `config.yaml`, row by row, `ON CONFLICT DO NOTHING` on the
content keys, idempotent and resumable, then writes a `store-import.done` marker beside
the files. After the import the files are never written again; `--verify` runs
`make parity` and requires the identical plan and decision hash. `preflight` on a fresh
database next to an old `~/.immich-memories` suggests the command.

**Backups.** §7 as written (nightly in-process `pg_dump`, `gzip -t`, manifest,
restore-verify, rotate by count), now covering settings, people and special days, so
the dump of the title holds every class 1 and class 2 row. Thumbnails are never in a
backup.

**Tests run against a real Postgres.** Locally through `testcontainers[postgres]`
(Docker required), in CI through a `services: postgres` block on the test job
(`pgvector/pgvector:pg17`), both behind `make test-store`; the unit suite skips
`test-store` when Docker is absent and mocks nothing about the store.

### The docker-compose.yml we ship

The full file, verbatim, as slice P10 lands it. Redis is absent on purpose: nothing
queues (the scheduler runs in the app process, the inference service is called
synchronously), so there is nothing for it to hold.

```yaml
services:
  immich-memories:
    image: ghcr.io/sam-dumont/immich-video-memory-generator:latest
    container_name: immich-memories
    ports:
      - "127.0.0.1:8080:8080"
    volumes:
      - immich-memories-data:/home/immich/.immich-memories   # renditions, attempt records, logs
      - ./output:/app/output
    environment:
      IMMICH_URL: "${IMMICH_URL:-http://immich-server:2283}"   # locked in the UI: env
      IMMICH_API_KEY: "${IMMICH_API_KEY}"                       # locked in the UI: env (secret)
      IMMICH_MEMORIES_DATABASE_URL: "postgresql://${DB_USERNAME:-immich_memories}:${DB_PASSWORD}@database:5432/${DB_DATABASE_NAME:-immich_memories}"
      IMMICH_MEMORIES_SECRET_KEY: "${IMMICH_MEMORIES_SECRET_KEY:-}"   # lets the UI store secrets; empty = env-only
      IMMICH_MEMORIES_INFERENCE__FACTS_BASE_URL: "${INFERENCE_URL:-}"  # set to http://immich-memories-inference:8092 with the profile
    depends_on:
      database:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "immich-memories", "preflight", "--quiet"]
      interval: 60s
      timeout: 10s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 4G
          cpus: "4"

  immich-memories-inference:
    image: ghcr.io/sam-dumont/immich-video-memory-generator/inference:${INFERENCE_TAG:-latest}
    container_name: immich-memories-inference
    profiles:
      - inference
    extends:
      file: docker/hwaccel.inference.yml
      service: cpu
    ports:
      - "127.0.0.1:8092:8092"
    volumes:
      - immich-memories-model-cache:/cache
    environment:
      IMMICH_MEMORIES_INFERENCE_ALLOW_MODEL_DOWNLOADS: "true"
      IMMICH_MEMORIES_INFERENCE_IDLE_UNLOAD_SECONDS: "300"
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 4G
          cpus: "4"

  database:
    image: ghcr.io/immich-app/postgres:17-vectorchord1.1.1-pgvector0.8.5   # pin by digest at release time
    container_name: immich-memories-database
    environment:
      POSTGRES_PASSWORD: "${DB_PASSWORD}"
      POSTGRES_USER: "${DB_USERNAME:-immich_memories}"
      POSTGRES_DB: "${DB_DATABASE_NAME:-immich_memories}"
      POSTGRES_INITDB_ARGS: "--data-checksums"
    volumes:
      - immich-memories-database:/var/lib/postgresql/data
    shm_size: 128mb
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USERNAME:-immich_memories} -d ${DB_DATABASE_NAME:-immich_memories}"]
      interval: 5s
      timeout: 5s
      retries: 12
    restart: unless-stopped

volumes:
  immich-memories-data:
  immich-memories-model-cache:
  immich-memories-database:
```

`.env` next to it:

```dotenv
IMMICH_URL=http://immich-server:2283
IMMICH_API_KEY=
DB_PASSWORD=change-me
# DB_USERNAME=immich_memories
# DB_DATABASE_NAME=immich_memories
# IMMICH_MEMORIES_SECRET_KEY=        # `openssl rand -base64 32`; enables secret entry in the UI
# INFERENCE_URL=http://immich-memories-inference:8092
# INFERENCE_TAG=latest
```

Everything set here shows as locked in the Settings pages with its variable named;
everything not set here is edited from the UI and stored in the `settings` table.

The one-file variant for an existing Immich stack (`docker-compose.override.yml` or a
merged file on the same compose network): no `database` service of ours, the URL points
at theirs.

```yaml
services:
  immich-memories:
    image: ghcr.io/sam-dumont/immich-video-memory-generator:latest
    ports:
      - "127.0.0.1:8080:8080"
    volumes:
      - immich-memories-data:/home/immich/.immich-memories
      - ./output:/app/output
    environment:
      IMMICH_URL: "http://immich-server:2283"
      IMMICH_API_KEY: "${IMMICH_MEMORIES_API_KEY}"
      # Our own database and role inside Immich's cluster; created once by
      # `immich-memories store init --admin-url postgresql://${DB_USERNAME}:${DB_PASSWORD}@database:5432/postgres`
      IMMICH_MEMORIES_DATABASE_URL: "postgresql://immich_memories:${IMMICH_MEMORIES_DB_PASSWORD}@database:5432/immich_memories"
    depends_on:
      database:
        condition: service_healthy
    restart: unless-stopped

volumes:
  immich-memories-data:
```

Slice P10's acceptance test is this file: `docker compose config` validates both
variants, and `make docker-smoke` boots the default stack, waits for the `database`
healthcheck, and asserts the app reached the database (`preflight` green, one settings
row written and read back).

### The slices, each at most ~300 lines, each with its test and its docs page

The checklist lives in issue #871.

| # | Slice | Test | Docs |
|---|---|---|---|
| P1 | Repository layer: `store/repository.py` (pool, transactions, `ON CONFLICT` helpers), `store/migrations/0001_initial.sql` (assets, facts, banks, settings, people, special days, operational tables, `embeddings` reserved), the boot-time migrator with the Postgres and pgvector version gates, Tier 2 `store` config with `IMMICH_MEMORIES_DATABASE_URL`, redaction by name and parsed password; `make test-store` with testcontainers and the CI service | migration applies twice idempotently; the version gate's sentences; redaction; connection refusal is a typed error | `reference/config-reference.md` |
| P2 | Annotation facts and banks over the repository (`store/editorial_preparation.py`, `episode_readings.py`, `period_insights.py`, `asset_annotations.py`, verdicts, judgments); `sqlite3` gone from `analysis/` | the store tests moved onto `make test-store`; the byte-identical key test for the banks; replay guards green | `deploy/maintenance/health-logs-cache.md` |
| P3 | Operational tables over the repository: `pipeline_runs` (+ attempt dir, storyboard summary), `phase_stats`, `automation_attempts`, `notification_health`, the run index; `tracking/`, `automation/*_state*`, health and trigger APIs | run tracker and health API tests on `make test-store`; `runs story` reads the index | `deploy/maintenance/health-logs-cache.md` |
| P4 | `settings` table + `SettingsStore`; `Config` assembled env > file > table > default with a per-key source; `config show --sources` | precedence property test over generated configs; `--sources` output pinned | `deploy/configuration/config-file.md`, `create/cli/config.md` |
| P5 | Settings pages write to the table; locked fields with the source shown; secrets via `IMMICH_MEMORIES_SECRET_KEY` | e2e: change a setting in the UI, restart the hermetic app, it sticks; an env-set field is locked and names the var | `create/web-ui/settings.mdx` |
| P6 | People: `people`, `person_evidence` tables; companion editor and `people` CLI through the repository; `people export/import` | YAML round trip property test, `confirmed:` byte-identical; e2e Settings > People edits persist | `create/web-ui/settings.mdx`, `create/cli/people.md` |
| P7 | Special days and the small caches (thumbnail hashes, sampled-pair confirmations, demanded motion, text judgments) over the repository; the last `sqlite3` import outside the importer removed, enforced by an import-linter contract | counts and replay unchanged | `deploy/maintenance/health-logs-cache.md` |
| P8 | `store import --from <dir> [--verify]`: every old file once, idempotent, resumable, the `.done` marker, `preflight` suggesting it | import the hermetic library, run it twice, `make parity` identical after | `deploy/maintenance/upgrading.md` |
| P9 | Refusal mode and the missing-store page: `preflight`, CLI refusal with host and error, the UI boot page, `/health/ready` | e2e with the database stopped: the page shows the sentence, Cut is absent, the CLI exits non-zero naming the host | `deploy/self-hosting.md`, `reference/troubleshooting.md` |
| P10 | The compose file above shipped verbatim (Immich's image, checksums, shm, healthcheck, `depends_on: service_healthy`, `.env`) plus the add-to-your-Immich-stack variant, Kubernetes StatefulSet + Secret + readiness, Terraform, the NAS override (`shared_buffers=64MB`), the `uv tool install` page, backups wired (`store backup|restore|status`, nightly job); the DS423+ memory measurement recorded on the NAS page | `docker compose config` validates both variants; `make docker-smoke` boots the stack, waits for the database healthcheck and asserts the app reached it; seed, dump, drop, restore, assert loop | `deploy/installation/*`, `deploy/common-setups/nas-only.md`, `deploy/maintenance/backup.md` (new), `deploy/running-modes.md` |

Acceptance for the whole program: on a library imported from the old files, the ten
editorial routes replay byte-identical (`make parity`), and `grep -r sqlite3 src/`
returns only `store/importer.py`.

---

## 0. The problem, measured

`~/.immich-memories` is **24 GB**. `~/.immich-memories-matrix` is **40 GB**.
Inside those 64 GB, the data that cannot be recreated by any amount of compute
is **370 KB**: `people.yaml` (55 KB) and `people-graph.json` (316 KB). Nothing
in the tree tells a self-hoster which is which, and nothing backs either up.

The stores are also multiplying. Today: `cache.db` (SQLite, 20 MB, schema v23,
11 tables), `cache/judgments.db` (SQLite, 40 MB, 29,347 visual judgments +
501 prompt judgments, no migration ladder), `triage-heads/embeddings.db`
(SQLite, 111 MB, 8,174 DINOv2 rows + head_facts), `people.yaml` +
`people-graph.json` (YAML/JSON), and six JSONL WALs. Six formats, four
consistency models, one backup command (`cache backup`) that covers exactly
one of them.

This design gives all of it one home, and makes the backup automatic.

---

## 1. Verified Immich-stack findings (2026-09-01)

All checked today against upstream sources, not memory.

| Fact | Value | Source |
|---|---|---|
| Current Immich stable | **v3.1.0** (2026-07-29); v3.2.0-rc.2 is pre-release | [releases/latest](https://api.github.com/repos/immich-app/immich/releases/latest) |
| Supported Postgres | "**Immich is known to work with Postgres versions `>= 14, < 20`**" | [postgres-standalone](https://docs.immich.app/administration/postgres-standalone/) |
| Postgres their compose actually ships | `ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0@sha256:bcf6335…` — **PG 14** | [docker-compose.yml](https://raw.githubusercontent.com/immich-app/immich/main/docker/docker-compose.yml) |
| Vector extension | **VectorChord** (`vchord`), accepted range `>= 0.3, < 2.0`, checked at startup or the server refuses to boot | [postgres-standalone](https://docs.immich.app/administration/postgres-standalone/) |
| pgvector's role | **A hard prerequisite of VectorChord**: "pgvector must remain installed as it provides the data types used by `vchord`". Range `>= 0.7, < 0.9` | [postgres-standalone](https://docs.immich.app/administration/postgres-standalone/), [VectorChord docs](https://docs.vectorchord.ai/vectorchord/getting-started/installation.html) |
| pgvecto.rs | **Dead as of Immich v3.0** — "Using `DB_VECTOR_EXTENSION=pgvecto.rs` now throws an error" | [v3 migration](https://immich.app/blog/v3-migration) |
| VectorChord current | 1.1.1 (2026-02-28). Newer images exist (`18-vectorchord1.1.1-pgvector0.8.5`) but their compose default has not moved off PG14 since 2026-06-02 | [VectorChord releases](https://github.com/tensorchord/VectorChord/releases), [image tags](https://github.com/immich-app/base-images/pkgs/container/postgres) |
| Built-in automatic DB backup | **Yes.** `backup.database`: `{"cronExpression": "0 02 * * *", "enabled": true, "keepLastAmount": 14}`, written to `UPLOAD_LOCATION/backups` | [config-file](https://docs.immich.app/install/config-file/), [backup-and-restore](https://docs.immich.app/administration/backup-and-restore/) |
| Their dump command | `pg_dump --clean --if-exists --dbname=… --username=…` piped to gzip | [backup-and-restore](https://docs.immich.app/administration/backup-and-restore/) |
| Their restore needs a **sed** | `s/set_config('search_path', '', false)/set_config('search_path', 'public, pg_catalog', true)/` | [backup-and-restore](https://docs.immich.app/administration/backup-and-restore/) |
| Shared-instance policy | **No explicit ban found.** External Postgres is supported but "while not officially recommended". Superuser expected by default; non-superuser is "advanced users only" | [postgres-standalone](https://docs.immich.app/administration/postgres-standalone/) |
| Their stance on touching the DB | "Keep in mind that mucking around in the database might set the Moon on fire." Schema drift is a named, documented error class | [database-queries](https://docs.immich.app/guides/database-queries/), [errors](https://docs.immich.app/errors/) |
| pgvector current | **0.8.6** (2026-07-29). `vector` ≤ 16,000 dims at `4*d+8` bytes; `halfvec` ≤ 16,000 at `2*d+8`. **HNSW and IVFFlat index `vector` to 2,000 dims only, `halfvec` to 4,000** | [README](https://raw.githubusercontent.com/pgvector/pgvector/master/README.md), [tags](https://github.com/pgvector/pgvector/tags) |

### What these imply for us

1. **We need pgvector; we never need VectorChord.** VectorChord is an ANN engine
   for 100M-vector search; our largest table is ~21k rows. Adopting it buys a
   `shared_preload_libraries` entry, a startup version gate, and a dependency
   whose accepted range Immich polices — for nothing.
2. **Do not copy their PG 14 pin.** PG 14 is where their pgvecto.rs *bridge*
   image lives, not where they think Postgres should be; their own docs sanction
   `< 20`. We start on 17.
3. **Sharing their instance is possible and still a bad idea.** Nothing forbids
   a second `CREATE DATABASE` in their cluster, but their setup expects
   superuser, `shared_preload_libraries` is cluster-scoped, and their backup
   guidance has historically been `pg_dumpall` — cluster-wide, so it would sweep
   our database into *their* dump silently. Supported via one URL, documented as
   discouraged, with those three consequences named.
4. **Never their schema.** Not one foreign key, join, or `SELECT` against an
   Immich table; Immich stays behind its HTTP API as today. Their own docs name
   schema drift as a corruption class — we do not become a cause of it.
5. **The `sed` is a warning label, and we get to not need it.** Their restore
   repairs `search_path` because `pg_dump` emits `search_path = ''`, breaking
   resolution of the `vector` type. Our dump excludes every vector column
   (class 3), so our restore is a plain `psql` pipe. That is a *correctness*
   reason for the exclusion, independent of size.
6. **2,304-dim vectors cannot be HNSW-indexed as `vector`.** The DINOv2 token
   pack measured on disk is exactly 9,216 bytes = 2304 × fp32, and 2,304 > 2,000.
   ANN over the raw pack would need `halfvec(2304)` (≤ 4,000, legal) or a
   `binary_quantize` prefilter. At 21k rows we need neither — a sequential scan
   over 22 MB of `vector(256)` beats any index. **No vector index in slice 1.**

---

## 2. The three-class inventory, measured

Every table is born into exactly one class. The class decides whether it is in
the dump.

### Class 1 — irreplaceable human decisions. Always dumped.

| Artifact | Today | Rows | Raw | gz |
|---|---|---|---|---|
| Person identities + confirmations | `people.yaml` | ~90 people | 55 KB | **7.5 KB** |
| Person evidence graph | `people-graph.json` | — | 316 KB | **20 KB** |
| Verified truth cards | `description-truth-*/library_truth.jsonl` | 300 (+400 elsewhere) | 79 KB | **20 KB** |
| Owner approvals / cert decisions | `*-decision.json`, `*-approval.json` | ~8 | 32 KB | ~6 KB |

**Class 1 total: 48 KB compressed.** Losing it means asking a human to redo
work no machine can redo. `people.yaml`'s `confirmed:` block is the canonical
example — the file's own header says the scan "copies it through untouched,
forever".

### Class 2 — expensive model outputs. Always dumped.

| Artifact | Today | Rows | Raw | gz |
|---|---|---|---|---|
| Visual judgments | `cache/judgments.db` | 29,347 | 40 MB (31 MB of text) | **8.95 MB** |
| Analysis + scores + runs | `cache.db` | 3,445 / 8,854 / 752 | 20 MB | **3.89 MB** |
| Teacher forced-choice labels | `active-v1-labels.jsonl` | 1,403 → 20k target | 1.1 MB | 129 KB → **~1.9 MB** |
| Description cards | `description-corpus-0.5b-topup-v2.jsonl` | 2,557 → 21,458 target | 748 KB | 159 KB → **~1.5 MB** |

**Class 2 total today: ~14.9 MB compressed. At full-library scale: ~16.2 MB.**

### Class 3 — cheap to recompute. Never dumped. Each needs a named command.

| Artifact | Size | Regenerate with | Measured cost |
|---|---|---|---|
| DINOv2 embeddings | 111 MB SQLite; as `vector(2304)` **198 MB**, as `vector(256)` **22 MB** | `immich-memories store reindex --embeddings` | encoder pass over the library |
| Head facts (location/people/children/activity) | small, but derived | `immich-memories store reindex --heads` | linear/MLP over banked vectors, seconds |
| PCA basis, cascade curves, training manifests | 2.2 MB + 187 KB + 2 MB | rerun the fit | minutes |
| Thumbnails / previews / video cache / editorial frames | **20.6 GB** | re-download on demand | already fail-open |
| Home-era table and other aggregates | small | `immich-memories store rebuild-aggregates` | seconds |

**Headline, verified: 64 GB on disk → a 16 MB nightly dump. Ratio ≈ 1,400:1.**

### The measurement that decides the key

`location-labels.jsonl` has 8,420 lines and 7,908 distinct `asset_id`s — 312
duplicated assets. Of those 312:

- **312 differ in `schema_sha256`**, 200 also in `prompt_sha256`
- **0 have identical provenance with a different answer**

Same pixels, same model, same preview, same temperature — only the contract
changed. This is the version-columns rule proved rather than asserted: with
`(model_id, prompt_version, schema_version)` in the key, `ON CONFLICT … DO
NOTHING` is a correct, idempotent WAL drain. Without them, a replay silently
overwrites a good answer with one from another contract.

---

## 3. Schema sketch

One database, one schema (`annotations`), one repository package. Types below
are indicative, not final DDL.

```sql
-- Identity. No FK to Immich; asset_id is their UUID used as an opaque key.
CREATE TABLE assets (
  asset_id       uuid PRIMARY KEY,
  source_updated timestamptz NOT NULL,  -- Immich updatedAt: the whole invalidation story
  captured_at    timestamptz,
  kind           text NOT NULL,                            -- image | video
  city text, state text, country text,                     -- Immich ingest-time geocoding
  latitude double precision, longitude double precision,
  checksum text, first_seen timestamptz, last_seen timestamptz
);
CREATE INDEX assets_place    ON assets (country, state, city);           -- query 1
CREATE INDEX assets_geo      ON assets USING gist (point(longitude, latitude));
CREATE INDEX assets_captured ON assets (captured_at);

-- The version columns, factored out. A bump inserts a new producer, never mutates rows.
CREATE TABLE producers (
  producer_key   text PRIMARY KEY,       -- sha256 over the rest
  model_id       text NOT NULL,
  prompt_version text NOT NULL,          -- prompt_sha256 today
  schema_version text NOT NULL,          -- schema_sha256 today
  extra          jsonb NOT NULL DEFAULT '{}'   -- temperature, endpoint, layout versions
);

CREATE TABLE descriptions (              -- class 2
  asset_id uuid REFERENCES assets, producer_key text REFERENCES producers,
  description text NOT NULL, setting text,
  preview_sha256 text NOT NULL,          -- what the model actually saw
  source_updated timestamptz NOT NULL,   -- re-queue trigger
  fts tsvector GENERATED ALWAYS AS (to_tsvector('simple', description)) STORED,
  PRIMARY KEY (asset_id, producer_key)
);
CREATE INDEX descriptions_fts ON descriptions USING gin (fts);           -- query 3

CREATE TABLE head_facts (                -- class 2; teacher and student alike
  asset_id uuid REFERENCES assets, head_name text NOT NULL,
  producer_key text REFERENCES producers,
  label text NOT NULL, confidence real, covered boolean NOT NULL,
  PRIMARY KEY (asset_id, head_name, producer_key)
);
CREATE INDEX head_facts_lookup ON head_facts (head_name, producer_key, label);

CREATE TABLE pixel_facts (               -- class 2; forever at this key
  asset_id uuid REFERENCES assets, producer_key text REFERENCES producers,
  perceptual_hash text, sharpness real, exposure real,
  ocr_text text, face_boxes jsonb, metrics jsonb NOT NULL DEFAULT '{}',
  PRIMARY KEY (asset_id, producer_key)
);
CREATE INDEX pixel_facts_phash ON pixel_facts (perceptual_hash);         -- query 5

CREATE TABLE judgments (                 -- class 2; 29,347 rows, key preserved (§6)
  judgment_key text PRIMARY KEY, answer text NOT NULL,
  original_provenance jsonb NOT NULL, answered_at timestamptz NOT NULL
);

CREATE TABLE people (                    -- class 1; people.yaml becomes export-only
  person_id uuid PRIMARY KEY, display_name text, tier text,
  confirmed boolean NOT NULL DEFAULT false,        -- confirmed beats inferred
  attributes jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE person_links (              -- class 1
  source_id uuid REFERENCES people, kind text NOT NULL, target_id uuid REFERENCES people,
  confirmed boolean NOT NULL DEFAULT false,
  evidence jsonb NOT NULL DEFAULT '{}',            -- evidence, never a relationship claim
  PRIMARY KEY (source_id, kind, target_id)
);
CREATE TABLE truth_cards (               -- class 1
  truth_set text NOT NULL, asset_id uuid NOT NULL, fields jsonb NOT NULL,
  verified_by text NOT NULL, verified_at timestamptz NOT NULL,
  PRIMARY KEY (truth_set, asset_id)
);
CREATE TABLE approvals (                 -- class 1; content-addressed decisions
  approval_id text PRIMARY KEY,          -- decision_sha256
  subject text NOT NULL, subject_sha256 text NOT NULL,   -- what was approved
  decision text NOT NULL, reviewer text NOT NULL,
  payload jsonb NOT NULL, decided_at timestamptz NOT NULL
);

CREATE TABLE embeddings (                -- class 3; EXCLUDED from every dump
  asset_id uuid REFERENCES assets, encoder_key text NOT NULL,
  vector vector(256) NOT NULL,           -- PCA-reduced, 1,032 B/row
  raw_pack halfvec(2304),                -- optional; 4,616 B/row, index-legal
  preview_sha256 text NOT NULL, source_updated timestamptz NOT NULL,
  PRIMARY KEY (asset_id, encoder_key)
);
CREATE TABLE encoder_registry (          -- class 3
  encoder_key text PRIMARY KEY, encoder_id text NOT NULL, weights_sha256 text NOT NULL,
  preprocess_version text NOT NULL, layout_version text NOT NULL
);

CREATE TABLE pipeline_runs (             -- class 2; the run history is the audit trail
  run_id uuid PRIMARY KEY, created_at timestamptz NOT NULL, completed_at timestamptz,
  status text NOT NULL, memory_type text, date_range daterange,
  output_path text, output_bytes bigint, params jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE scheduled_job_runs (        -- new; the backup job needs it (§7)
  job_name text NOT NULL, fired_on date NOT NULL,
  started_at timestamptz NOT NULL, finished_at timestamptz,
  outcome text, detail text,
  PRIMARY KEY (job_name, fired_on)
);
```

### The five queries, each answered

From `docs/designs/2026-08-31-the-per-asset-index.md` §"The queries that shape it":

1. **Place at acquisition** — `assets_place` btree for named places,
   `assets_geo` gist for "within R km of P". Place is a filterable column,
   which is what killed the free-text-threads over-fetches (9.6k, 4.5k assets).
2. **The home-era table** — a materialized view over query 1: distinct-days ×
   month-spread per place cluster per era. Class 3; `store rebuild-aggregates`.
3. **Description-word threads** — `descriptions_fts` GIN. Library-wide concept
   scan across 18 years in one statement, no model.
4. **Warm generation** — one `WHERE asset_id = ANY($1)` per fact table returns
   every layer-1 fact in one round trip. This is the 60 s / 0-token year.
5. **Cull and dedup inputs** — `pixel_facts_phash` plus the quality columns and
   `ocr_text` for the document shortlist.

Not served, deliberately: ranking, similarity search, "best of". The index
holds evidence; taste stays in the cards — the same ruling that bans CLIP as a
basis.

**The port.** `EmbeddingStore` already exists as a Protocol in the triage-heads
architecture. The store adds siblings in one package
`src/immich_memories/store/`: `AssetIndex`, `DescriptionStore`,
`JudgmentStore`, `PeopleStore`, `TruthStore`. Constructor injection, no mixins.
New import-linter contract: `store` may not import `ui`, `cli`, `analysis`,
`processing`.

---

## 4. The WAL layer stays

Not a transitional hack. `scripts/triage_heads/memory.py` and
`generate_labels.py` already implement `flock(LOCK_EX)` per WAL, append +
`fsync(fd)` + `fsync(dirfd)`, `chmod 0600`, and a tail repair that discards only
an incomplete crash fragment. That discipline survived every crash this program
has had. Contract:

- The pipeline writes **only** to a WAL. It never blocks on Postgres.
- A drainer upserts into Postgres in one transaction and truncates on commit.
  `ON CONFLICT (asset_id, …, producer_key) DO NOTHING` — proved idempotent by
  the 312-duplicate measurement in §2.
- **DB down ≠ pipeline down.** The WAL grows; the next drain catches up.
- A WAL that fails to drain three times is left alone and surfaced in
  `/health/ready`, never silently discarded.

---

## 5. Deployment

### Compose

`docker-compose.yml` gains one service and one named volume. Digest pinning
matches the Dockerfile's existing convention (`python:3.11-slim@sha256:…`).

```yaml
services:
  immich-memories-db:
    image: pgvector/pgvector:0.8.6-pg17-bookworm@sha256:<pin at authoring time>
    container_name: immich-memories-db
    environment:
      POSTGRES_USER: immich_memories
      POSTGRES_DB: immich_memories
      POSTGRES_PASSWORD: "${IMMICH_MEMORIES_DB_PASSWORD:?set this in .env}"
      POSTGRES_INITDB_ARGS: "--data-checksums"
    volumes:
      - immich-memories-db:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U immich_memories -d immich_memories"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped
    # No published port. Reachable only on the compose network.

  immich-memories:
    depends_on:
      immich-memories-db:
        condition: service_healthy
    environment:
      IMMICH_MEMORIES_STORE__DATABASE_URL: "postgresql://immich_memories:${IMMICH_MEMORIES_DB_PASSWORD}@immich-memories-db:5432/immich_memories"

volumes:
  immich-memories-db:
```

`--data-checksums` is copied from Immich deliberately: it is how a silently
corrupting NAS disk gets caught at read time instead of at restore time. Debian
bookworm, `linux/amd64` + `linux/arm64` — matches `cross-platform first` and the
existing `python:3.11-slim` base. The Dockerfile needs `libpq5` in the runtime
stage; `pyproject.toml` gains `psycopg[binary]>=3.2` and `pgvector>=0.4`.

### Config

New **Tier 2** section `store` — a self-hoster on the default compose never
touches it. New `src/immich_memories/config_models_store.py`:

```python
class StoreConfig(BaseModel):
    database_url: str = Field(
        default="",   # empty = degraded mode: WALs and SQLite caches only
        description="Postgres DSN.",
    )
    pool_size: int = Field(default=5, ge=1, le=50)
    statement_timeout_s: int = Field(default=30, ge=1, le=600)
    backup: StoreBackupConfig = Field(default_factory=StoreBackupConfig)
```

Touch points, exactly: (1) the new model file; (2) `config_loader.py` — import,
`store: StoreConfig = Field(default_factory=…)` on `Config`, `"store"` into
`_TIER2_SECTIONS`; (3) `config.py` re-export + `__all__`; (4) `security.py`;
(5) `config-reference.md`, enforced by `make docs-config-check`.

**Two config traps found while reading, both real:**

- Log redaction is **name-based**: `CREDENTIAL_FIELD_NAMES = {"api_key",
  "password", "client_secret", "trigger_token"}`. A field named `database_url`
  carrying `postgresql://user:hunter2@host/db` is **logged in full**. Fix: add
  `"database_url"` to that frozenset *and* have `configured_secret_values()`
  yield the parsed `.password` component. This is the one place the owner's
  single-URL preference costs something, and it costs six lines.
- `--config <path>` bypasses `_apply_env_overrides()` entirely
  (`cli/__init__.py:80` calls `Config.from_yaml` directly). So the env var must
  be the **mechanical** `IMMICH_MEMORIES_STORE__DATABASE_URL`. Do **not** add a
  bare `DATABASE_URL` alias to `_CREDENTIAL_ENV_ALIASES`: it would work by
  default, silently not work with `-c`, and collide with any other tool sharing
  that container's environment.

### First boot

Forward-only SQL migrations in `store/migrations/`, `0001_initial.sql` upward,
applied inside `BEGIN` against a `schema_migrations` table — the same shape as
`cache/schema_migrator.py`, whose version discovery already happens inside the
exclusive transaction so a racing second process blocks rather than replaying.
No Alembic: one migration idiom in the codebase beats a better one alongside it.

`CREATE EXTENSION IF NOT EXISTS vector;` runs in `0001`. On Immich's cluster it
is a no-op — VectorChord already required it.

`database_url` empty ⇒ **degraded mode**: WALs still append, SQLite caches still
work, `/health/ready` reports `store: unconfigured`. Nothing breaks. That keeps
`uv tool install` users working with zero Postgres, and it is what makes this
whole migration reversible.

---

## 6. Migration order for today's artifacts

Import order matters: `assets` must exist before anything referencing it.

| # | Artifact | When | How |
|---|---|---|---|
| 1 | Immich asset list | slice 1 | live fetch; upsert `assets` keyed on `source_updated` |
| 2 | `people.yaml` + `people-graph.json` | slice 1 | `store import people` — one-shot; the YAML becomes export-only. `confirmed:` wins, always |
| 3 | `library_truth.jsonl` (300 + 400) | slice 1 | `store import truth --set description-truth-2026-08-31` |
| 4 | `*-decision.json` / `*-approval.json` | slice 1 | `store import approvals` — content-addressed, replay-safe |
| 5 | `cache.db` → `pixel_facts`, `pipeline_runs` | slice 2 | `store import legacy-cache` |
| 6 | `judgments.db` (29,347 rows) | slice 2 | `store import judgments` — key preserved verbatim (below) |
| 7 | `description-corpus-*.jsonl` | slice 3 | **stays a WAL**; drained continuously |
| 8 | `active-v1-labels.jsonl`, `location-labels.jsonl` | slice 3 | **stay WALs**; drained continuously |
| 9 | `embeddings.db` (111 MB) | slice 4 | `store import embeddings`, or just re-encode — class 3 |
| 10 | Thumbnails, previews, video cache, editorial frames | never | stay SQLite/files, explicitly disposable |

**The judgment key is not to be recomputed.** `VisualJudgmentIdentity.key()`
hashes 17 fields — page hashes, ordered input ids, ordered group ids,
annotations, model, thinking, image detail, pass/prompt/schema/render/layout
versions, upstream material, request limits, continuation identity, endpoint —
under `_VISUAL_ANSWER_VERSION = "visual2"`. Changing one byte of that
construction abandons 29,347 answers, and re-earning them is the single most
expensive line item in this document. **Slice 2 has a test that asserts the key
function is byte-identical before and after the move.** SQLite's
`INSERT OR REPLACE` becomes `ON CONFLICT (judgment_key) DO NOTHING` — same
semantics, since the key already carries everything that could change the
answer.

The same discipline preserves `SCHEMA_VERSION`/`ANALYSIS_VERSION`/
`SCORING_VERSION` from `cache/versions.py` as `producers.extra` fields on
imported rows, so a legacy row keeps saying which algorithm made it.

---

## 7. Automated backups

### Mechanism

Not a sidecar cron. The app already ships a scheduler in the single container
process; adding a second container to a self-hoster's stack to run one `pg_dump`
a night is the wrong trade.

The honest finding: **`InProcessScheduler` has no job registry.** One asyncio
loop (`POLL_SECONDS = 30.0`, started at `ui/app.py:589`), one hardcoded job (the
daily auto-run decision), cross-restart dedup hardwired to the
`automation_attempts` SQLite table via `_last_attempt_local_date()`. Its
constructor's `run_once` callable exists for tests, not as a registry.

So slice 1 owes a small, contained refactor: extract the "fire once per calendar
day at `HH:MM`, catch up after a restart like systemd `Persistent=true`" logic
into a `DailySlot`; add
`register(name: str, at: str, run: Callable[[Config], JobResult])`; back
per-job last-fired state with `scheduled_job_runs` (Postgres when configured,
SQLite `migration_v24` when not — a `uv tool install` user's backup job still
fires, dumps nothing, and says so); re-register today's automation job through
the same API unchanged. That last step is the regression risk, and it is one
test away.

The three existing schedulers that are **not** the answer: `scheduling/`
(separate daemon, `enabled: false`, shells out to `generate`),
`system_scheduler.py` (host launchd/systemd/crontab, hardcodes `auto run`,
useless in-container), and a k8s `CronJob` (works, but abandons the "no host
cron needed" property — document it as the k8s alternative, not the default).

### Defaults

```yaml
advanced:
  store:
    backup:
      enabled: true          # ON by default. The whole point.
      daily_at: "03:30"      # after Immich's 02:00, so the two never overlap
      directory: "~/.immich-memories/backups"
      keep_daily: 7
      keep_weekly: 4         # Sunday's dump promoted
      keep_monthly: 6        # the 1st's dump promoted
      verify: true           # restore-into-scratch check, see below
```

`enabled: true` is a deliberate break from `automation.enabled: false`.
Automation generates videos and costs money and CPU; a 16 MB dump costs seconds
and prevents the one failure nobody recovers from. `03:30` is chosen against
Immich's documented `"0 02 * * *"` so a NAS never runs two `pg_dump`s at once.

### What the dump contains

```
pg_dump --format=plain --clean --if-exists \
        --exclude-table-data='annotations.embeddings' \
        --exclude-table-data='annotations.embedding_staging' \
        --exclude-table-data='annotations.home_eras' \
        | gzip -9 > immich-memories-<ISO8601>-v<app>-pg<major>.sql.gz
```

`--exclude-table-data`, not `--exclude-table`: the schema ships, the rows do
not, so a restore produces empty tables that refill themselves rather than
missing tables that break queries. Plain format so the file is greppable and
restorable with `psql` alone — and because with no `vector` *data* in it, we
need none of Immich's `search_path` `sed`. The filename mirrors their convention
so a sweep-a-directory backup script picks both up the same way.

**Size estimates:** class 1 + class 2 = **16.2 MB gzipped today**
(judgments 8.95 MB, cache 3.89 MB, labels 1.9 MB at 20k, corpus 1.5 MB at
21,458, people 28 KB, truth 20 KB). Full retention (7+4+6 = 17 files) ≈
**280 MB**. Class 3 excluded: 198 MB of `vector(2304)` and 20.6 GB of media
caches never enter a dump.

### Integrity

Three checks, cheapest first, all before rotation deletes anything:

1. `gzip -t` on the written file.
2. Row-count assertion: `pipeline_runs`, `people`, `truth_cards`, `approvals`,
   `judgments` counts recorded in a sidecar `.manifest.json` next to the dump,
   plus a sha256 of the gz.
3. `verify: true` — restore into a throwaway database
   (`immich_memories_verify_<ts>`), run the manifest's count assertions against
   it, drop it. On a 16 MB dump this is single-digit seconds. **A dump that
   fails verification is kept and never counted toward retention**, and
   `/health/ready` goes `degraded` with `backup: unverified`.

Rotation is grandfather-father-son by promotion, not by three separate dumps:
one dump a night, Sunday's is also the week's, the 1st's is also the month's.

### Restore

```bash
# 1. Stop the app, keep the database up.
docker compose stop immich-memories

# 2. Restore. No sed. Nothing to repair.
gunzip -c immich-memories-2026-09-01T03:30:00Z-v0.63.0-pg17.sql.gz \
  | docker exec -i immich-memories-db \
      psql --username=immich_memories --dbname=immich_memories \
           --single-transaction --set ON_ERROR_STOP=on

# 3. Refill class 3. Costs time, not correctness.
docker compose start immich-memories
docker exec immich-memories immich-memories store reindex --embeddings --heads
```

`--single-transaction --set ON_ERROR_STOP=on` is Immich's own restore posture
and the right one: a half-applied restore is worse than a failed one.

**Tested, not documented-and-hoped.** New suite `tests/integration/store/` with
`make test-integration-store`: seed → dump → drop database → restore → assert
every class-1 and class-2 row count and a `people.confirmed` content spot-check.
This test is the deliverable of slice 1; a restore path nobody has run is not a
backup story. It needs a real Postgres, so it lives in the integration tier, not
`make test`.

### Interaction with the user's Immich backup

Documented explicitly, because getting this wrong is how people lose data:

- Our dump goes to `~/.immich-memories/backups` — **inside the config volume the
  user already backs up.** Anyone sweeping that volume gets us for free.
- Point us at **Immich's cluster** and their built-in `backup.database` job
  dumps *their* database only (`pg_dump --dbname=<theirs>`) — it will **not**
  contain ours. But if their deployment still uses the `pg_dumpall` from their
  template backup script, it **will** contain ours without their knowledge, and
  restoring their dump would restore our data too. Both directions surprise
  someone. This is the strongest practical argument for the dedicated container.
- Our rows are metadata about their photos. Restoring ours over a library that
  was itself restored from an older Immich dump leaves rows keyed to asset ids
  that no longer exist. Harmless — `last_seen` ages them out and the read path
  is fail-open — but it belongs in the docs.

---

## 8. Docs refresh plan

Per CLAUDE.md's documentation-freshness mapping. `make docs-config-check`
mechanically enforces the config page; the rest is discipline.

**New pages:**

| Page | Scope (one line) |
|---|---|
| `docs-site/docs/deploy/maintenance/backup-and-restore.md` | What the three data classes are, what the nightly dump contains and omits, retention defaults, the verify step, and the exact tested restore commands. |
| `docs-site/docs/deploy/configuration/database.md` | The `store` section: dedicated container (default), pointing at your own cluster, pointing at Immich's cluster and the three consequences, degraded mode with no database at all. |

**Updated pages:**

| Page | Scope (one line) |
|---|---|
| `docs-site/docs/deploy/installation/docker.md` | Add the `immich-memories-db` service to both compose blocks, incl. "Adding to your existing Immich stack"; rewrite `## Cache persistence` to point at the new backup page. |
| `docs-site/docs/deploy/installation/kubernetes.md` | Add the Postgres StatefulSet/PVC and the DSN secret key; replace `## Storage and backups`' `cache backup` line with the dump job (and the `CronJob` alternative). |
| `docs-site/docs/deploy/installation/terraform.md` | Note the new DB resources and the `database_url` secret variable. |
| `docs-site/docs/deploy/installation/uv-pip.md` | Degraded mode: no Postgres needed; what you give up and how to add one later. |
| `docs-site/docs/deploy/configuration/environment-variables.md` | `IMMICH_MEMORIES_STORE__DATABASE_URL`, and why there is no bare `DATABASE_URL` alias. |
| `docs-site/docs/deploy/configuration/config-file.md` | Link the new `advanced.store` section from the tier overview. |
| `docs-site/docs/deploy/maintenance/health-logs-cache.md` | Split: `## Analysis cache` shrinks to disposable caches only; add the `store:` and `backup:` blocks now in `/health/ready`. |
| `docs-site/docs/deploy/maintenance/upgrading.md` | `## Data compatibility` gains the store's forward-only migration rule and "take a dump before upgrading". |
| `docs-site/docs/deploy/common-setups/nas-only.md` | Update the ASCII architecture box and compose block with the DB container; ~200 MB extra RAM, ~300 MB extra disk. |
| `docs-site/docs/deploy/common-setups/kubernetes-gpu.md` | Same `cache backup` line replacement as the k8s page. |
| `docs-site/docs/reference/config-reference.md` | New `## Annotation store` section with every `store.*` key — **CI-enforced**. |
| `docs-site/docs/reference/cli-reference.md` | Regenerated by `make docs-cli` for the `store` command group — **CI-enforced**. |
| `docs-site/docs/reference/architecture.md` + `/ARCHITECTURE.md` | New `store/` package, its Protocols, the new import-linter contract. |
| `docs-site/docs/reference/troubleshooting.md` | "Store unreachable", "WAL not draining", "backup unverified" — each with the health field that shows it. |
| `docs-site/sidebars.ts` | Add `deploy/configuration/database` and `deploy/maintenance/backup-and-restore`. |

Run `make docs-build` after; `make ci` covers the two enforced pages.

---

## 9. Phased build plan

Each slice is independently useful and independently shippable. **STOP** means
the owner decides before the next line of code.

### Slice 1 — the store exists and the backup works end to end

Smallest thing that proves both halves. Deliberately carries almost no data.

- `store/` package: connection, pool, forward-only migrations, `0001_initial.sql`
- Tier 2 `store` config + the two security fixes from §5
- `assets`, `people`, `person_links`, `truth_cards`, `approvals`,
  `scheduled_job_runs` only
- `InProcessScheduler.register()` + `DailySlot`; automation re-registered
  through it, behavior unchanged
- backup job: dump → `gzip -t` → manifest → restore-verify → rotate
- CLI: `store init`, `store import people|truth|approvals`, `store export people`,
  `store backup`, `store restore`, `store status`
- compose + Dockerfile (`libpq5`) + `psycopg[binary]`
- `tests/integration/store/`: the seed → dump → drop → restore → assert loop

Ships with: 48 KB of class-1 data in Postgres, nightly, verified, restorable.
Nothing else moves. `people.yaml` keeps being written as export.

> **STOP 1.** Is `pgvector/pgvector:0.8.6-pg17-bookworm` the image, or does the
> owner want `ghcr.io/immich-app/postgres:17-vectorchord1.1.1-pgvector0.8.5`
> for one-image-in-the-stack symmetry with Immich? (My recommendation:
> pgvector's. We do not want VectorChord's startup gate or its
> `shared_preload_libraries` requirement for 21k rows.)
>
> **STOP 2.** Backup `enabled: true` by default — confirm. It writes ~16 MB/night
> into the config volume without being asked.

### Slice 2 — the expensive answers move

- `producers`, `judgments`, `pixel_facts`, `pipeline_runs`
- `store import judgments` + the byte-identical-key test
- `store import legacy-cache`
- `JudgmentCache` gains a Postgres implementation behind the existing interface;
  SQLite stays as the degraded-mode fallback
- backup grows from 48 KB to ~13 MB; verify time re-measured

> **STOP 3.** Once judgments live in Postgres, is `cache/judgments.db` deleted
> or kept as a read-through mirror for a release? (The file is currently
> described in its own docstring as deletable at any time — but it holds
> 29,347 answers that cost real money.)

### Slice 3 — the WAL drain and the description corpus

- `descriptions` + `head_facts` + the FTS index
- drainer for `description-corpus-*.jsonl`, `active-v1-labels.jsonl`,
  `location-labels.jsonl` with `ON CONFLICT DO NOTHING`
- the 312-duplicate case becomes a regression fixture
- query 3 (description-word threads) demonstrably answerable

### Slice 4 — vectors and the queries

- `embeddings` (`vector(256)`, optional `halfvec(2304)`), `encoder_registry`;
  `EmbeddingStore` Protocol implemented against Postgres
- `store reindex --embeddings --heads`
- queries 1, 2, 4, 5 with the place indexes and the home-era materialized view
- **no ANN index** until a measurement says a sequential scan over 22 MB is
  too slow

> **STOP 4.** Before slice 4, re-measure the warm-year claim (60 s / 0 tokens)
> against the real store. The per-asset-index doc flags that number as having
> been measured with request-scoped caches aligned by luck.

---

## 10. Risks and open questions, ranked

| # | Risk | Cheapest resolving experiment |
|---|---|---|
| 1 | **The judgment key changes during the move and 29,347 answers are abandoned.** Highest-value loss in the document. | Before writing any migration code: a test that pins `VisualJudgmentIdentity.key()` output for 10 fixed inputs. It must pass unchanged through slice 2. ~30 min. |
| 2 | **The scheduler refactor breaks daily automation.** `InProcessScheduler` is load-bearing and has no job registry; `_last_attempt_local_date` is hardwired to `automation_attempts`. | Write `register()` with the automation job as its *only* caller first, ship that alone, and confirm one real daily fire before adding the backup job. One day of observation. |
| 3 | **A NAS user cannot afford a Postgres container.** The nas-only page targets Celeron-class hardware and a 4 GB limit. | Run the existing `preset: fast` monthly benchmark (measured 10 min 08 s at `--cpus=4 --memory=4g`) with the DB container alongside; compare RSS and wall time. If Postgres costs > 300 MB idle, degraded mode becomes the documented NAS default. Half a day. |
| 4 | **Immich-cluster users get swept into, or missed by, the wrong dump.** Their template script uses `pg_dumpall`; their in-app job uses `pg_dump`. Two different outcomes for our data, neither obvious. | Stand up Immich's compose, add our database to their cluster, run both their backup paths, and grep the outputs for one of our table names. Half a day, and it settles the docs wording exactly. |
| 5 | **`people.yaml` round-trip loses an owner edit.** It is class 1 and the file explicitly promises `confirmed:` survives forever. | Property test: import the real 55 KB file, export it, diff. Must be semantically identical, and the `confirmed:` block byte-identical. ~2 h. |
| 6 | **Row weight at full library is larger than measured.** The per-asset-index doc's own open question. Today's numbers come from 8,174 embedded and 2,557 described assets, not 21,458. | Extrapolation is in §2 and it is linear; confirm by re-running the size table when the overnight corpus run finishes. Free. |
| 7 | **Postgres becomes a hard dependency by accident** — a code path that raises instead of degrading. | An integration test that runs the full monthly generation with `database_url: ""` and asserts a finished video. Should be a variant of an existing suite. |
| 8 | **`--data-checksums` + a NAS disk = a database that refuses to start.** Checksums turn silent corruption into loud failure, which is right, but it is a new failure mode for users. | Nothing to measure; document it, and make `store restore` the first suggestion in the troubleshooting entry. |

### Open questions the design does not answer

- **Do we store person cluster ids at all?** Face boxes are pixel-derived;
  person identity is Immich-owned and mutable. The per-asset-index doc defers
  this to the person-annotation-layer design, and so does this one. `people`
  above stores identities and confirmations; it stores no per-asset person
  assignment.
- **Does `pipeline_runs` move or stay?** It is operational, not annotation.
  It is in the schema above because the run history is the audit trail behind
  every generated video, and because `tracking/run_database.py` already shares
  `cache.db` with it. Splitting the "annotation store" from an "operations
  store" is a real option and this design chose not to, on the grounds that two
  Postgres databases is a worse answer than one.
- **Does the matrix directory converge?** `~/.immich-memories-matrix` is 40 GB
  of probe artifacts under active use tonight. Nothing in this design touches
  it. When the triage heads productize (next-steps item: "migrate 4 probe
  layers into analysis/"), their WALs point at the store instead of at a
  directory — that is the convergence, and it is slice 3's shape, not a
  separate migration.
