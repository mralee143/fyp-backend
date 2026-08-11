#!/bin/bash
# Wait until the API has pushed the schema, then become the ARQ worker.
#
# The worker writes to the same tables the API serves. Starting it before
# `prisma db push` completes means the first job dies on a missing table
# instead of waiting a few seconds for one that is about to exist.
set -euo pipefail

echo "[worker] waiting for the schema to be applied..."
until [ -f /run/schema.ready ]; do sleep 1; done

echo "[worker] starting arq"
exec arq worker.WorkerSettings
