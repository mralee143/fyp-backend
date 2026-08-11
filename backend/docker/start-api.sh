#!/bin/bash
# Wait for the bundled services, push the schema, then become uvicorn.
#
# Supervisor starts programs in priority order but does not wait for them to be
# usable, so readiness is checked here rather than assumed.
set -euo pipefail

echo "[api] waiting for postgres..."
# -U/-d are only to keep the log clean: this runs as root, so the defaults make
# pg_isready ask for database "root", and postgres logs a FATAL for every probe.
# Readiness itself does not depend on them.
until /usr/lib/postgresql/14/bin/pg_isready -h 127.0.0.1 -p 5432 -q \
        -U "${POSTGRES_USER:-root}" -d postgres; do
    sleep 1
done

echo "[api] waiting for minio..."
until curl -fsS http://127.0.0.1:9000/minio/health/live >/dev/null 2>&1; do sleep 1; done

echo "[api] waiting for redis..."
until redis-cli -h 127.0.0.1 ping >/dev/null 2>&1; do sleep 1; done

# Idempotent: on an existing database this is a no-op, on a fresh volume it
# creates every table. Runs here so a first boot needs no manual migration.
echo "[api] applying prisma schema..."
prisma db push --schema=prisma/schema.prisma --skip-generate --accept-data-loss

# Releases the worker (see start-worker.sh).
touch /run/schema.ready

echo "[api] starting uvicorn on :8000"
exec uvicorn main:app --host 0.0.0.0 --port 8000
