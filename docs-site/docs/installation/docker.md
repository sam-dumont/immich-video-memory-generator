---
sidebar_position: 3
title: Docker
---

# Install with Docker

Run Immich Memories as a container. No Python environment to manage.

## Docker Compose

Create a `.env` file with your Immich credentials:

```bash
IMMICH_URL=https://photos.example.com
IMMICH_API_KEY=your-api-key-here
```

Then start it:

```bash
docker-compose up -d
```

The UI is at [http://localhost:8080](http://localhost:8080).

## Standalone Docker Run

```bash
docker run -d \
  --name immich-memories \
  -p 8080:8080 \
  -e IMMICH_URL=https://photos.example.com \
  -e IMMICH_API_KEY=your-api-key-here \
  -v ./output:/app/output \
  ghcr.io/sam-dumont/immich-video-memory-generator:latest
```

## Adding to Your Existing Immich Stack

Add this to your existing Immich `docker-compose.yml`:

```yaml
services:
  immich-memories:
    image: ghcr.io/sam-dumont/immich-video-memory-generator:latest
    ports:
      - "8080:8080"
    environment:
      - IMMICH_URL=http://immich_server:3001
      - IMMICH_API_KEY=${IMMICH_API_KEY}
    networks:
      - default
    depends_on:
      - immich-server
```

This connects directly to Immich's internal network — no need to expose Immich externally.
