#!/usr/bin/env bash
# Restore one backup into a new database and check it (operations design, O7):
#   restore.sh BACKUP_NAME TARGET_DATABASE PATH_TO_AGE_PRIVATE_KEY
# Run it for the rehearsal on a scratch server, or for disaster recovery. The private key is piped
# into the tools container for this run only and never stored on the server. After restoring production data, run both retention
# cleanups before traffic returns, so data deleted since the backup is deleted again.
set -euo pipefail
cd "${MINTFLOW_DIR:-$(cd "$(dirname "$0")" && pwd)}"
backup="${1:?usage: restore.sh BACKUP_NAME TARGET_DATABASE PATH_TO_AGE_PRIVATE_KEY}"
target="${2:?usage: restore.sh BACKUP_NAME TARGET_DATABASE PATH_TO_AGE_PRIVATE_KEY}"
identity="${3:?usage: restore.sh BACKUP_NAME TARGET_DATABASE PATH_TO_AGE_PRIVATE_KEY}"
[ -r "$identity" ] || { echo "cannot read the private key at $identity" >&2; exit 2; }

compose=(docker compose -f compose.production.yaml)
"${compose[@]}" --profile tools run --rm -T backup restore "$backup" "$target" < "$identity"

echo "migrations in $target:"
set -a; source .env; set +a
"${compose[@]}" run --rm -T \
  -e MINTFLOW_DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${target}" \
  migrate alembic current
echo "If this restore replaces production data, run both retention cleanups before traffic returns:"
echo "  ./mintflow-command.sh authentication_retention_cleanup"
echo "  ./mintflow-command.sh telegram_retention_cleanup"
