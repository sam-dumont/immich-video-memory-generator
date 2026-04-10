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
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml README.md ./
COPY src/ ./src/

# hatch-vcs needs git history to detect version, which isn't available in Docker.
# Use SETUPTOOLS_SCM_PRETEND_VERSION to provide the version explicitly.
ARG SETUPTOOLS_SCM_PRETEND_VERSION=0.2.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${SETUPTOOLS_SCM_PRETEND_VERSION}

RUN pip install --no-cache-dir build && \
    python -m build --wheel && \
    (pip wheel --no-cache-dir --wheel-dir=/wheels ".[all]" || \
     pip wheel --no-cache-dir --wheel-dir=/wheels .)


# Stage 2: Runtime image
FROM python:3.11-slim@sha256:b1b81d67b8df73bf6067191790c233ef9e598b863fc1ccf447956138ca466d99

LABEL maintainer="Immich Memories Contributors"
LABEL description="Create video compilations from your Immich library"
LABEL version="0.2.0"

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
COPY --from=builder /app/dist/*.whl /wheels/

# Install the application
RUN pip install --no-cache-dir /wheels/*.whl && \
    rm -rf /wheels

# Create non-root user for security
RUN groupadd -r immich && useradd -r -g immich -d /home/immich -m immich

# Create config, cache, and NiceGUI state directories with correct ownership
RUN mkdir -p /home/immich/.immich-memories/cache /app/output /app/.nicegui && \
    chown -R immich:immich /home/immich/.immich-memories /app/output /app/.nicegui

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV HOME=/home/immich

# Switch to non-root user
USER immich

# Expose NiceGUI port
EXPOSE 8080

# Health check (use python instead of curl since slim image doesn't include curl)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# Default command: run the UI
CMD ["immich-memories", "ui", "--host", "0.0.0.0"]
