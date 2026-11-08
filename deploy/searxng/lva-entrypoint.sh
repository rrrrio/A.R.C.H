#!/bin/sh
set -eu

secret=${SEARXNG_SECRET:-}
if [ "${#secret}" -lt 32 ]; then
    echo "SEARXNG_SECRET must be provisioned before starting the search service." >&2
    exit 64
fi

exec /usr/local/searxng/entrypoint.sh "$@"
