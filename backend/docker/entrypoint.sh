#!/bin/bash
# Prepare persistent state, then hand off to supervisor.
#
# Everything mutable lives under two mounts: $PGDATA for the database and /data
# for MinIO objects and the Redis append-only file. Both are empty on a first
# run and must be initialised before any service touches them.
set -euo pipefail

PGBIN=/usr/lib/postgresql/14/bin
PGDATA=${PGDATA:-/var/lib/postgresql/data}
POSTGRES_USER=${POSTGRES_USER:-root}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-postgres}
POSTGRES_DB=${POSTGRES_DB:-vision_db}

mkdir -p "$PGDATA" /data/minio /data/redis /app/media/clips
# A fresh named volume arrives owned by root; postgres refuses to start unless
# the data directory is its own and not group/world readable.
chown postgres:postgres "$PGDATA"
chmod 700 "$PGDATA"
chown -R redis:redis /data/redis

if [ ! -s "$PGDATA/PG_VERSION" ]; then
    echo "[init] creating a new PostgreSQL 14 cluster in $PGDATA"
    runuser -u postgres -- "$PGBIN/initdb" \
        -D "$PGDATA" \
        --encoding=UTF8 \
        --auth-local=trust \
        --auth-host=scram-sha-256

    # initdb only trusts loopback. The published port arrives from the Docker
    # bridge gateway, which is neither local nor 127.0.0.1.
    echo "host all all all scram-sha-256" >> "$PGDATA/pg_hba.conf"

    # Bind to loopback only while seeding, so nothing can connect half-built.
    runuser -u postgres -- "$PGBIN/pg_ctl" -D "$PGDATA" \
        -o "-c listen_addresses=127.0.0.1" -w start

    runuser -u postgres -- "$PGBIN/pg_ctl" -D "$PGDATA" -w stop
    echo "[init] cluster created"
else
    existing=$(cat "$PGDATA/PG_VERSION")
    if [ "$existing" != "14" ]; then
        echo "[init] ERROR: $PGDATA holds a PostgreSQL $existing cluster, but this" >&2
        echo "       image ships PostgreSQL 14. Point the volume elsewhere, or" >&2
        echo "       dump and restore: a major version cannot be read in place." >&2
        exit 1
    fi
    echo "[init] reusing the existing PostgreSQL $existing cluster in $PGDATA"
fi

# Reconcile the role and database with the environment on EVERY start, not just
# on first init. The password lives in the data directory, so a rotated
# POSTGRES_PASSWORD would otherwise never reach an existing cluster and every
# connection would fail authentication against a volume that predates it.
echo "[init] reconciling role '$POSTGRES_USER' and database '$POSTGRES_DB'"
runuser -u postgres -- "$PGBIN/pg_ctl" -D "$PGDATA" \
    -o "-c listen_addresses=127.0.0.1" -w start

# Which superuser to connect as is not knowable up front. A cluster this image
# created has `postgres`; one created by the official postgres:14 image has
# whatever POSTGRES_USER was set to then, and no `postgres` role at all. Try
# both rather than assume.
SUPERUSER=""
for candidate in "$POSTGRES_USER" postgres; do
    if runuser -u postgres -- "$PGBIN/psql" -U "$candidate" -d postgres \
            -tAc "SELECT 1" >/dev/null 2>&1; then
        SUPERUSER="$candidate"
        break
    fi
done

if [ -z "$SUPERUSER" ]; then
    echo "[init] WARNING: could not connect as a superuser, so the role and" >&2
    echo "       database were left untouched. The cluster keeps whatever" >&2
    echo "       credentials it already has; if they do not match" >&2
    echo "       POSTGRES_PASSWORD, the API will fail to connect." >&2
else
    psql_super() {
        runuser -u postgres -- "$PGBIN/psql" -U "$SUPERUSER" -v ON_ERROR_STOP=1 -d postgres "$@"
    }

    if [ -n "$(psql_super -tAc "SELECT 1 FROM pg_roles WHERE rolname = '$POSTGRES_USER'")" ]; then
        psql_super -c "ALTER ROLE \"$POSTGRES_USER\" WITH LOGIN SUPERUSER PASSWORD '$POSTGRES_PASSWORD';"
    else
        psql_super -c "CREATE ROLE \"$POSTGRES_USER\" WITH LOGIN SUPERUSER PASSWORD '$POSTGRES_PASSWORD';"
    fi

    if [ -z "$(psql_super -tAc "SELECT 1 FROM pg_database WHERE datname = '$POSTGRES_DB'")" ]; then
        runuser -u postgres -- "$PGBIN/createdb" -U "$SUPERUSER" -O "$POSTGRES_USER" "$POSTGRES_DB"
        echo "[init] created database '$POSTGRES_DB'"
    fi
    echo "[init] database ready: '$POSTGRES_DB' owned by '$POSTGRES_USER'"
fi

runuser -u postgres -- "$PGBIN/pg_ctl" -D "$PGDATA" -w stop

# Cleared on every start: the API rewrites it once the schema is pushed, and
# the worker waits for it. A stale one from the previous run would let the
# worker race ahead of a migration.
rm -f /run/schema.ready

exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
