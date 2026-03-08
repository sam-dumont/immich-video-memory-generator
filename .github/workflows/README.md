# GitHub Actions Workflows

These workflow files and templates need to be manually added to your repository.

## Quick Setup

```bash
# Create directories
mkdir -p .github/workflows
mkdir -p .github

# Copy workflows
cp docs/github-workflows/ci.yml .github/workflows/
cp docs/github-workflows/release.yml .github/workflows/
cp docs/github-workflows/pr-automation.yml .github/workflows/

# Copy templates
cp docs/github-templates/labeler.yml .github/
cp docs/github-templates/pull_request_template.md .github/

# Commit and push (requires write access to workflows)
git add .github/
git commit -m "ci: Add GitHub Actions workflows and templates"
git push
```

## Workflow Descriptions

### `ci.yml` - Continuous Integration

Runs on every push to `main` and on pull requests. All jobs use `make` targets as the single source of truth for commands.

**Jobs:**
- **commitlint**: Validates commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
- **lint**: Runs `make lint` and `make format-check`
- **typecheck**: Runs `make typecheck`
- **file-length**: Runs `make file-length` (all `.py` files must be ≤500 lines)
- **complexity**: Runs `make complexity` (Xenon grade C max)
- **test**: Runs `make test` on Python 3.11, 3.12, and 3.13, on Ubuntu and macOS
- **test-extras**: Tests optional extras (face, audio, audio-ml, gpu, mac) on Python 3.13
- **build**: Builds the package with version from git tags

Run the full CI pipeline locally with `make ci`.

### `release.yml` - Automatic Release

Runs on every push to `main` and automatically releases if there are releasable commits.

**How it works:**
1. Analyzes commits AND merged branch names since last release
2. Determines version bump based on:

   **Branch names (checked first):**
   - `breaking/*` or `major/*` → Major version bump (0.1.0 → 1.0.0)
   - `feat/*`, `feature/*`, or `minor/*` → Minor version bump (0.1.0 → 0.2.0)
   - `fix/*`, `bugfix/*`, `patch/*`, or `hotfix/*` → Patch version bump (0.1.0 → 0.1.1)

   **Commit types (conventional commits):**
   - `feat!:` or `BREAKING CHANGE:` → Major version bump
   - `feat:` → Minor version bump
   - `fix:`, `perf:` → Patch version bump

3. Creates git tag and GitHub Release
4. Builds and pushes Docker images

> **Note:** This project does NOT publish to PyPI. Releases are available via GitHub Releases and Docker images only.

**Environments required:**
- `production` - For minor/patch releases
- `production-major` - For major releases (add approval requirement)

### `pr-automation.yml` - PR Automation

Runs on pull request events.

**Features:**
- Auto-labels PRs based on files changed
- Validates PR title follows conventional commits
- Adds size labels (XS, S, M, L, XL)
- Flags breaking changes for maintainer review
- Welcomes first-time contributors

## GitHub Repository Setup

### 1. Create Environments

Go to Settings → Environments and create:

| Environment | Description | Protection Rules |
|-------------|-------------|------------------|
| `production` | Minor/patch releases | Optional: require approval |
| `production-major` | Major releases | **Required: require approval from maintainers** |

### 2. Configure Branch Protection

Go to Settings → Branches → Add rule for `main`:

- [x] Require a pull request before merging
- [x] Require approvals (1+)
- [x] Require status checks to pass
  - `CI Success`
  - `Validate PR Title`
- [x] Require conversation resolution before merging
- [x] Require signed commits (optional but recommended)
- [x] Do not allow bypassing the above settings

### 3. Configure Labels

Create these labels in your repository (Settings → Labels):

**Type labels:**
- `breaking-change` (red)
- `needs-maintainer-review` (orange)
- `documentation` (blue)
- `tests` (green)
- `ci` (purple)
- `dependencies` (yellow)

**Size labels:**
- `size/XS`, `size/S`, `size/M`, `size/L`, `size/XL`

**Area labels:**
- `area/api`, `area/analysis`, `area/processing`, `area/ui`, `area/cli`

**Platform labels:**
- `platform/mac`, `platform/nvidia`

## Semantic Versioning

Version bumps are automatic based on **branch names** and **commit messages**.

### Branch Naming Conventions

Use descriptive branch names to automatically determine the release type:

| Branch Pattern | Examples | Version Bump |
|----------------|----------|--------------|
| `breaking/*`, `major/*` | `breaking/new-config-format` | Major (0.1.0 → 1.0.0) |
| `feat/*`, `feature/*`, `minor/*` | `feat/music-overlay` | Minor (0.1.0 → 0.2.0) |
| `fix/*`, `bugfix/*`, `patch/*`, `hotfix/*` | `fix/memory-leak` | Patch (0.1.0 → 0.1.1) |

### Commit Message Conventions

Commits are also analyzed using [Conventional Commits](https://www.conventionalcommits.org/):

| Commit Type | Example | Version Bump |
|-------------|---------|--------------|
| `fix:` | `fix: resolve memory leak` | Patch (0.1.0 → 0.1.1) |
| `perf:` | `perf: optimize video encoding` | Patch |
| `feat:` | `feat: add music overlay` | Minor (0.1.0 → 0.2.0) |
| `feat!:` | `feat!: change API response format` | Major (0.1.0 → 1.0.0) |
| `BREAKING CHANGE:` in body | Any commit with breaking change | Major |

> **Priority:** Branch names are checked first, then commit messages. The highest severity wins (major > minor > patch).

### Pre-release Versions

Pre-release versions are generated on non-main branches:
- `dev/*`, `feature/*` branches → `0.2.0.dev1`
- `rc/*` branches → `0.2.0rc1`

## Commit Message Format

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`

**Examples:**
```
feat(ui): add dark mode toggle
fix(api): handle pagination correctly for large libraries
docs: update installation instructions
feat!: redesign configuration format

BREAKING CHANGE: Configuration file format has changed.
See migration guide in docs/migration.md
```
