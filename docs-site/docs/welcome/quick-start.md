---
sidebar_position: 2
title: Quick Start
---

# Quick Start

Start with one month or a small album. The setup below uses the rules reader and local image
classifiers, with no separate model server. See [Running modes](../deploy/running-modes.md) if
you want model-based selection, captions, or a mode without image classifiers.

## Docker Compose

Create an API key in Immich under **Account Settings > API Keys**. Use your Immich server's
reachable URL; `localhost` inside a container refers to that container.

```bash
curl -O https://raw.githubusercontent.com/sam-dumont/immich-video-memory-generator/main/docker-compose.yml
export IMMICH_URL="http://your-immich-server:2283"
export IMMICH_API_KEY="your-api-key"
mkdir -p output
docker compose up -d
docker compose exec immich-memories immich-memories models fetch
docker compose exec immich-memories immich-memories preflight
```

The container writes to `./output` as UID/GID 1000. On Linux, give that user write access before
the first run; see [Docker permissions](../deploy/installation/docker.md#output-directory-permissions).
Keep the named volumes: they hold settings, downloaded models, caches and run history.

Open [http://localhost:8080](http://localhost:8080). The port is published on localhost only
because authentication is off. On a remote server, use `ssh -L 8080:localhost:8080 your-server`,
or configure [authentication](../deploy/configuration/authentication.mdx) before exposing it.
Run one app replica.

## Without Docker

Install Python 3.11 or later, [uv](https://docs.astral.sh/uv/), and FFmpeg. On macOS use
`brew install ffmpeg`; on Debian or Ubuntu use `sudo apt install ffmpeg`.

With `IMMICH_URL` and `IMMICH_API_KEY` set as above:

```bash
uv tool install "immich-memories[editorial]"
export IMMICH_MEMORIES_EDITORIAL__READER=rules
export IMMICH_MEMORIES_EDITORIAL__PREPARATION__TIER=no_captions
immich-memories models fetch
immich-memories preflight
immich-memories ui
```

These environment variables last for the current shell. To keep the reader and tier, put them
in `~/.immich-memories/config.yaml`. The example still reads `IMMICH_API_KEY` from the environment;
set it for each new shell or service, or replace the placeholder with your key and restrict the
file to your user (`chmod 600 ~/.immich-memories/config.yaml`):

```yaml
immich:
  url: http://your-immich-server:2283
  api_key: "${IMMICH_API_KEY}"
advanced:
  editorial:
    reader: rules
    preparation:
      tier: no_captions
```

The native default is `full`, which requires a caption server. Setting `reader: rules` alone
does not disable caption preparation. `metadata_only` skips the model files too, but has less
image evidence and refuses a sendable export. See [native installation](../deploy/installation/uv-pip.md).

## Make a memory

On the **Memory** page, choose a type and period, leave duration on **Auto**, and press **Cut**.
Review the storyboard, change the selection if needed, then press **Export**.
[Your first memory](../create/first-memory.mdx) walks through it.

For a CLI run, substitute a month present in your library:

```bash
immich-memories generate --memory-type monthly_highlights --year 2024 --month 6
```

In Docker, prefix that command with `docker compose exec immich-memories`. Generation reads
from Immich; uploading the result is opt-in. The
[Docker guide](../deploy/installation/docker.md) lists API permissions, storage and backup details.
