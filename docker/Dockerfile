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

# Layer order is for the build cache: everything up to the dependency wheels
# depends only on the lockfile, the build tooling and the build arguments that
# do not change per commit. The source tree and APP_VERSION (a new value on
# every commit) come after, so a code-only change rebuilds only the app wheels.
COPY docker/uv-requirements.txt ./docker/uv-requirements.txt
COPY docker/validate_install_extras.py ./docker/validate_install_extras.py
COPY pyproject.toml README.md uv.lock ./

ARG INSTALL_EXTRAS=all
ARG TARGETARCH

# WHY constraints from uv.lock (#345): a bare `pip wheel` re-resolves every
# dependency at build time, so the published image could ship a set CI never
# tested — that is how OpenCV 5 landed in a release (#339/#340). The lock is
# exported as a pip constraints file: the image resolves to exactly the
# versions the suite ran against.
RUN pip install --no-cache-dir --require-hashes -r docker/uv-requirements.txt && \
    uv export --frozen --all-extras --no-emit-project \
        --no-emit-package immich-memories-music --no-hashes \
        --format requirements-txt -o /constraints.txt
# pip rejects unnamed (path/editable) constraint lines; only pins may remain
RUN grep -E '^[A-Za-z0-9_.[-]+==' /constraints.txt > /constraints.pins && \
    mv /constraints.pins /constraints.txt

# WHY the CPU index: torch's CUDA dependencies are pinned on `sys_platform ==
# 'linux'` with no architecture guard, so PyPI hands aarch64 the same CUDA stack
# it hands x86_64 -- measured in this image at 3.3 GB of `nvidia`, 818 MB of
# triton and a 964 MB CUDA-flavoured torch, none of which a NAS or Pi-class box
# can use. Nothing in this image runs GPU inference: the model seats are ONNX
# Runtime on the CPU provider, and the only thing left that wants torch at all
# is local Demucs stem separation.
# Measured with INSTALL_EXTRAS=all on arm64: 7.08 GB -> 2.37 GB.
# `torchvision` is deliberately absent: nothing resolves it any more, now that
# the sensitive-content detector is an ONNX graph rather than a timm model. That
# retires the failure where a CPU-index torch met a PyPI torchvision, the C++
# operators never registered, and the detector died on import with `operator
# torchvision::nms does not exist` (#805, #806).
# The wheels are built into their own directory, not /wheels, so a torch-free
# INSTALL_EXTRAS never installs them, and uv.lock is untouched so Apple
# development resolves exactly as before. The case list is what release.yml
# publishes; any other arch keeps the PyPI resolution rather than failing
# against an index that may not serve it.
RUN mkdir -p /torch-cpu && \
    case "${TARGETARCH}" in \
        amd64|arm64) \
            pip wheel --no-cache-dir --no-deps --wheel-dir=/torch-cpu \
                --index-url https://download.pytorch.org/whl/cpu \
                --constraint /constraints.txt torch ;; \
    esac

# The dependency wheels for INSTALL_EXTRAS, fetched from the lock alone so this
# layer survives a code-only commit. They go to /deps, not /wheels: the wheel
# step below still resolves the app exactly as before and only takes each file
# from here instead of the index. The torch family stays out: torch comes from
# /torch-cpu above, and the CUDA stack only follows PyPI's torch.
RUN python docker/validate_install_extras.py "${INSTALL_EXTRAS}" >/dev/null && \
    if [ "${INSTALL_EXTRAS}" = "none" ]; then extras=""; else extras="--extra ${INSTALL_EXTRAS}"; fi && \
    uv export --frozen --no-dev ${extras} --no-emit-project \
        --no-emit-package immich-memories-music --no-hashes \
        --format requirements-txt -o /deps.txt && \
    grep -E '^[A-Za-z0-9_.[-]+==' /deps.txt \
        | grep -vE '^(torch|torchaudio|triton|nvidia-[a-z0-9-]+|cuda-[a-z-]+)==' > /deps.pins && \
    pip wheel --no-cache-dir --no-deps --wheel-dir=/deps -r /deps.pins

COPY src/ ./src/
COPY packages/ ./packages/
COPY services/render-worker/ ./services/render-worker/

# hatch-vcs needs git history to detect version, which isn't available in Docker.
# The caller must provide the release identity explicitly.
ARG APP_VERSION
RUN if [ -z "${APP_VERSION}" ]; then echo "APP_VERSION build argument is required" >&2; exit 1; fi
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${APP_VERSION}

# The bundled-music package is built from the tree so the image does not depend
# on it being published yet; pip then resolves the `music` extra against it.
RUN pip wheel --no-cache-dir --wheel-dir=/wheels --constraint /constraints.txt ./packages/immich-memories-music

RUN INSTALL_TARGET="$(python docker/validate_install_extras.py "${INSTALL_EXTRAS}")" && \
    pip wheel --no-cache-dir --wheel-dir=/wheels --find-links=/wheels --find-links=/deps --find-links=/torch-cpu --constraint /constraints.txt "${INSTALL_TARGET}"

# The worker uses the app and HTTP dependencies already built above.
RUN pip wheel --no-cache-dir --no-deps --wheel-dir=/wheels ./services/render-worker


# Stage 2: Runtime image
FROM python:3.11-slim@sha256:b1b81d67b8df73bf6067191790c233ef9e598b863fc1ccf447956138ca466d99

WORKDIR /app

# Install runtime dependencies.
# WHY the VA-API drivers: FFmpeg lists `vaapi` and `qsv` under -hwaccels because
# it is compiled with them, but libva needs a backend `*_drv_video.so` to talk to
# the GPU. The image shipped none, so `vaInitialize` failed with -542398533 on
# every Linux host and hardware encoding silently never happened. All four are
# Debian main (MIT): iHD covers Gen8+ Intel including Gemini Lake and Quick Sync,
# i965 covers pre-Gen8 Intel, mesa covers AMD, and vainfo makes the next such
# failure diagnosable from inside the container. `dpkg --print-architecture`
# rather than $TARGETARCH: the latter is empty under the legacy builder, which
# would silently ship the broken image again.
# WHY libfribidi0: Pillow's wheels carry HarfBuzz but load FriBiDi from the
# system; without it an Arabic or Hebrew title is drawn unshaped (#1101).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libfribidi0 \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
         apt-get install -y --no-install-recommends \
           intel-media-va-driver \
           i965-va-driver \
           mesa-va-drivers \
           vainfo; \
       fi \
    && rm -rf /var/lib/apt/lists/*

# WHY a mount and not COPY: COPY writes every wheel into a layer of its own, and the
# `rm -rf /wheels` that used to follow the install lands in a later layer, so it
# reclaimed nothing -- the image shipped each dependency twice, once compressed as a
# wheel and once unpacked into site-packages. A mount is never committed. Measured on
# arm64 with INSTALL_EXTRAS=all: 10.5 GB -> 7.08 GB, one fewer layer.
RUN --mount=type=bind,from=builder,source=/wheels,target=/wheels \
    pip install --no-cache-dir /wheels/*.whl

# The Noto faces for every script the wheel does not carry (Arabic, Hebrew, the
# Indic scripts, Thai, CJK and more): 43 MB, 39.5 MB of it the two CJK
# collections. WHY at build time: a render never downloads a font. Each file is
# checked against its pinned SHA-256 in script_fonts.py and a mismatch fails the
# build. Outside $HOME because the config volume is mounted over it.
ENV IMMICH_MEMORIES_FONTS_DIR=/usr/share/immich-memories/fonts
RUN python -c "from immich_memories.titles.script_fonts import install_script_fonts; install_script_fonts()"

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

# Last on purpose: every RUN after an ARG sees it, so a per-commit APP_VERSION
# declared at the top of this stage would rebuild the FFmpeg layer every time.
ARG APP_VERSION
ARG VCS_REF=unknown
ARG SOURCE_URL=https://github.com/sam-dumont/immich-video-memory-generator

LABEL maintainer="Immich Memories Contributors"
LABEL description="Create video compilations from your Immich library"
LABEL org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="${SOURCE_URL}"
