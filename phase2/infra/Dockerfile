# lingren SVS API service -- Linux GPU image (Phase 2).
#
# Target: a Linux GPU server or cloud GPU host. NOT macOS: Docker on
# macOS has no GPU passthrough, so this image is never the macOS-local
# path (macOS users run the service natively with MPS/MLX).
#
# Build context MUST be the svs-api-service root (the directory that
# contains phase1/), because the service code is copied from phase1/:
#   docker build -f phase2/infra/Dockerfile -t lingren-svs:phase2 .
#
# Weights are NEVER baked into this image. They arrive only as a volume
# mounted at /models (see docker-compose.yml). Two reasons, per design
# section 8: a ~9.5 GB weight layer is expensive to distribute, and
# weight licences must be handled separately from code licences.
# The image contains only code + Python dependencies; without /models
# the API still starts (health/voices work) but SoulX synthesis fails
# loudly on missing SOULX_MODEL_PATH, which is the honest behaviour.
#
# NOTE: this Dockerfile has NOT been built (no Docker daemon on the
# dev VM -- see infra/REPORT.md). Treat it as a reviewed deliverable
# and build it on a Linux GPU host before trusting it.

FROM nvidia/cuda:12.4.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv python3-pip \
        libsndfile1 ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

WORKDIR /app

# GPU requirements first (better layer caching): API deps + torch cu124.
COPY phase2/infra/requirements-gpu.txt /tmp/requirements-gpu.txt
RUN python3 -m pip install --upgrade pip \
    && python3 -m pip install -r /tmp/requirements-gpu.txt

# Service code, copied at build time (reproducible image). For live
# development against changing code, use a bind mount instead -- see
# the commented override in docker-compose.yml -- but do not ship that
# as the production image.
COPY phase1/service/ /app/

# Model mount points (volumes at run time, NOT image layers).
VOLUME ["/models"]
ENV SOULX_DEVICE=cuda \
    SVS_DATA_DIR=/app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# Bind 0.0.0.0: inside a container, 127.0.0.1 would hide the service
# from the published port. SVS_API_KEY is REQUIRED at run time for any
# non-local deployment (compose enforces it -- see compose file).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
