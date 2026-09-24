#!/usr/bin/env bash
# The backup round trip, locally (OPS-04): dump a throwaway database, encrypt it to a fresh age key,
# upload it to a local S3 server (SeaweedFS), then download, decrypt, and restore it into a new
# database and compare every table's row count. Also checks that the tools refuse to run without
# a public key or a private key, or into an existing database, and print no secret.
#
#   scripts/check-backup-roundtrip.sh [SOURCE_DATABASE]   (default: mintflow_screens)
set -euo pipefail

cd "$(dirname "$0")/.."
source_db="${1:-mintflow_screens}"
target_db="mintflow_restore_check"
tools="mintflow-backup-tools:check"
network="mintflow-backup-check"
minio="mintflow-backup-check-s3"  # the local S3 server
work="$(mktemp -d)"
set -a; source .env; set +a

cleanup() {
  # The scratch database goes first: dropping it needs the network.
  run "$tools" psql --dbname postgres -qc "DROP DATABASE IF EXISTS $target_db" </dev/null \
    >/dev/null 2>&1 || true
  docker rm -f "$minio" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

run() {
  docker run --rm -i --network "$network" --add-host=host.docker.internal:host-gateway \
    -e POSTGRES_HOST=host.docker.internal -e PGHOST=host.docker.internal \
    -e PGPORT="${POSTGRES_PORT:-55432}" -e PGUSER="$POSTGRES_USER" -e PGPASSWORD="$POSTGRES_PASSWORD" \
    -e POSTGRES_USER -e POSTGRES_PASSWORD -e POSTGRES_DB="$source_db" \
    -e BACKUP_S3_ENDPOINT="http://$minio:9000" -e BACKUP_S3_BUCKET=backups -e BACKUP_S3_PREFIX=nightly/ \
    -e AWS_ACCESS_KEY_ID=checkaccess -e AWS_SECRET_ACCESS_KEY=checksecretkey -e AWS_DEFAULT_REGION=us-east-1 \
    "$@"
}

counts() {
  run "$tools" psql --dbname "$1" -At -c "
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name" \
    | while read -r table; do
        # </dev/null: the container must not swallow the rest of the table list.
        echo "$table $(run "$tools" psql --dbname "$1" -Atc "SELECT count(*) FROM \"$table\"" </dev/null)"
      done
}

echo "==> Tools image"
docker build --quiet --tag "$tools" deploy/tools >/dev/null
docker network create "$network" >/dev/null 2>&1 || true
run "$tools" psql --dbname postgres -qc "DROP DATABASE IF EXISTS $target_db" </dev/null >/dev/null

echo "==> Local S3 server"
docker run --detach --name "$minio" --network "$network" \
  chrislusf/seaweedfs:latest server -s3 -s3.port=9000 -dir=/data >/dev/null
for _ in $(seq 1 60); do
  run "$tools" aws s3 ls --endpoint-url "http://$minio:9000" >/dev/null 2>&1 && break
  sleep 1
done
run "$tools" aws s3 mb s3://backups --endpoint-url "http://$minio:9000" >/dev/null

echo "==> A fresh age key pair (the private key stays in a temporary directory)"
run "$tools" age-keygen 2>/dev/null > "$work/identity"
chmod 600 "$work/identity"
recipient="$(run "$tools" age-keygen -y < "$work/identity")"

echo "==> Refusals"
if run "$tools" backup > "$work/refusal.txt" 2>&1; then echo "backup ran without a key" >&2; exit 1; fi
grep -q "BACKUP_AGE_RECIPIENT" "$work/refusal.txt"
if run -e BACKUP_AGE_RECIPIENT=notakey "$tools" backup >/dev/null 2>&1; then
  echo "backup accepted a malformed key" >&2; exit 1
fi

echo "==> Backup of $source_db"
run -e BACKUP_AGE_RECIPIENT="$recipient" "$tools" backup > "$work/backup.txt" 2>&1 \
  || { cat "$work/backup.txt" >&2; echo "the backup failed" >&2; exit 1; }
cat "$work/backup.txt"
name="$(sed -n 's/^backup \(nightly\/[^ ]*\) uploaded.*/\1/p' "$work/backup.txt")"
[ -n "$name" ] || { echo "no backup name reported" >&2; exit 1; }
run "$tools" aws s3 cp "s3://backups/$name" - --endpoint-url "http://$minio:9000" | head -c 21 \
  | grep -q "age-encryption.org/v1" || { echo "the stored backup is not age-encrypted" >&2; exit 1; }

echo "==> Restore into $target_db"
if run "$tools" restore "$name" "$target_db" </dev/null >/dev/null 2>&1; then
  echo "restore ran without a private key" >&2; exit 1
fi
run "$tools" restore "$name" "$target_db" < "$work/identity" \
  > "$work/restore.txt" 2>&1 || { cat "$work/restore.txt" >&2; echo "the restore failed" >&2; exit 1; }
tail -n +1 "$work/restore.txt" | head -3
if run "$tools" restore "$name" "$target_db" < "$work/identity" >/dev/null 2>&1; then
  echo "restore overwrote an existing database" >&2; exit 1
fi

echo "==> Row counts"
counts "$source_db" > "$work/source.txt"
counts "$target_db" > "$work/restored.txt"
[ "$(wc -l < "$work/source.txt")" -gt 10 ] || { echo "too few tables compared" >&2; exit 1; }
diff "$work/source.txt" "$work/restored.txt" || { echo "row counts differ" >&2; exit 1; }
echo "    $(wc -l < "$work/source.txt") tables, $(awk '{s+=$2} END {print s}' "$work/source.txt") rows match"

echo "==> No secret in the output"
private="$(grep '^AGE-SECRET-KEY' "$work/identity")"
for file in "$work"/refusal.txt "$work"/backup.txt "$work"/restore.txt; do
  ! grep -q -e "$private" -e checksecretkey -e "$POSTGRES_PASSWORD" "$file" \
    || { echo "a secret appeared in $(basename "$file")" >&2; exit 1; }
done
echo "The backup round trip passed."
