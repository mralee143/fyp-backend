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
ENV OBJECT_MODEL_ID=${OBJECT_MODEL_ID} \
    ACTION_MODEL_ID=${ACTION_MODEL_ID} \
    VIOLENCE_MODEL_ID=${VIOLENCE_MODEL_ID} \
    YOLO_MODEL_REPO=${YOLO_MODEL_REPO}

WORKDIR /build
# yolov8n.pt is vendored in the repo; ultralytics resolves it by name and would
# otherwise download it on the first annotation pass.
COPY yolov8n.pt .
COPY docker/download-models.py .
RUN python download-models.py && rm download-models.py yolov8n.pt

# --------------------------------------------------------------------------- #
# Stage 3 — runtime
# --------------------------------------------------------------------------- #
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:/usr/lib/postgresql/14/bin:$PATH" \
    HF_HOME=/opt/models \
    YOLO_CONFIG_DIR=/opt/models/ultralytics \
    PGDATA=/var/lib/postgresql/data

# Match the uid/gid the official postgres:14 image uses, so an existing
# `postgres_data` volume is readable as-is and stays readable to that image if
# the split stack is ever brought back. Created before the package so
# postgresql-common adopts this account instead of allocating its own.
RUN groupadd -g 999 postgres && \
    useradd -u 999 -g postgres -d /var/lib/postgresql -s /bin/bash postgres

# ffmpeg for the video pipeline; libgl1/libglib2.0-0 are what OpenCV links
# against; nodejs runs the Prisma CLI at startup; supervisor drives the five
# processes. PostgreSQL 14 comes from PGDG — Debian 13 ships 17, which cannot
# read a 14 data directory.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
         https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" \
         > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        nodejs \
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

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /root/.cache/prisma-python /root/.cache/prisma-python
COPY --from=models  /opt/models /opt/models

COPY docker/supervisord.conf /etc/supervisor/supervisord.conf
COPY docker/entrypoint.sh docker/start-api.sh docker/start-worker.sh /usr/local/bin/
# Strip CRLF: these are edited on Windows, and `#!/bin/bash\r` is not a shebang.
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
                     /usr/local/bin/start-api.sh \
                     /usr/local/bin/start-worker.sh \
    && chmod +x /usr/local/bin/entrypoint.sh \
                /usr/local/bin/start-api.sh \
                /usr/local/bin/start-worker.sh

WORKDIR /app

# Application code. .dockerignore keeps the venvs, sample videos and .env out.
COPY . .

RUN mkdir -p media/clips /data/minio /data/redis

# Defaults that make `docker run` work with no configuration. Everything points
# at loopback because all five processes share this container's network
# namespace. docker-compose.yml overrides what it needs to.
ENV DATABASE_URL=postgresql://root:postgres@127.0.0.1:5432/vision_db \
    REDIS_URL=redis://127.0.0.1:6379/0 \
    MINIO_ENDPOINT=127.0.0.1:9000 \
    MINIO_ACCESS_KEY=minioadmin \
    MINIO_SECRET_KEY=minioadmin \
    MINIO_ROOT_USER=minioadmin \
    MINIO_ROOT_PASSWORD=minioadmin \
    MINIO_SECURE=false \
    MINIO_BUCKET_NAME=vision-bucket \
    MINIO_MEDIA_BUCKET=vision-media \
    POSTGRES_USER=root \
    POSTGRES_PASSWORD=postgres \
    POSTGRES_DB=vision_db \
    SECRET_KEY=dev-secret-change-me-min-32-characters-long \
    CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000

# 8000 API · 5432 Postgres · 6379 Redis · 9000 MinIO S3 · 9001 MinIO console
EXPOSE 8000 5432 6379 9000 9001

# start-period covers the first-boot initdb plus the schema push.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=5 \
    CMD curl -fsS http://localhost:8000/ || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
