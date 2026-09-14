# Household memories using the existing people model

Status: proposed, design only. Implement after the PostgreSQL people migration in
[#871](https://github.com/sam-dumont/immich-video-memory-generator/issues/871).
Refs [#717](https://github.com/sam-dumont/immich-video-memory-generator/issues/717),
[#718](https://github.com/sam-dumont/immich-video-memory-generator/issues/718) and
[#720](https://github.com/sam-dumont/immich-video-memory-generator/issues/720).

## Decision

Use the people model we already have, with both accounts' existing tagged people. Build
persistent extensions on the PostgreSQL repository once it lands. The primary acceptance
case runs before Immich 3.2, without an upgrade, re-ingest, face reset or Immich tag remapping.
Native clustering is optional for libraries already using it, never a prerequisite.

There is no new `identities.subjects` registry, second birthday field or parallel people
editor. Today `people.yaml` holds the companion. Under #871 the same people move into
PostgreSQL and YAML becomes import/export. Saved groups refer to those people.

The target is two accounts on one Immich server: a memory can include photos from both,
find a person across the selected accounts, and count an identical original once. Requiring two
people means both appear in the same item. Either owner's favourite must count when the
run explicitly includes both accounts' favourite evidence.

## Optional Immich 3.2.0 support

The [official release notes](https://immich.app/blog/v3.2.0-release) introduce cluster
groups: trusted users on one instance can share face clustering and recognize their own
people in shared assets. Names and birth dates remain per-user. This supersedes #717's
blanket assumption that sharing cannot carry usable person identity on every version.
It does not remove the need to support existing libraries with separate account people IDs.

Retroactive recognition currently requires a facial-recognition reset for the group;
the release notes say that this loses names and birth dates. Reading a library must never
create a cluster group, accept an invitation or trigger that reset. Existing tagged people
are valuable data, not disposable setup state.

Version-pinned source clarifies the optional integration, not the current library's behavior:

| Immich 3.2.0 behavior | Consequence here |
| --- | --- |
| [`mapPerson` returns `personGroupId` as the public person ID](https://github.com/immich-app/immich/blob/v3.2.0/server/src/dtos/person.dto.ts#L174), while [`findOrFail` looks up that group for the viewing user](https://github.com/immich-app/immich/blob/v3.2.0/server/src/services/person.service.ts#L659). | Accept native group IDs when present. Do not copy another user's name into the companion or require existing tags to be rebuilt. |
| [`searchMetadata` passes `viewingUserId`; `getUserIdsToSearch` includes timeline-enabled partners](https://github.com/immich-app/immich/blob/v3.2.0/server/src/services/search.service.ts). | A verified native shared view may reduce discovery reads. It must cover the same explicit scope as the two-account route. |
| [`mapAsset` returns `isFavorite` only to the asset owner](https://github.com/immich-app/immich/blob/v3.2.0/server/src/dtos/asset-response.dto.ts#L227). | A shared response's false value cannot establish that the partner did not star it. Owner-authenticated evidence is needed for the either-owner rule. |
| [`mapAsset` includes the raw asset ID, owner, people and checksum](https://github.com/immich-app/immich/blob/v3.2.0/server/src/dtos/asset-response.dto.ts). | Retain those fields for matching, exact-copy deduplication and access checks. |

For that optional route, cluster membership and asset access are separate prerequisites.
A server version alone proves neither. Shared albums need their own explicit scope; a cluster
group is not permission to read another user's whole account.

Our `api/search_service.py` already requests `withPeople` on `/search/metadata`. First test
the existing route against each account on the current server. Test 3.2 clustering separately
on a disposable fixture; Search v2 can be an adapter improvement later. Neither requires
replacing the existing bounded AND/OR expression model.

## Reuse the companion, then its PostgreSQL repository

These are existing contracts, not proposed new configuration:

| Existing boundary | Keep |
| --- | --- |
| `people/companion.py` | One record with `ids`, `name`, `birth_date`, `confirmed` and `inferred`. Human confirmations survive refresh. |
| `people/context.py::load_people_prompt_context` | Every ID in an entry resolves to the same context; owner and relationship references resolve through that mapping. |
| `analysis/editorial_people.py::_merged_identities` | One editorial person for the entry's IDs; the first ID currently anchors its canonical identity. |
| `people/editor.py` | One place to edit people, roles, notes and relationships. |
| `api/person_expression.py` | Existing bounded expression tree and leaf mapping for person selection. |

Preserve the imported canonical reference and all aliases during #871 P6. If PostgreSQL
uses internal row IDs, those must not leak into existing requests, links or editorial token
ordering. Keep manual people and confirmed relationships even when a roster refresh no
longer returns the old Immich ID. Never merge people merely because their names match.

Attach the second account's existing person ID to the same companion person through an
explicit confirmation in the existing editor. This is a local association, not an Immich
face merge or remapping job. For example, one existing person can have the primary account's
ID and the partner account's different ID, with one name, birthday and confirmation block.
Store which account can query each ID alongside that person's aliases in PostgreSQL.
Credentials remain in connection settings. One account/person binding cannot belong to two
companion people; several face clusters within an account may belong to the same person.

Preserve existing IDs and facts when adding a binding. Conflicting names or dates from the
second account do not overwrite the companion's facts. A missing binding is unresolved
evidence, not permission to match by name or a reason to reject unrelated account assets.
On an already clustered 3.2 library, a native group ID can serve both account bindings.
Changed IDs still require review; a scan cannot transfer confirmations to an unrelated face.

Saved groups add a label and an expression referring to existing canonical people. For
example, a group can represent `any(child A, child B)` or
`all(parent A, any(child A, child B))`. The words here are illustrative; saved references
use canonical IDs, so renaming somebody does not change membership. Groups have no copied
names, birthdays, roles or independent subject records. Group names must be unambiguous;
dangling person references are validation errors. Keep the existing expression size limits
and omit nested saved-group references in the first implementation.

## PostgreSQL sequencing and portability

[#871](https://github.com/sam-dumont/immich-video-memory-generator/issues/871) and the
[current store design](../research/2026-09-01-annotation-store-design.md) own persistence.
The first implementation waits for its repository and people import/editor path, including
P1 and P6. Any account connections also depend on its settings and secret handling (P4/P5).
Run-history changes depend on P3. These are prerequisites, not an instruction to reorder
the PostgreSQL release work.

Before that foundation lands, complete this design and bounded, read-only compatibility
checks. Do not add temporary SQLite tables, extend the live YAML schema with account/group
state, or build a dual-write migration bridge. The current companion writer reconstructs
its top-level keys on a scan; bolting groups into that file now would also require a second
writer migration.

Once the foundation lands:

- Extend its existing people repository with confirmed aliases and saved expressions where
  needed. CLI, UI, scheduler and editorial callers consume the same people records.
- Keep expression expansion and checksum grouping as pure operations over typed values.
  Pass loaded values into them; no SQL or YAML access inside selection logic.
- Store durable groups and any required access bindings through #871 migrations. YAML
  export/import must round-trip the same records, confirmations and references. It is not
  a second active store. Updates to people and dependent group references are transactional.
- Keep connection settings separate from person facts. Reuse the settings source/locking
  rules and encrypted secrets. A connection contains access details, never a second roster.
- Use existing stable asset references and content keys. Freeze source choices in the
  existing per-attempt records; do not design another run database or rewrite every cache.
- Test repository behavior against the real PostgreSQL test target from #871. Retain the
  migration's byte-identical editorial replay acceptance before adding household behavior.

Exact table names and repository methods belong to the landed PostgreSQL code. This design
specifies their behavior without inventing a competing persistence abstraction in advance.

## Discovery, favourites and duplicates

For the current setup, open an authenticated client for each explicitly selected account
and verify its identity with `/users/me`. A household run records admitted owners and source
scope. Filter each response to its intended owner/scope so partner sharing does not introduce
additional accounts. Preserve existing visibility and audience rules. Use the same date windows
for photos and videos; keep the complete context pool that
`analysis/editorial_source.py::fetch_full_window_source` gives the story editor. Person
selection determines eligible carriers without discarding the surrounding context.

Resolve each account's returned people IDs through that account's bindings to the existing
companion. Read favourites through the owning account. Do not infer an owner's star from a
partner-visible response. Selecting both accounts explicitly grants the requested read scope;
merely configuring a second credential does not add its library to every memory.

Keep both clients alive through preview and export. Retain an access-account reference with
each raw asset ID; every original, thumbnail and companion read uses that reference. The
existing primary connection remains the upload destination. Reading a second account does
not opt it into uploads. A native shared view can later reduce reads where equivalent people,
scope and access have been verified; owner-only favourite evidence still needs its owner.

If a selected account or required owner favourite read fails, fail the run
before editing instead of presenting an incomplete result as complete. A one-account run
can still use its existing semantics, but cannot claim to include both owners' favourites.

Deduplicate in two steps:

1. Multiple responses for the same server asset UUID represent one object. Merge evidence
   from authorized readers without counting the object twice.
2. Distinct asset UUIDs with a recognized, nonempty equal checksum and the same media kind
   are exact copies. Keep all contributing raw references. Prefer a favourited copy, then
   the primary owner's copy, then stable owner/asset IDs. Sort the final pool consistently.

A missing checksum permits only same-object deduplication. Similar pictures, edited exports
and re-encodes are not proved identical by this rule. Near-duplicate work can later use the
embedding store reserved by #871; it is not needed to collapse byte-identical copies.

Evaluate person expressions against canonical people on each deduplicated item. Exact
copies may contribute complementary, explicitly resolved tags. Separate photos cannot
satisfy an AND merely because each contains one of the requested people.

Do not replace raw Immich IDs globally with synthetic IDs. Keep a content-group key separate
from the representative asset reference, using the landed store's content-key conventions.
Cache content-derived facts by that stable key where appropriate; owner metadata and access
remain tied to their source. Stars or response order must not force the same bytes through
analysis again. Freeze the chosen representative and evidence in the attempt record for
replay, while allowing a later run to observe changed stars or permissions.

Test a partner-only source that the primary credential cannot download: its recorded owner
client must handle preview and export. Keep each still's companion lineage;
equal still checksums do not prove equal motion. A failed source read is an explicit failed
attempt, not permission to substitute another account's source silently.

Two accounts on the same server are supported without requiring native shared people.
Multiple servers remain outside this first slice; do not redesign all cache keys for them.

## Issue slices after PostgreSQL

Credit: [Mike7154's discussion and reference fork](https://github.com/sam-dumont/immich-video-memory-generator/discussions/703)
provided the household use case and Boolean-selection examples. Its configuration proposal
predates this reassessment; reuse useful behavior tests with attribution, not its duplicate
subject registry.

| Issue | Revised scope |
| --- | --- |
| #717 | Bind both accounts' existing IDs to existing people, retain account access through export, deduplicate exact copies and preserve owner favourites over the PostgreSQL foundation. Native 3.2 IDs are optional. |
| #718 | Existing `--person` / `--people-expression` selection resolves the companion's canonical people and aliases. Add saved-group selection only as a reference to those same people. No parallel `--subject` namespace. |
| #720 | The existing people editor manages saved groups; wizard, scheduler and automation pass the same resolved expression and explicit source scope. Ship CLI/UI parity together and test UI changes in a browser. |

These revisions need agreement before implementation; this design PR does not close the
three issues. Their old `identities:` examples should not be treated as the implementation
contract. Annual birthday windows still depend on
[#719](https://github.com/sam-dumont/immich-video-memory-generator/issues/719); no second
birth-date authority is introduced here. Merge and release sequencing stays with the owner.

## Acceptance before calling it supported

Use synthetic fixtures for pure behavior and real PostgreSQL for storage tests. With the
owner's two-account library, use bounded read-only checks and keep names, IDs, credentials,
server addresses and source material out of public artifacts.

- Import an existing companion and preserve all IDs, canonical references, manual entries,
  dates, confirmations and reciprocal links. Export/import is idempotent. A refresh preserves
  confirmed data; renaming a person leaves groups and scheduled references intact.
- Prove the current pre-3.2 setup works with existing tags and explicit companion bindings.
  Both accounts' sources must resolve to the same existing person, with no Immich writes,
  upgrade, re-ingest, face reset or retagging. Test missing and conflicting bindings too.
- Separately test native IDs on a disposable 3.2 fixture with clustering already configured,
  and on 3.2 without clustering. Upgrading alone must not break existing bindings or change
  source scope. Record actual capabilities, not just a successful version request.
- Compare one scoped partner-owned favourite through both readers. Verify the owner flag
  survives enrichment and wins exact-copy selection; test unavailable owner evidence too.
- Test repeated asset IDs, equal checksums with distinct IDs, missing checksums, reversed
  page order and complementary tags. A checksum group counts once; two different photos
  cannot satisfy same-item AND. A favourite change reuses content-derived analysis.
- Verify photo/video context parity, download and Live Photo companion access, permission
  loss, stable replay and existing audience boundaries. Test the same group through CLI,
  browser and scheduler before marking #718/#720 complete.

Research performed for this proposal: inspected the current companion and editorial mapping,
#871's migration plan and the upstream v3.2.0 source linked above. A prior read-only account
check confirmed that the supplied second credential belongs to the intended account. The
owner confirms the private library is not on 3.2 and will not be reclustered for this feature.
Cross-account person resolution, favourites and duplicate counts remain unverified there.
No Immich settings or tagged people were changed.
