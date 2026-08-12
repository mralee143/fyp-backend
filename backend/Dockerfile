# Complete backend image — API, worker, database and every model, in one.
#
#   docker compose build backend
#
# Contents:
#   PostgreSQL 14   the application database        (:5432, published as 5434)
#   Redis 8         job broker, cache, SSE pub/sub  (:6379)
#   MinIO           object storage                  (:9000, console :9001)
#   uvicorn         the FastAPI API                 (:8000)
#   arq             the analysis worker
#   ~2.5 GB of pre-downloaded weights (OWLv2, VideoMAE x2, YOLOv8n)
#
# Nothing is fetched at run time: the container analyses video with no network.
# supervisor runs the five processes; see docker/supervisord.conf.
#
# Torch defaults to the CPU wheels. For an NVIDIA GPU build with
#   --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu126
# and uncomment the `deploy.resources` block on `backend` in docker-compose.yml.

# --------------------------------------------------------------------------- #
# Stage 1 — python dependencies and the generated Prisma client
# --------------------------------------------------------------------------- #
FROM python:3.11-slim AS builder

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git: requirements.txt installs transformers straight from GitHub.
# build-essential: source-only wheels (decord2, hiredis) compile here.
# nodejs: `prisma generate` drives the Prisma CLI, which is a Node program.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        build-essential \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Torch first, from the index the build arg selects. requirements.txt lists
# torch/torchvision/torchaudio unpinned, so the next step sees them satisfied
# and leaves this build in place instead of pulling the default wheels.
RUN pip install --upgrade pip \
    && pip install --index-url "${TORCH_INDEX_URL}" torch torchvision torchaudio

COPY requirements.txt .
# imageio-ffmpeg and opencv are imported by services/ (frame extraction, clip
# cutting, box drawing) but reach the host venv only as transitive deps. Pin
# them explicitly so a dependency change upstream cannot break the image.
RUN pip install -r requirements.txt \
    && pip install imageio-ffmpeg opencv-python-headless

# Generate the Prisma client into the venv. This also downloads the query
# engine binary into ~/.cache/prisma-python, which the runtime stage copies.
COPY prisma/ ./prisma/
RUN prisma generate --schema=prisma/schema.prisma

# --------------------------------------------------------------------------- #
# Stage 2 — model weights
# --------------------------------------------------------------------------- #
# Separate stage so the ~2.5 GB download is cached independently: touching the
# application code, or even requirements.txt, does not re-fetch the weights.
FROM builder AS models

ENV HF_HOME=/opt/models \
    YOLO_CONFIG_DIR=/opt/models/ultralytics \
    HF_HUB_DISABLE_TELEMETRY=1

# Overridable so a build can pin different checkpoints; the defaults match the
# ones services/*_detection.py and ml/vid_img.py load.
ARG OBJECT_MODEL_ID=google/owlv2-base-patch16-ensemble
ARG ACTION_MODEL_ID=OPear/videomae-large-finetuned-UCF-Crime
ARG VIOLENCE_MODEL_ID=cliffer1/videomae-base-finetuned-kinetics-violence-nonviolence-tuned
ARG YOLO_MODEL_REPO=Subh775/Threat-Detection-YOLOv8n
# Qwen2.5-VL (the `model=qwen` backend) is opt-in — 16.6 GB for 7B, 7.5 GB for
# 3B. It is the last stage of a fallback that only runs when Gemini cannot
# answer, so most builds do not want it:
#   --build-arg INCLUDE_QWEN_VL=true
#   --build-arg QWEN_VL_MODEL_ID=Qwen/Qwen2.5-VL-3B-Instruct
ARG INCLUDE_QWEN_VL=false
ARG QWEN_VL_MODEL_ID=Qwen/Qwen2.5-VL-7B-Instruct
ENV OBJECT_MODEL_ID=${OBJECT_MODEL_ID} \
    ACTION_MODEL_ID=${ACTION_MODEL_ID} \
    VIOLENCE_MODEL_ID=${VIOLENCE_MODEL_ID} \
    YOLO_MODEL_REPO=${YOLO_MODEL_REPO} \
    INCLUDE_QWEN_VL=${INCLUDE_QWEN_VL} \
    QWEN_VL_MODEL_ID=${QWEN_VL_MODEL_ID}

WORKDIR /build
# yolov8n.pt is vendored in the repo; ultralytics resolves it by name and would
# otherwise download it on the first annotation pass.
COPY yolov8n.pt .
COPY docker/download-models.py .
RUN python download-models.py && rm download-models.py yolov8n.pt

# --------------------------------------------------------------------------- #
# Stage 2b — Ollama, trimmed to the CPU runtime
# --------------------------------------------------------------------------- #
# The official image carries 3.1 GB of accelerator runners: cuda_v12, cuda_v13,
# mlx_cuda_v13 and vulkan. This build is CPU-only (see TORCH_INDEX_URL), so they
# are dead weight — dropping them leaves ~60 MB of ggml CPU kernels plus the
# 38 MB binary. Pruning happens HERE rather than after the COPY because a later
# `rm` cannot shrink a layer that already shipped the files.
#
# Building for a GPU? Delete the matching cuda_* line below, or Ollama will
# silently fall back to the CPU runner.
FROM ollama/ollama:latest AS ollama-src

RUN rm -rf /usr/lib/ollama/cuda_v12 \
           /usr/lib/ollama/cuda_v13 \
           /usr/lib/ollama/mlx_cuda_v13 \
           /usr/lib/ollama/vulkan

# --------------------------------------------------------------------------- #
# Stage 2c — bake the Qwen chat model into the image
# --------------------------------------------------------------------------- #
# `ollama pull` needs a running server, so one is started, used and dropped
# inside this stage. Only $OLLAMA_MODELS is copied forward.
FROM ollama-src AS ollama-models

ARG QWEN_CHAT_MODEL=qwen2.5:3b
ENV OLLAMA_MODELS=/opt/ollama

RUN mkdir -p /opt/ollama \
    && (ollama serve &) \
    && for i in $(seq 1 60); do ollama list >/dev/null 2>&1 && break; sleep 1; done \
    && ollama pull "${QWEN_CHAT_MODEL}" \
    && ollama list

# --------------------------------------------------------------------------- #
# Stage 3 — service  (DEFAULT TARGET: the API and the worker, nothing else)
# --------------------------------------------------------------------------- #
# This is the image the split stack runs. It carries the application, the venv
# and all ~2.1 GB of model weights, but no database, cache or object store —
# those are the official postgres / redis / minio images, which get security
# updates without a 5 GB rebuild and can be backed up and restarted on their own.
#
#   docker build --target service .      -> ~2.9 GB, api + worker
#   docker build --target allinone .     -> ~5.4 GB, adds postgres/redis/minio
FROM python:3.11-slim AS service

# Must match what stage 2 cached, or ml/qwen_infer.py will try to download a
# different checkpoint on first use.
ARG QWEN_VL_MODEL_ID=Qwen/Qwen2.5-VL-7B-Instruct

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/opt/models \
    YOLO_CONFIG_DIR=/opt/models/ultralytics \
    QWEN_VL_MODEL_ID=${QWEN_VL_MODEL_ID}

# ffmpeg for the video pipeline; libgl1/libglib2.0-0 are what OpenCV links
# against; nodejs runs the Prisma CLI (`prisma db push`) at startup.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /root/.cache/prisma-python /root/.cache/prisma-python
COPY --from=models  /opt/models /opt/models

# Ollama serves the Qwen chat model behind an OpenAI-compatible API, which is
# what the agentic /chat endpoints talk to. Binary, CPU runners and the model
# weights all ship in the image, so the chat agent needs no network either.
COPY --from=ollama-src    /usr/bin/ollama /usr/bin/ollama
COPY --from=ollama-src    /usr/lib/ollama /usr/lib/ollama
COPY --from=ollama-models /opt/ollama     /opt/ollama

WORKDIR /app

# Application code. .dockerignore keeps the venvs, sample videos and .env out.
COPY . .

RUN mkdir -p media/clips

# Points at the compose service names, since in the split stack the database,
# cache and object store are separate containers on the same network.
ENV DATABASE_URL=postgresql://root:postgres@postgres:5432/vision_db \
    REDIS_URL=redis://redis:6379/0 \
    MINIO_ENDPOINT=minio:9000 \
    MINIO_ACCESS_KEY=minioadmin \
    MINIO_SECRET_KEY=minioadmin \
    MINIO_SECURE=false \
    MINIO_BUCKET_NAME=vision-bucket \
    MINIO_MEDIA_BUCKET=vision-media \
    SECRET_KEY=dev-secret-change-me-min-32-characters-long \
    CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000 \
    # Where the baked Qwen weights live, and the address `ollama serve` binds.
    # 0.0.0.0 so the split stack's api/worker containers can reach the one
    # running `ollama serve`; the chat client is pointed at it by compose.
    OLLAMA_MODELS=/opt/ollama \
    OLLAMA_HOST=0.0.0.0:11434 \
    QWEN_CHAT_BASE_URL=http://ollama:11434/v1 \
    QWEN_CHAT_ALLOW_LOCAL_FALLBACK=false \
    # services/qwen_detection.py spawns ml/qwen_infer.py as a subprocess. On the
    # host that means a separate `qwen_env` venv; in the image every dependency
    # it needs (transformers, qwen-vl-utils, decord, accelerate) is already in
    # /opt/venv, so point it there instead of at a venv that does not exist.
    QWEN_PYTHON=/opt/venv/bin/python \
    QWEN_SCRIPT=ml/qwen_infer.py

# 8000 API · 11434 Ollama
EXPOSE 8000 11434

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
    CMD curl -fsS http://localhost:8000/ || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

# --------------------------------------------------------------------------- #
# Stage 4 — allinone  (the single-container build: adds the backing services)
# --------------------------------------------------------------------------- #
FROM service AS allinone

ENV PATH="/opt/venv/bin:/usr/lib/postgresql/14/bin:$PATH" \
    PGDATA=/var/lib/postgresql/data

# Match the uid/gid the official postgres:14 image uses, so an existing
# `postgres_data` volume is readable as-is and stays readable to that image if
# the split stack is used instead. Created before the package so
# postgresql-common adopts this account instead of allocating its own.
RUN groupadd -g 999 postgres && \
    useradd -u 999 -g postgres -d /var/lib/postgresql -s /bin/bash postgres

# supervisor drives the five processes. PostgreSQL 14 comes from PGDG — Debian
# 13 ships 17, which cannot read a 14 data directory.
RUN apt-get update && apt-get install -y --no-install-recommends gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
         https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" \
         > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        supervisor \
        redis-server \
        postgresql-14 \
        locales \
    && sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

# The official postgres:14 image runs initdb under en_US.utf8 and writes that
# into postgresql.conf. A cluster created there refuses to start anywhere the
# locale does not exist — which is every slim image, until it is generated
# above. Setting LANG also makes a fresh initdb here match, so a data directory
# stays interchangeable between the two images.
ENV LANG=en_US.utf8 \
    LC_ALL=en_US.utf8

# MinIO ships as a single static binary; there is no Debian package.
RUN curl -fsSL -o /usr/local/bin/minio https://dl.min.io/server/minio/release/linux-amd64/minio \
    && chmod +x /usr/local/bin/minio

COPY docker/supervisord.conf /etc/supervisor/supervisord.conf
COPY docker/entrypoint.sh docker/start-api.sh docker/start-worker.sh /usr/local/bin/
# Strip CRLF: these are edited on Windows, and `#!/bin/bash\r` is not a shebang.
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
                     /usr/local/bin/start-api.sh \
                     /usr/local/bin/start-worker.sh \
    && chmod +x /usr/local/bin/entrypoint.sh \
                /usr/local/bin/start-api.sh \
                /usr/local/bin/start-worker.sh

RUN mkdir -p /data/minio /data/redis

# Override the service stage's addresses: here all five processes share this
# container's network namespace, so everything is on loopback.
ENV DATABASE_URL=postgresql://root:postgres@127.0.0.1:5432/vision_db \
    REDIS_URL=redis://127.0.0.1:6379/0 \
    MINIO_ENDPOINT=127.0.0.1:9000 \
    MINIO_ROOT_USER=minioadmin \
    MINIO_ROOT_PASSWORD=minioadmin \
    POSTGRES_USER=root \
    POSTGRES_PASSWORD=postgres \
    POSTGRES_DB=vision_db

# 8000 API · 5432 Postgres · 6379 Redis · 9000 MinIO S3 · 9001 MinIO console
EXPOSE 8000 5432 6379 9000 9001

# start-period covers the first-boot initdb plus the schema push.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=5 \
    CMD curl -fsS http://localhost:8000/ || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
