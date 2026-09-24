#!/usr/bin/env bash
# The nightly backup (operations design, O7), run from the crontab through run-job.sh, which pings
# the backup check only when this succeeds. Settings are in backup.env next to this script.
set -euo pipefail
cd "${MINTFLOW_DIR:-$(cd "$(dirname "$0")" && pwd)}"
exec docker compose -f compose.production.yaml --profile tools run --rm -T backup backup
