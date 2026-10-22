#!/bin/sh

set -eu

# The API process is not started until the relational schema is current. Compose
# already waits for the store healthchecks; Alembic is the final cold-start gate.
alembic upgrade head

exec "$@"
