# C.O.R.E. Operations and Recovery Runbook

This runbook covers the monitoring and database maintenance helpers added in
Batch 12. It assumes the Compose deployment from the repository README and a
maintenance host with `pg_dump`/`pg_restore` and `neo4j-admin` installed or
available through `docker compose exec`.

## Observability

- Every HTTP response receives an `X-Request-ID` header. A safe incoming ID is
  preserved; a missing or malformed value is replaced with a UUID.
- Application logs are one JSON object per line and include timestamp, level,
  logger, message, request ID, HTTP status, and `latency_ms` where applicable.
- Migration workflow logs include the agent name, execution latency, retry
  count, and `crag_retry_count`. Credential-bearing fields and configured
  connection URLs are redacted before serialization.
- `GET /api/v1/health` probes PostgreSQL, Neo4j, and Redis concurrently. The
  successful payload includes nested component status/detail/latency fields
  plus `postgres_ms`, `neo4j_ms`, and `redis_ms`. If any required store is
  missing or unreachable, the endpoint returns the documented `503`
  `SERVICE_UNAVAILABLE` envelope with the same component diagnostics.

Do not enable shell tracing (`set -x`) around maintenance commands. Request
IDs are correlation values, not credentials, and should be included when
escalating an incident.

## Backup policy

Backups should be written to an encrypted, access-controlled volume with a
documented retention policy. The scripts read credentials from environment
variables and never put database passwords in command-line arguments. The
PostgreSQL helpers use a temporary mode `0600` `PGPASSFILE`, remove it on
exit, and do not print child-process output.

Create a restricted backup directory first:

```bash
install -d -m 700 /var/backups/core
```

### PostgreSQL backup

Set `DATABASE_URL` in the process environment, without placing it in the
command line:

```bash
export DATABASE_URL='postgresql+asyncpg://core_user:<password>@postgres:5432/core_db'
python scripts/postgres_backup.py \
  --output /var/backups/core/postgres-$(date -u +%Y%m%dT%H%M%SZ).dump
```

The output is a PostgreSQL custom-format dump suitable for `pg_restore`. Use
`--force` only when intentionally replacing an existing dump.

### Neo4j backup

Neo4j database dumps are offline administrative artifacts. Run the helper from
the Neo4j installation/container context and use an access-controlled output
directory:

```bash
export NEO4J_DATABASE=neo4j
python scripts/neo4j_backup.py \
  --output-dir /var/backups/core/neo4j-$(date -u +%Y%m%dT%H%M%SZ)
```

`NEO4J_ADMIN` may point to a specific `neo4j-admin` executable. The offline
dump command does not require an authentication password in its arguments.

## Recovery procedure

1. Declare the incident and record the UTC start time, the backup artifact
   names, the affected repository snapshot, and a representative request ID.
2. Stop API/worker traffic so no new ingestion or migration writes occur.
3. Preserve the current PostgreSQL and Neo4j data directories before replacing
   anything. Confirm the selected backup artifacts are readable and belong to
   the same recovery point.
4. Restore PostgreSQL into the intended database. This is destructive and
   requires the explicit confirmation flag:

   ```bash
   python scripts/postgres_restore.py \
     --input /var/backups/core/postgres-<timestamp>.dump \
     --yes-i-really-mean-it
   ```

5. Stop Neo4j, restore its dump into the target database, and start it again:

   ```bash
   python scripts/neo4j_restore.py \
     --input-dir /var/backups/core/neo4j-<timestamp> \
     --yes-i-really-mean-it
   ```

   The exact stop/start wrapper is deployment-specific. Do not run an offline
   database load while the target database is serving traffic.
6. Start the backend. Its entrypoint runs `alembic upgrade head`; do not
   manually edit the schema to bypass a migration failure.
7. Check `/api/v1/health` until PostgreSQL, Neo4j, and Redis all report
   `healthy`. A `503` is an actionable dependency failure, not a successful
   degraded state.
8. Verify repository row counts, the active ingestion snapshot, and a known
   graph symbol. If PostgreSQL and Neo4j snapshots do not match, mark the
   affected repository for re-ingestion before allowing analysis or migration.
9. Exercise an authenticated read-only repository/graph request, review the
   correlated JSON logs for latency and errors, then gradually resume writes.
10. Record the restore point, validation evidence, data loss window, and any
    follow-up re-ingestion in the incident record.

## Portable validation

The helpers can be syntax- and argument-checked without live services:

```bash
python scripts/postgres_backup.py --help
python scripts/postgres_restore.py --help
python scripts/neo4j_backup.py --help
python scripts/neo4j_restore.py --help
```

Live database backup/restore, Docker execution, and cold-start recovery must
be verified in a deployment environment with disposable stores. They are not
represented as local checks when those services are unavailable.
