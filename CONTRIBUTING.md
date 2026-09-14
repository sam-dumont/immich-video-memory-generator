# Contributing to Immich Memories

Open an Issue or Discussion before starting a feature or large change so we can agree on
scope. Bug reports and ideas are welcome even if you do not plan to write code.

Keep a PR focused on one concern, around 300 changed lines where practical, excluding
generated and lock files. Link the issue, use a conventional commit title and run
`make ci` before requesting review.

## Development setup

You need Python 3.11+, FFmpeg, [uv](https://docs.astral.sh/uv/), Git and GNU Make. Media
fixtures use Git LFS; run `git lfs pull` when your checkout contains pointers instead of media.

```bash
git clone https://github.com/YOUR_USERNAME/immich-video-memory-generator.git
cd immich-video-memory-generator
make dev
make check
```

`make dev` installs the declared extras and development tools. Model weights and the
ACE-Step runtime need separate installation if your change uses them. The lighter
`make dev-test` and `make dev-ci` targets match CI's locked base/dev environment, but
`make check` and `make ci` sync all extras through `ensure-dev`.

Use the Makefile instead of running pytest, Ruff or mypy directly:

| Command | Purpose |
|---|---|
| `make test` | Default tests; some render media with FFmpeg |
| `make test-one T=tests/test_config.py` | A focused test file or node |
| `make test-integration` | Aggregate integration suites; some require Immich |
| `make ci` | Local quality, security, documentation and test gates |
| `make docs-build` | Documentation site build |
| `make launch-check` | Local checks, builds and browser launch against a fake Immich |

A local pass does not guarantee a CI pass. GitHub Actions also checks other Python
versions, macOS/Linux, package and container builds, and jobs with separate dependencies.
See the [testing guide](docs-site/docs/contribute/testing.md) for suite requirements,
coverage and interrupted-run diagnosis.

## Code and tests

Use composition and explicit dependencies. Files over 800 lines warn; files over 1000
fail. Split by responsibility, not by moving arbitrary lines into a helpers file.

Write tests alongside a behavior change, using RED → GREEN → REFACTOR. Exercise public
behavior and replace external boundaries where needed. Explain mocks with a `# WHY:`
comment. Avoid tests that repeat Python arithmetic, Pydantic defaults or mock return
values. FFmpeg pipeline changes need tests that run the affected filters or encoding path.

Integration tests use real media reads and replace upload/mutation operations. Keep test
media short and outputs temporary. Missing prerequisites can produce skips; those paths
still need validation in an environment that supplies them.

Ruff handles formatting and lint; mypy checks types. `make arch-check` enforces the package
import boundaries. These gates catch specific problems, not every design mistake. Review
your diff. The full working conventions are in [CLAUDE.md](CLAUDE.md).

## Code map

- `analysis/` owns source admission, preparation and story-first editorial selection.
- `photos/`, `processing/` and `titles/` render the selected sources; `audio/` handles music.
- `api/` talks to Immich; `ui/` and `cli/` expose the application.
- `automation/` selects unattended memory requests; `memory_types/` defines presets.
- `store/`, `cache/`, `operations/` and `tracking/` hold facts, media, attempt state and history.
- `services/inference/` serves the shared inference implementations over HTTP.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the production route and dependency boundaries.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```text
feat(ui): add a storyboard control
fix(api): handle pagination for large albums
docs: correct the macOS installation steps
refactor(analysis): separate source validation
test: cover HDR output conversion
```

## AI-assisted contributions

This project uses AI tools. Mention their use briefly in the PR and review every line
you submit. You are responsible for the change, including generated tests and prose.

`make ci` includes `make critique`, which checks several recurring code smells. A green
gate does not establish that an abstraction is useful or a test checks real behavior.

## If diff-cover fails on your PR

The CI gate requires 80% coverage on changed lines, with skips for very small or large
diffs and an Apple Vision exclusion. It runs selected FFmpeg integration suites for
supported changed paths and merges their coverage with the default suite.

```bash
make integration-coverage-for-diff
make diff-cover-local
```

The suite selector compares committed changes at `HEAD` against `origin/main`. Run the
matching integration target explicitly for uncommitted work. The local diff gate does not
apply all CI exemptions; details are in the [testing guide](docs-site/docs/contribute/testing.md#coverage-and-diff-cover).

Inspect uncovered lines and add a behavior test at the right boundary. Existing integration
suites do not necessarily reach new code. Do not force-add coverage XMLs; they are generated
reports and are deliberately gitignored.

## Getting help

Use [Discussions](https://github.com/sam-dumont/immich-video-memory-generator/discussions)
for questions and [Issues](https://github.com/sam-dumont/immich-video-memory-generator/issues)
for bugs. Include the command, relevant settings and logs with private details removed.
For security reports, follow [SECURITY.md](SECURITY.md).

This is a spare-time project. Response times vary; there is no SLA.

## Code of conduct and license

Contributions follow the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md)
and are licensed under the MIT License.
