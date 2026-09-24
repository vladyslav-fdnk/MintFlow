#!/usr/bin/env bash
# Succeed only while the root file system is below the limit (default 80% used), so run-job.sh
# pings the disk check only then and a filling disk raises an alert (operations design, O8).
set -euo pipefail
limit="${MINTFLOW_DISK_LIMIT_PERCENT:-80}"
used="$(df --output=pcent "${MINTFLOW_DISK_PATH:-/}" | tail -n 1 | tr -dc '0-9')"
if [ "$used" -ge "$limit" ]; then
  echo "disk ${used}% used, at or above ${limit}%" >&2
  exit 1
fi
echo "disk ${used}% used"
