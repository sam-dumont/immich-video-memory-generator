# Immich Memories

[![CI](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/ci.yml/badge.svg)](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/sam-dumont/immich-video-memory-generator/graph/badge.svg)](https://codecov.io/gh/sam-dumont/immich-video-memory-generator)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/sam-dumont/immich-video-memory-generator/badge)](https://scorecard.dev/viewer/?uri=github.com/sam-dumont/immich-video-memory-generator)
[![Release](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/release.yml/badge.svg)](https://github.com/sam-dumont/immich-video-memory-generator/actions/workflows/release.yml)
[![Python](https://img.shields.io/pypi/pyversions/immich-memories)](https://pypi.org/project/immich-memories/)
[![License](https://img.shields.io/github/license/sam-dumont/immich-video-memory-generator)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-Docusaurus-blue)](https://sam-dumont.github.io/immich-video-memory-generator/)

**Turn your [Immich](https://immich.app/) photo library into video memory compilations with music, title screens, and smart cuts.**

Immich Memories connects to your self-hosted Immich server, selects the best moments from your videos *and* photos, and compiles them into shareable memory videos. Year-end recaps, trip highlights, person spotlights, seasonal compilations, monthly highlights, "on this day" flashbacks -- all from a single tool.

> **Full documentation**: [sam-dumont.github.io/immich-video-memory-generator](https://sam-dumont.github.io/immich-video-memory-generator/)

<p align="center">
  <a href="https://sam-dumont.github.io/immich-video-memory-generator/docs/welcome/overview">
    <img src="https://sam-dumont.github.io/immich-video-memory-generator/img/demo-hero.gif" alt="Immich Memories demo: clip review, title screens and a finished memory video" width="800">
  </a>
  <br/>
  <sub><a href="https://sam-dumont.github.io/immich-video-memory-generator/docs/welcome/overview">▶ Watch the 60-second demo</a> · <a href="https://sam-dumont.github.io/immich-video-memory-generator/docs/create/first-memory">Make your first memory</a></sub>
</p>

**Why:** you left Google Photos for Immich and lost the year-in-review / trip / "your kid's year" videos. This brings them back — on your hardware, with clips you can veto and music that isn't canned. LLM titles and AI music are optional extras; the core pipeline runs on CPU.

---

## Docker (recommended for self-hosters)

```bash
# 1. Download the compose file
curl -O https://raw.githubusercontent.com/sam-dumont/immich-video-memory-generator/main/docker-compose.yml

# 2. Set your Immich connection
export IMMICH_URL="http://your-immich-server:2283"
export IMMICH_API_KEY="your-api-key"

# 3. Start
docker compose up -d

# 4. Open http://localhost:8080
```

> **Do not expose the default UI as-is.** Authentication is disabled by default, and the
> container listens on `0.0.0.0`. Anyone who can reach port 8080 can use it. Enable
> [authentication](https://sam-dumont.github.io/immich-video-memory-generator/docs/deploy/configuration/authentication)
> before publishing the port. The UI is single-user, single-replica; run one instance.

### Resource Requirements

Time depends mostly on whether analysis runs on a GPU/Apple Silicon or on a CPU-only box, and
analysis results are cached, so the first run of a library is the slow one.

| Phase | RAM | CPU | Apple Silicon / GPU | CPU-only (4-core NAS class) |
|-------|-----|-----|---------------------|-----------------------------|
| Idle (UI) | ~100MB | minimal | — | — |
| Analyzing clips (first run) | 2-4GB | 2+ cores | ~1 min per 10 clips | ~1-2 min per clip |
| Encoding 1080p | 4GB | 4 cores | ~2 min per 5 min of output | ~15 min for a 30-clip video |
| Encoding 4K | 6-8GB | 4+ cores | ~5 min per 5 min of output | not recommended |

Default Docker limits: 4GB RAM, 4 CPUs. A monthly memory (~30 clips) is a coffee break on a
Mac or GPU box and up to an hour on a NAS; a full year is an overnight job on a NAS. See the
[NAS-only guide](https://sam-dumont.github.io/immich-video-memory-generator/docs/deploy/common-setups/nas-only)
for a Celeron-class table and the settings that keep it usable.

> **Developed on** Apple Silicon (macOS). CI runs the unit suite on Ubuntu + macOS, the
> integration suite on a Linux NVIDIA runner, and publishes amd64 + arm64 images. Field reports
> from Synology, Unraid, Proxmox and Raspberry Pi are welcome — please
> [report your experience](https://github.com/sam-dumont/immich-video-memory-generator/issues).

### Supported Immich Versions

Immich Memories supports **Immich v2 and v3**. Automatic runtime detection is the default:

```yaml
immich:
  api_version: auto  # auto | v2 | v3
```

Leave this on `auto`. The app detects the server major version and uses the matching API contract;
you do not choose a version for each run. The explicit `v2` and `v3` values are manual
troubleshooting overrides—escape hatches for proxies or unusual deployments that hide or rewrite
the version endpoint. They force that contract, so don't use them as upgrade flags.

The compatibility layer handles the actual v2-to-v3 breaks: v2 duration strings and v3
millisecond durations both become seconds internally, uploads use the fields accepted by the
detected major, and asset search dates include the UTC offset required by v3.

Check the detected API contract and your credentials without generating or uploading anything:

```bash
immich-memories config test
```

### Optional: LLM for smart clip analysis

For AI-powered content analysis (identifies what's happening in each clip), point to any OpenAI-compatible vision model:

```yaml
# In ~/.immich-memories/config.yaml
advanced:
  llm:
    provider: "openai-compatible"
    base_url: "http://your-llm-server:8080/v1"
    model: "qwen2.5-vl"
```

## Quick Install

```bash
# One-liner (no clone needed)
uvx immich-memories --help

# Or clone and install
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
uv sync
```

## Quick Start

```bash
# 1. Configure
mkdir -p ~/.immich-memories
cat > ~/.immich-memories/config.yaml << EOF
immich:
  url: "https://photos.example.com"
  api_key: "your-api-key-here"
  api_version: auto  # auto | v2 | v3
EOF

# 2. Launch the UI
immich-memories ui
# Opens at http://localhost:8080

# 3. Or use the CLI
immich-memories generate --year 2024 --person "John" --output ~/Videos/john_2024.mp4
```

## Key Features

- **Videos + Photos** — Unified selection pool: videos, photos (Ken Burns / face-aware pan), and Live Photos
- **7 Memory Types** — Year in Review, Season, Person Spotlight, Multi-Person, Monthly Highlights, On This Day, Trip
- **Smart Clip Selection** — Scene detection, interest scoring, duplicate filtering, temporal coverage
- **Cinematic Titles** — GPU-rendered title screens with globe animations, satellite maps, month dividers
- **Face-Aware Cropping** — Keeps faces centered when converting aspect ratios
- **Hardware Acceleration** — NVIDIA NVENC, Apple VideoToolbox, Intel QSV, AMD VAAPI
- **AI Music Generation** — ACE-Step or MusicGen with automatic mood detection and audio ducking
- **Privacy Mode** — Blur all video, muffle audio, anonymize GPS/names for demos
- **Smart Automation** — one daily `auto run` decides what deserves to run, then performs one action
- **Authentication** — Basic auth, OIDC/SSO (Auth0, Authelia, Keycloak), or trusted header proxy
- **Web UI + CLI** — 4-step wizard or headless automation
- **Docker & Kubernetes** — Containerized deployment with GPU support

## Daily automation

Schedule one daily invocation of `immich-memories auto run`. It first retries the oldest pending
Immich delivery when a completed output still needs uploading; otherwise it selects and generates
one eligible memory. It never tries to catch up by doing several things in one invocation.

The terminal outcome is `skipped`, `dry_run`, `completed`, or `failed`. The first three exit 0;
`failed` exits 1. Use `--quiet` when a scheduler needs the stable JSON result.

Docker: no cron needed — set `IMMICH_MEMORIES_AUTOMATION__ENABLED=true` (and `…__DAILY_AT=09:00`)
and the web UI process runs that same decision once a day itself.

The selector keeps variety on purpose: latest completed month only, at most one monthly review per
calendar month, no category twice in a row, and no category more than twice in the last six
completed automatic runs. See the [auto CLI docs](https://sam-dumont.github.io/immich-video-memory-generator/docs/create/cli/auto).

## Documentation

See the [full documentation](https://sam-dumont.github.io/immich-video-memory-generator/) for:

- [Installation](https://sam-dumont.github.io/immich-video-memory-generator/docs/deploy/installation/docker) (Docker, uv/pip, Kubernetes, Terraform)
- [Web UI Walkthrough](https://sam-dumont.github.io/immich-video-memory-generator/docs/create/web-ui/step1-configuration)
- [CLI Reference](https://sam-dumont.github.io/immich-video-memory-generator/docs/reference/cli-reference)
- [Configuration](https://sam-dumont.github.io/immich-video-memory-generator/docs/deploy/configuration/config-file)
- [Hardware Acceleration](https://sam-dumont.github.io/immich-video-memory-generator/docs/deploy/hardware/overview)
- [Audio & Music](https://sam-dumont.github.io/immich-video-memory-generator/docs/create/pipeline/audio-and-music)
- [Recipes](https://sam-dumont.github.io/immich-video-memory-generator/docs/create/recipes/birthday-compilations) (birthday compilations, automation, best practices)

## How the maintainer runs it

```mermaid
graph LR
    subgraph "Apple Silicon Mac"
        IM["Immich Memories<br/>Python + FFmpeg"]
        LLM["omlx (mlx-vlm)<br/>local vision LLM"]
    end

    subgraph "Optional GPU box / K8s"
        ACE["ACE-Step 1.5<br/>(or in-process on the Mac)"]
        MG["MusicGen API<br/>(fallback)"]
    end

    subgraph "Synology NAS"
        Immich["Immich v2 or v3<br/>Photos + Videos"]
    end

    IM -->|"API reads<br/>(download clips)"| Immich
    IM -->|"Vision analysis<br/>(clip scoring)"| LLM
    IM -->|"Background music<br/>(AI-generated)"| ACE
    ACE -.->|"fallback"| MG
    IM -->|"Upload back<br/>(optional)"| Immich
```

*One example, not a requirement. The LLM runs locally on the Mac via [omlx](https://github.com/nicepkg/omlx) (Apple Silicon MLX); music generation runs either in-process (ACE-Step on Apple Silicon) or on a remote GPU API. Both are optional — without them you get template titles and your own music (or silence), and everything else still works.*

## Development

```bash
make dev      # Install all dependencies
make check    # Run all checks (lint, format, typecheck, tests)
make ci       # Full CI pipeline
make help     # Show all available targets
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Built with AI

> This entire codebase was written with AI (Claude) as an experiment in building complex
> software cleanly with AI assistance. 5,000+ tests (4,400+ unit, 600+ integration/E2E), ~20 CI quality gates, 250+ source modules.
> See [DISCLAIMER.md](DISCLAIMER.md) for the full story.

## License

MIT License — see [LICENSE](LICENSE) for details.

---

**Made with ❤️ for the Immich community**
