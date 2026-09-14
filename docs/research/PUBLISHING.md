# Publishing research from a private library

Anything derived from the owner's library must undergo the manual anonymisation
used in [PR #830](https://github.com/sam-dumont/immich-video-memory-generator/pull/830)
before entering this repository, a commit message, or a PR description.

Keep raw exports and review material outside the repository. Publish numeric
aggregates and the fixed vocabulary in `scripts/research_data_schema.py`:
anonymous product and host classes, timings, costs, and measured limitations.
Remove scene descriptions, capture dates, personal context, raw library totals,
run IDs, fingerprints, machine names, addresses, and local paths. The reviewed
benchmark date and sample size are publication metadata, not capture metadata.
New schemas, labels, or benchmark dates require an explicit schema change.

Run `make research-data-check` and `make privacy-gate` before committing. The
schema check reads complete Git-index documents; the PR gate also checks each
commit, including data removed later. Neither needs a private denylist. Errors
never repeat rejected field names or values.

The schema validates JSON, not prose. Research Markdown still needs the same
manual anonymisation and owner review; a green check does not certify it safe.
The owner must also maintain the external private-terms file with terms from
past leaks. Do not put those terms in this repository or its test fixtures.
