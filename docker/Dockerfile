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
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY docker/validate_install_extras.py ./docker/validate_install_extras.py

# hatch-vcs needs git history to detect version, which isn't available in Docker.
# The caller must provide the release identity explicitly.
ARG APP_VERSION
ARG INSTALL_EXTRAS=all
RUN if [ -z "${APP_VERSION}" ]; then echo "APP_VERSION build argument is required" >&2; exit 1; fi
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${APP_VERSION}

RUN INSTALL_TARGET="$(python docker/validate_install_extras.py "${INSTALL_EXTRAS}")" && \
    pip wheel --no-cache-dir --wheel-dir=/wheels "${INSTALL_TARGET}"


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
RUN mkdir -p /home/immich/.immich-memories/cache /app/output /app/.nicegui && \
    chown -R immich:immich /home/immich/.immich-memories /app/output /app/.nicegui

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV HOME=/home/immich
# Generated videos must land on the volume the compose quickstart mounts;
# the app default (~/Videos/Memories) would resolve inside the container.
ENV IMMICH_MEMORIES_OUTPUT__DIRECTORY=/app/output

# Switch to non-root user
USER immich

# Expose NiceGUI port
EXPOSE 8080

# Health check (use python instead of curl since slim image doesn't include curl)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health/live')" || exit 1

# Default command: run the UI
CMD ["immich-memories", "ui", "--host", "0.0.0.0"]
