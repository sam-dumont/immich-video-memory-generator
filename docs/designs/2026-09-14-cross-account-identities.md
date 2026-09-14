# One household, multiple Immich accounts

Status: proposed. Design only; the configuration and flags below are not implemented.
Refs [#717](https://github.com/sam-dumont/immich-video-memory-generator/issues/717),
[#718](https://github.com/sam-dumont/immich-video-memory-generator/issues/718) and
[#720](https://github.com/sam-dumont/immich-video-memory-generator/issues/720).

## The request

Two parents keep photos in separate Immich accounts. A child has a different face ID in each
account. Some photos exist in both libraries; others belong to only one parent. A monthly
memory of the children should use both libraries, count a shared original once, and respect
either parent's favourite. An anniversary selection should require both people in the same
photo, not merely somewhere in the combined pool.

Start with two accounts on one server. Keep the server in every source reference so a second
server cannot collide with the first. An external library inside one account remains part of
that account's existing discovery scope; it does not need a second identity account.

This changes how an existing memory finds its sources. It adds no memory type, face recognition
model, account synchronisation, asset copying or mutation of either Immich library.

## What to reuse, and what has changed

Credit: [Mike7154's discussion and reference fork](https://github.com/sam-dumont/immich-video-memory-generator/discussions/703).
The fork supplies account/subject/group models, Protocol-based discovery, an `ExitStack` for
clients, Boolean-query tests, and CLI/UI examples. Reuse that work with attribution.
Reference inspected: [cad6eb27](https://github.com/Mike7154/immich-video-memory-generator/commit/cad6eb274353470a43624ecc89debad64e6bc829),
especially `config_models_identity.py`, `identity_source.py`, `identity_generation.py` and their tests.

Three parts need adapting to current main:

- `analysis/editorial_source.py::fetch_full_window_source` fetches the whole period without
  person filters. Filtering the cross-account fetch first would discard the context the editor
  now reads. Keep the complete context pool separate from eligible carriers.
- The fork's `identity_source.py` keeps the first checksum match. #717 instead requires
  favourite first, primary account second, and a stable tie-break.
- The fork closes discovery clients and renders through the primary client. That assumes
  partner sharing grants access to every selected asset. A partner-only favourite disproves it.

Current integration points:

| Existing code | Design use |
| --- | --- |
| `api/person_expression.py` | One bounded AND/OR expression model; logical subject keys become its leaves. |
| `analysis/selection_source.py` | Preserve scope, chronology, favourites, provenance and audience rules. Its existing coalescer handles representations of one asset, not copies owned by different accounts. |
| `cli/_run_inputs.py::ResolvedRunInputs` | Consume one resolved identity selection; do not create a second generation orchestrator. |
| `people/companion.py` | Preserve confirmed people facts and merged face clusters; qualify their account scope. |
| `cache/thumbnail_cache.py`, `cache/video_cache.py`, `store/editorial_preparation.py` | Replace bare remote IDs only on the new identity route; these stores currently key by asset ID. |
| `automation/generation_request.py`, `scheduling/executor.py` | Carry the same selector and scope into unattended generation. |

## Configuration contract

`identities` is optional, top-level configuration. Without an explicit subject/group selection,
the existing single-account route stays unchanged. Configuring a partner does not silently add
their library to every memory.

Proposed example, using invented labels and placeholder IDs:

```yaml
identities:
  accounts:
    partner:
      api_key: ${PARTNER_IMMICH_API_KEY}
      # url and api_version inherit immich unless explicitly supplied
  subjects:
    child_a:
      display_name: Child A
      people: {primary: [PRIMARY_CHILD_A_ID], partner: [PARTNER_CHILD_A_ID]}
    child_b:
      display_name: Child B
      people: {primary: [PRIMARY_CHILD_B_ID], partner: [PARTNER_CHILD_B_ID]}
    parent_a:
      display_name: Parent A
      people: {primary: [PRIMARY_PARENT_A_ID], partner: [PARTNER_PARENT_A_ID]}
    parent_b:
      display_name: Parent B
      people: {primary: [PRIMARY_PARENT_B_ID], partner: [PARTNER_PARENT_B_ID]}
  groups:
    children:
      display_name: Children
      subjects: [child_a, child_b]
      match: any
    parents:
      display_name: Parents together
      subjects: [parent_a, parent_b]
      match: all
    parent_with_children:
      display_name: Parent with children
      required: [parent_a]
      any_of: [child_a, child_b]
```

`primary` is a reserved alias for the existing `immich` connection, not a second copy of its
credentials. Additional accounts may inherit that URL or name another server. Reuse
`expand_env_vars` and `ApiVersionPolicy`; unresolved credential references fail before discovery.
Credentials stay server-side, out of browser storage, command arguments and run manifests.

A subject key is its stable identity; `display_name` is presentation. Accept the fork's scalar
person-ID form as a one-element list. Multiple IDs within one account mean alternative face
clusters for the same person, matching the existing companion's merged-ID model.

Reject unknown references, empty groups, mixed group syntaxes and a qualified face ID assigned
to two subjects. An account may lack a binding for a subject; that is missing evidence, not a
reason to drop the whole account. Never bind people by matching display names automatically.
Do not support groups containing groups in the first version.

Normalize `subjects + match` to `any(...)` or `all(...)`. Normalize the composite form to
`all(required..., any(any_of...))`, omitting an absent arm. Both arms empty is invalid.
Keep the existing expression limits; do not expand nested expressions into an unbounded list
of clauses. Labels use display names; identity and dedup keys use subject keys.

An optional subject `birth_date` is the explicit date for identity-based requests. Otherwise
use a confirmed companion date only when mapped entries agree. Conflicts are reported, not
resolved by whichever account answered first. Existing single-account birthday behavior stays
unchanged here; the shared birthday resolver belongs to the #719 dependency below.

## Resolve once, retain the source through export

The proposed `identity_source` boundary returns a frozen selection, canonical sources, and a
source resolver. It composes the existing clients rather than pretending several servers are
one `SyncImmichClient`.

```mermaid
flowchart LR
    A[Primary account] --> S[Full period and qualified origins]
    B[Partner account] --> S
    S --> D[Deduplicate and resolve logical people]
    D --> E[Existing story editor]
    E --> R[Read each selected source through its account]
    R --> V[One memory video]
```

1. Resolve the requested subject/group into a canonical expression and its participating
   accounts: the union of accounts named by its subjects. Record that set in the request.
2. Authenticate each account and bind its configured alias to the server endpoint and current
   user ID (`get_current_user` already calls `/users/me`). Open clients with `ExitStack` for the
   whole worker lifetime, including preview preparation and rendering. Redact failures by alias.
3. Fetch the same exact windows from every participating account, including photos, videos and
   needed Live Photo companions. Preserve the existing visibility, place and library boundaries.
   People-query fan-out may assist discovery, but cannot narrow the editor's context pool.
4. Qualify remote references before combining results. Build checksum groups, choose a stable
   representative, and retain every admitted copy's origin, favourite and face evidence.
5. Evaluate the subject expression on logical evidence for each canonical source. Return both
   the full context and carrier eligibility to the existing source pipeline. Existing provenance,
   privacy, owner-exclusion and audience rules still apply; a star bypasses none of them.
6. Resolve every thumbnail, original, companion and burst member through its recorded account.
   Delivery uses the existing primary upload connection and album settings. Reading the partner
   account does not opt it into receiving an upload.

A requested account that is unavailable or unauthorized fails the combined request before
editing. Do not publish a smaller memory as a successful complete household run. In automation,
record the failed attempt and leave existing retry/backoff rules in charge. An accessible empty
account is valid and has a zero count. A missing face binding is reported separately from an
account failure; it never becomes positive evidence.

## Source identity, duplicates and access

Keep three identifiers distinct:

| Identifier | Meaning |
| --- | --- |
| Subject key | One logical person, independent of account and display name. |
| Remote reference | Server namespace + authenticated account identity + raw asset UUID. Used for access. |
| Canonical source ID | Stable, filesystem-safe ID for one admitted content item in this identity scope. Used in plans and caches. |

Collapse different objects only with a recognized, nonempty checksum of the same media kind.
Normalize documented checksum encodings in the API adapter. Without a usable checksum, dedup
only the same `(server namespace, remote UUID)` object, retaining each account's
access reference. Equal raw UUIDs on different servers are not duplicates. Similar-looking
edits and re-encodes remain distinct sources.

Choose the representative by `(favourite first, primary first, account alias, remote UUID)`.
This follows #717 even when the winning favourite belongs only to the partner. The canonical
favourite is OR across admitted copies. Retain all origins and logical face evidence, while
using the chosen representative's ordinary metadata consistently. Record metadata disagreements
and the chosen origin so the result can be explained. Pagination and response order cannot
change the winner or the final `(capture time, canonical ID)` order.

AND means co-occurrence in one canonical item. If two exact copies have complementary face
tags, their explicitly mapped subjects can satisfy AND. Two different photos cannot, even if
taken at the same time. This deliberately improves on the fork's per-account AND query, which
can miss complementary tags across copies. Do not infer identities absent from all copies.

The canonical ID uses a versioned identity-scope namespace plus content identity, independent
of the representative's account and favourite flag. For missing checksums it uses the server
namespace and remote UUID. The adapter projects that ID into pipeline DTOs while keeping raw references
in the resolver; synthetic IDs must never be sent to an Immich endpoint. Resolve before the
existing same-asset coalescer so different owners are not mistaken for conflicting DTOs.

The run manifest freezes the representative and origin map. A later run can choose differently
after a star or permission change; resuming an existing run must not silently change its source.
If a chosen original disappears mid-run, report the failed source instead of switching accounts
or swallowing the clip. Deterministic fallback to an equivalent origin can be a later feature.
Verify downloaded material against the recorded content identity, when available, before reuse.

Live Photo stills and motion are separate content items linked by qualified references. Equal
stills do not prove equal motion. Keep the selected copy's validated companion/burst lineage,
rewrite all member references through the resolver, and never pair a still with another
account's unrelated video because their bare IDs match.

## Cache and replay contract

No blanket migration of existing single-account asset IDs or annotation rows. The identity route
gets a versioned namespace; legacy requests, serialized walls and prompt bytes remain unchanged.
Two participating-account sets must not accidentally share private run artifacts or eligibility.

Cache pixel facts and descriptions against canonical content plus the actual preview digest,
recipe and producer version. A change of representative alone should reuse identical evidence;
a different preview must not. Face mappings, favourites, dates and owner-confirmed facts are
mutable metadata and need their own fingerprint. A changed subject binding must invalidate
selection inputs without forcing identical pixels through analysis again.

Extend `source-snapshot.private.json` with a versioned identity manifest: canonical expression,
account scope, origin map, representative decisions and metadata fingerprint. Store no keys.
Keep credentials in a separate authorization fingerprint: key rotation invalidates access and
discovery state, not the subject's identity or unchanged pixel facts. Replacing the authenticated
user behind an account alias changes its scope and cannot reuse that user's private results.

The current `people.yaml` contains unqualified face IDs. Introduce a versioned account-qualified
binding for identity mode while preserving confirmed entries. Do not concatenate two rosters
into the existing file or let a refresh overwrite one parent's confirmations with the other's.
Household-level confirmations live under subject keys in that versioned companion, with source
bindings in `identities`. They outrank inferred account roles. Conflicting account-level
confirmations need an explicit household decision; refresh must not choose one silently.

## CLI, page and unattended runs

Proposed CLI: `generate --memory-type monthly_highlights --group children --year 2024 --month 6`
or `generate --memory-type person_spotlight --subject child_a --year 2024`.
Resolve before source acquisition; project into `ResolvedRunInputs` and the existing pipeline.
Reject combining `--subject` and `--group`, or either with `--person` / `--people-expression`.
Unsupported selector/memory-type combinations fail explicitly, never ignore the selector.

Initial #718 enablement: monthly highlights, year in review and custom windows accept either
selector; person spotlight accepts a subject; multi-person accepts a group. Keep the existing
window calculations. Trip, album and annual-story combinations remain unavailable until their
catalogue/membership or date-window integrations carry the same qualified scope. The UI must
disable those combinations too, rather than offering a choice the CLI cannot honour.

The Memory brief gets the equivalent selector using existing components. Show human names and
“Either child” / “Both parents together”, plus the participating account labels. Show per-account
discovery counts, duplicate count and unique pool size in Details; expose UUIDs only in private
diagnostics. Preview, Cut, reload, owner exclusions, recut and Export all retain the same scope.
Account keys never enter a URL or the browser session.

Schedules and `GenerationRequest` carry the stable selector and its resolved scope signature.
History retains display names and the selected subject/group key. Automatic candidate keys bind
the normalized expression, participating accounts and date windows, so equivalent CLI/UI runs
dedup together while different household scopes do not consume each other's slot. Reuse the
existing variety, cap, lease and failure policies; do not add a second scheduler.

## Delivery slices and acceptance evidence

| Slice | Must be true before it ships |
| --- | --- |
| #717 foundation | Config validation, bounded expression resolution, qualified origins, deterministic dedup and resolver/cache contracts pass against two fake accounts. No user-facing feature claim yet. |
| #718 existing memory types | CLI generation uses the full context and correct account through a real export. Publish the temporary UI divergence in `tests/test_surface_parity.py`. Include a fixture transcript. |
| #720 UI and unattended selection | Same request yields the same pool, eligibility and cut from CLI, Memory and scheduled generation. Remove the declared divergence. Include browser walkthrough and screenshots. |
| #720 annual stories | Still depends on [#719](https://github.com/sam-dumont/immich-video-memory-generator/issues/719). Its birthday-to-birthday windows and anniversary rules are outside this design's implementation scope; do not close #720 while that dependency remains. |

Use vertical TDD slices during implementation. Required cases include:

- One child, two face IDs, both accounts' unique photos included; legacy mode unchanged.
- Two face clusters for one subject stay one person; equal names alone never merge identities.
- ANY, ALL and required-plus-any groups; complementary tags on exact copies; two different
  photos cannot satisfy ALL; missing bindings never become matches.
- Favourite partner copy wins; primary wins an unstarred tie; reversing account/page order
  changes nothing; an unstarred occasion remains context.
- Partner-only originals and Live motion render using partner credentials, including after
  a page reload. Same bare UUID across two servers never aliases a cache or download.
- Duplicate stills with different companions preserve the chosen copy's motion lineage.
- A star changes the representative without recaptioning identical previews; a changed preview
  or mapping invalidates the appropriate evidence; different accounts cannot read stale results.
- One account unavailable, empty, revoked mid-render or rebound to another user; no silent
  partial household success, leaked credentials or upload to an unintended account.
- CLI/UI/scheduler parity, cancellation and replay of the frozen identity manifest.

Use the two-account household as the later private acceptance case: a bounded month containing
one shared favourite, one partner-only photo, and one partner-only Live Photo. First compare
discovery, duplicate decisions and the proposed cut; then render locally. Keep real IDs, names,
dates, captions and screenshots out of public PR evidence. Hermetic fixtures demonstrate the
contract publicly. No real-library access is required to review this design.

This PR edits only this proposal. Config reference, setup instructions and memory-type docs
belong with the implementing slices and their owners. It does not enter the deployment/matrix
files, release sequencing, or the separately owned docs-debt work.
