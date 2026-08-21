# Immich Memories - Docker Image
# Multi-stage build for smaller final image

# Stage 1: Build dependencies
# WHY: pinned to digest to prevent supply-chain attacks via tag mutation
FROM python:3.11-slim@sha256:b1b81d67b8df73bf6067191790c233ef9e598b863fc1ccf447956138ca466d99 AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    libopus-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml README.md uv.lock ./
COPY src/ ./src/
COPY packages/ ./packages/
COPY docker/validate_install_extras.py ./docker/validate_install_extras.py

# hatch-vcs needs git history to detect version, which isn't available in Docker.
# The caller must provide the release identity explicitly.
ARG APP_VERSION
ARG INSTALL_EXTRAS=all
RUN if [ -z "${APP_VERSION}" ]; then echo "APP_VERSION build argument is required" >&2; exit 1; fi
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${APP_VERSION}

# WHY constraints from uv.lock (#345): a bare `pip wheel` re-resolves every
# dependency at build time, so the published image could ship a set CI never
# tested — that is how OpenCV 5 landed in a release (#339/#340). The lock is
# exported as a pip constraints file: the image resolves to exactly the
# versions the suite ran against.
RUN pip install --no-cache-dir "uv==0.8.17" && \
    uv export --frozen --all-extras --no-emit-project \
        --no-emit-package immich-memories-music --no-hashes \
        --format requirements-txt -o /constraints.txt
# pip rejects unnamed (path/editable) constraint lines; only pins may remain
RUN grep -E '^[A-Za-z0-9_.[-]+==' /constraints.txt > /constraints.pins && \
    mv /constraints.pins /constraints.txt

# The bundled-music package is built from the tree so the image does not depend
# on it being published yet; pip then resolves the `music` extra against it.
RUN pip wheel --no-cache-dir --wheel-dir=/wheels --constraint /constraints.txt ./packages/immich-memories-music

RUN INSTALL_TARGET="$(python docker/validate_install_extras.py "${INSTALL_EXTRAS}")" && \
    pip wheel --no-cache-dir --wheel-dir=/wheels --find-links=/wheels --constraint /constraints.txt "${INSTALL_TARGET}"


# Stage 2: Runtime image
FROM python:3.11-slim@sha256:b1b81d67b8df73bf6067191790c233ef9e598b863fc1ccf447956138ca466d99

ARG APP_VERSION
ARG VCS_REF=unknown
ARG SOURCE_URL=https://github.com/sam-dumont/immich-video-memory-generator

LABEL maintainer="Immich Memories Contributors"
LABEL description="Create video compilations from your Immich library"
LABEL org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="${SOURCE_URL}"

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Copy wheels from builder
COPY --from=builder /wheels /wheels

# Install the application
RUN pip install --no-cache-dir /wheels/*.whl && \
    rm -rf /wheels

# Create non-root user for security. UID/GID 1000 on purpose: it matches the first
# Linux user on most home servers (bind mounts of ./output and ./config are
# writable without chown) and the shipped Kubernetes/Terraform manifests
# (runAsUser: 1000).
RUN groupadd -g 1000 immich && useradd -u 1000 -g immich -d /home/immich -m immich

# Create config, cache, and NiceGUI state directories with correct ownership
RUN mkdir -p /home/immich/.immich-memories/cache /home/immich/.immich-memories/.nicegui \
      /app/output && \
    chown -R immich:immich /home/immich/.immich-memories /app/output

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV HOME=/home/immich
# Generated videos must land on the volume the compose quickstart mounts;
# the app default (~/Videos/Memories) would resolve inside the container.
ENV IMMICH_MEMORIES_OUTPUT__DIRECTORY=/app/output
# NiceGUI writes session storage to `.nicegui` beside the working directory,
# which is /app -- part of the root filesystem the hardening recipe makes
# read-only. The write fails silently and logins evaporate about ten seconds
# after the last tab closes. The config volume is writable and persists, so
# sessions survive a restart rather than merely surviving the container.
ENV NICEGUI_STORAGE_PATH=/home/immich/.immich-memories/.nicegui

# Switch to non-root user
USER immich

# Expose NiceGUI port
EXPOSE 8080

# Health check (use python instead of curl since slim image doesn't include curl)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health/live')" || exit 1

# Default command: run the UI
CMD ["immich-memories", "ui", "--host", "0.0.0.0"]
