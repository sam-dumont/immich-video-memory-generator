---
date: 2026-09-14
status: planned — after stabilization, required before beta and announcement
builds-on: docs/designs/2026-08-31-the-per-asset-index.md (the five queries, ownership classes)
           docs/designs/2026-08-27-the-annotation-layer.md (layers, units, lifetimes)
           docs/research/2026-08-31-triage-heads-architecture.md (EmbeddingStore protocol)
supersedes: "Knowledge store migration (design-later)" — next-steps item 14
---

# PostgreSQL and VectorChord persistence

This is the current plan for [#871](https://github.com/sam-dumont/immich-video-memory-generator/issues/871).
The superseded September 1/13 research remains in
[Git history](https://github.com/sam-dumont/immich-video-memory-generator/blob/24a54298cfd653c182dab3aba7dc2de61f16b229/docs/research/2026-09-01-annotation-store-design.md).
It is background, not an alternative implementation contract.

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

### Official Immich setup, checked 2026-09-14

The [official Docker Compose guide](https://docs.immich.app/install/docker-compose/)
points to the release's Compose download. Inspect that artifact, not an arbitrary registry
tag or the development branch. Latest release checked: **v3.2.0**, published September 10.
This identifies our upstream reference; it does not require upgrading the owner's library.

The database service in the
[shipped v3.2.0 Compose file](https://github.com/immich-app/immich/releases/download/v3.2.0/docker-compose.yml)
uses this exact image:

```text
ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0@sha256:bcf63357191b76a916ae5eb93464d65c07511da41e3bf7a8416db519b40b1c23
```

| Shipped database setting | Value |
| --- | --- |
| PostgreSQL / VectorChord | PostgreSQL 14 / VectorChord 0.4.3 |
| Initialization | `POSTGRES_INITDB_ARGS: --data-checksums` |
| Shared memory | `shm_size: 128mb` |
| Data volume | `DB_DATA_LOCATION` mounted at `/var/lib/postgresql/data` |
| Healthcheck | The image healthcheck is enabled with `disable: false` |
| Disk type | Compose documents `DB_STORAGE_TYPE: HDD` for non-SSD storage |

Our default database service and test fixtures follow this release image and its database
settings. The prior PG17 example was a different published image, not Immich's shipped
default, and is removed. Recheck the official release artifact when implementation starts.
Never change the image or storage of an operator's existing Immich instance automatically.

The [official existing-Postgres guide](https://docs.immich.app/administration/postgres-standalone/)
requires **VectorChord**, with `vchord.so` preloaded and the extension enabled in the target
database. It documents PostgreSQL `>= 14, < 20`, VectorChord `>= 0.3, < 2.0`, and its pgvector
type dependency `>= 0.7, < 0.9`. pgvector supplies types used by VectorChord; it is not our
chosen engine or a fallback. The image's legacy `pgvectors` tag component does not change
our requirement to use `vchord`. Verify compatible versions and preload configuration at
startup, without changing shared-server settings.

The [official backup/restore guide](https://docs.immich.app/administration/backup-and-restore/)
says that a restore replaces the current database. Our same-database option therefore
shares that recovery boundary. Its isolation and recovery need explicit testing below;
we do not claim Immich officially supports our application sharing its database.

### One backend, standard models and migrations

Use **PostgreSQL with VectorChord, SQLAlchemy 2 and Alembic**, with psycopg as the driver.
The repository owns connections, bounded pooling and transactions. ORM models describe persistent entities;
SQLAlchemy Core handles bulk fact operations where appropriate. Callers receive the existing
application values rather than depending on ORM sessions or lazy relationships.

**VectorChord (`vchord`) is required in every deployment and in database tests**, matching
Immich's stack. There is no pgvector-only mode and no optional VectorChord path. Use Immich's
PostgreSQL image for our default service and isolated tests, with compatible versions pinned
during implementation. pgvector appears only as VectorChord's required type dependency,
already part of that stack; it is not an alternative engine. See
[Immich's database requirements](https://docs.immich.app/administration/postgres-standalone/).

There is no intermediate project to consolidate files into SQLite, no supported SQLite
runtime option, and no second backend test matrix. SQLite remains a read-only import source
after cutover. Existing files and stores continue serving the current release until that
cutover is ready; this is sequencing, not a runtime fallback or dual-write scheme.

Alembic owns ordered schema revisions and its version ledger. Replace the older proposal
for a custom `schema_migrations` runner and one hand-written `0001_initial.sql` containing
every table. Apply migrations at startup under a database lock so simultaneous starts do
not race. Check PostgreSQL, VectorChord and its dependency compatibility before migrating.
Failed migrations roll back and refuse generation with a redacted, actionable error.

Derive models from the shipped stores and their consumers. The historical schema sketch
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
- Extensions are shared at database scope. Discover and validate the existing VectorChord
  extension, its dependencies and their schemas. Do not install, upgrade, relocate or drop
  Immich's extensions during our startup. Missing or incompatible requirements produce an explicit setup error.
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

The default service and test fixtures use Immich's PostgreSQL image with VectorChord.
Recheck and pin a compatible digest during implementation. A bare PostgreSQL or pgvector-only
test service does not satisfy `make test-store`. Verify the installed `vchord` version and
exercise a small VectorChord index/query in the fixture, separate from production domain
indexes. Do not silently change an operator's existing database image.

### Deliverable order and acceptance

Keep the P identifiers used by #871 and dependent plans. Split oversized slices into small
PRs; each domain's schema, importer and tests should be designed together.

| Order | Existing slice | Deliverable |
| --- | --- | --- |
| 1 | P1a | SQLAlchemy connection/transaction boundary, URL redaction, schema configuration, isolated PostgreSQL + VectorChord tests using Immich's image behind `make test-store`. |
| 2 | P1b | Alembic revisions and scoped ledger, startup locking, PostgreSQL/VectorChord compatibility checks, rollback and shared-schema isolation tests. Domain tables arrive with their consumers. |
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

### Storage and import contracts

Move persistent application state through the same repository; SQLAlchemy does not imply
changing the existing domain model or recomputing expensive analysis.

| Current storage | PostgreSQL destination / preservation rule |
| --- | --- |
| Annotation facts and model-answer banks | Preserve asset references, producer/model versions, evidence keys, source provenance and stored answers. |
| Run history and phase timings | Preserve completed attempts, delivery state and measured phase durations; index attempt directories without importing media blobs. |
| People companion and evidence graph | Preserve canonical IDs, all aliases, manual people, birthdays, roles, notes and reciprocal links. Confirmed facts beat inferred evidence. |
| Configuration | UI-editable settings with per-key source reporting; env/file overrides remain authoritative. Database URL/schema stay bootstrap-only. |
| Automation, notifications and special days | Preserve retry/backoff state, schedules and curated dates. |
| Small derived caches | Import where useful without changing their content keys. Recalculation must be explicit, not a side effect of migration. |
| Thumbnails, previews and per-attempt artifacts | Remain files, with retention and database references where needed. |

`store import --from <dir> [--verify]` reads the legacy SQLite, YAML and JSON sources without
modifying them. Track progress so interruption and repetition are safe. A completion marker
is written only after verification. Prevent concurrent old/new application writes during
cutover; there is no live dual-write period. Verify facts, people and accepted editorial
plans, not just table counts. The replay harness must read PostgreSQL for this check.

People import/export remains supported after migration. Preserve the existing canonical
references and confirmation contents rather than generating new IDs on import. Keep
human decisions and inferred evidence distinct. Stored secrets use encryption with
`IMMICH_MEMORIES_SECRET_KEY`; exporting configuration must not disclose those secrets.

### Recovery and deployment acceptance

Implement `store backup`, `store restore` and `store status`, with a scheduled database dump,
retention and a manifest identifying the application/schema version and included data.
Human decisions and expensive model answers must be recoverable. Media files need their
existing separate backup path; a database dump does not contain them.

Use database-scoped dumps for our dedicated database and schema-scoped dumps for the
same-database option. Restoration must account for roles, extension types and their schemas.
Do not run cluster-wide restore or extension-management operations through our application
role. Verify dumps by restoring into an isolated database and checking facts and references
before describing a backup as verified. Test the shared-database case alongside Immich,
including what an Immich whole-database restore does to our schema.

After the current finishing work, update Docker/Kubernetes deployment, database setup,
upgrade/import, backup/restore, configuration reference and people UI/CLI documentation
with each implementation slice. The default image, real test image and documented commands
must agree with the inspected upstream release. Measure idle and peak memory plus stage and
total run times on the target hosts; the earlier research estimates are not measurements.

Production cutover is complete only when all three deployment modes, data migration,
recovery, editorial replay and timing checks pass. This is the gate before beta and the
announcement; none of it is being activated by this plan-only PR.
