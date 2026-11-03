#!/bin/sh
set -eu

if [ "${WAIT_FOR_DATABASE:-1}" = "1" ]; then
    python scripts/wait_for_database.py
fi

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "applying database migrations"
    alembic upgrade head
fi

exec "$@"