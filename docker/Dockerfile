# Immich Memories - Docker Image
# Multi-stage build for smaller final image

# Stage 1: Build dependencies
FROM python:3.11-slim AS builder

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

RUN pip install --no-cache-dir build && \
    python -m build --wheel && \
    pip wheel --no-cache-dir --wheel-dir=/wheels -r <(echo ".[face]") || \
    pip wheel --no-cache-dir --wheel-dir=/wheels .


# Stage 2: Runtime image
FROM python:3.11-slim

LABEL maintainer="Immich Memories Contributors"
LABEL description="Create video compilations from your Immich library"
LABEL version="0.1.0"

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1-mesa-glx \
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

# Create config and cache directories
RUN mkdir -p /root/.immich-memories/cache /app/output

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Expose NiceGUI port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

# Default command: run the UI
CMD ["immich-memories", "ui", "--host", "0.0.0.0"]
