#!/usr/bin/env bash
# Check the production deployment files locally (OPS-02): the Compose file validates with the
# example environment, the image builds, runs as a non-root user, and reports healthy against the
# local PostgreSQL from `docker compose up postgres`.
set -euo pipefail

cd "$(dirname "$0")/.."
image="mintflow:check"
container="mintflow-image-check"
example="deploy/production.env.example"

echo "==> Compose file with the example environment"
MINTFLOW_ENV_FILE="$PWD/$example" docker compose -f deploy/compose.production.yaml \
  --env-file "$example" config --quiet
MINTFLOW_ENV_FILE="$PWD/$example" docker compose -f deploy/compose.production.yaml \
  --env-file "$example" config | grep -q "published: \"5432\"" \
  && { echo "PostgreSQL must not publish a port" >&2; exit 1; }

echo "==> Image build"
docker build --quiet --tag "$image" . >/dev/null

echo "==> Non-root user"
uid="$(docker run --rm --entrypoint id "$image" -u)"
[ "$uid" != "0" ] || { echo "the image runs as root" >&2; exit 1; }
echo "    uid $uid"

echo "==> Health against the local PostgreSQL"
set -a; source .env; set +a
database="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@host.docker.internal:${POSTGRES_PORT:-55432}/${POSTGRES_DB}"
docker rm -f "$container" >/dev/null 2>&1 || true
trap 'docker rm -f "$container" >/dev/null 2>&1 || true' EXIT
docker run --detach --name "$container" --add-host=host.docker.internal:host-gateway \
  -e MINTFLOW_ENVIRONMENT=test -e MINTFLOW_LOG_LEVEL=WARNING \
  -e MINTFLOW_DATABASE_URL="$database" \
  -e MINTFLOW_AUTHENTICATION_RATE_LIMIT_KEY=check -e MINTFLOW_AUTHENTICATION_CSRF_SIGNING_KEY=check \
  -e MINTFLOW_AUTHENTICATION_WEB_ORIGIN=https://mintflow.example \
  -e 'MINTFLOW_AUTHENTICATION_RETURN_TARGETS=["dashboard"]' \
  "$image" >/dev/null
for _ in $(seq 1 30); do
  status="$(docker inspect --format '{{.State.Health.Status}}' "$container")"
  [ "$status" = "healthy" ] && break
  [ "$(docker inspect --format '{{.State.Running}}' "$container")" = "true" ] || break
  sleep 2
done
if [ "$status" != "healthy" ]; then
  echo "the container is $status" >&2
  docker logs "$container" >&2
  exit 1
fi
echo "    healthy"
echo "All production image checks passed."
