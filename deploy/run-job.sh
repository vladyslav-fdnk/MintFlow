#!/usr/bin/env bash
# Run one scheduled job and report success to its Healthchecks.io check (operations design, O5).
#
#   run-job.sh NAME COMMAND [ARGUMENT...]
#
# The check's ping URL is HEALTHCHECK_<NAME> in healthchecks.env next to this script. The ping is
# sent only when COMMAND succeeds, so a failed or missed run raises an alert. The URL is a secret:
# it is read from the file and never printed. The job's own exit status is returned.
set -uo pipefail

name="${1:?usage: run-job.sh NAME COMMAND [ARGUMENT...]}"
shift
[ "$#" -gt 0 ] || { echo "usage: run-job.sh NAME COMMAND [ARGUMENT...]" >&2; exit 64; }
directory="${MINTFLOW_DIR:-$(cd "$(dirname "$0")" && pwd)}"
cd "$directory" || exit 1

"$@"
status=$?
if [ "$status" -ne 0 ]; then
  echo "job $name failed with status $status; no ping sent" >&2
  exit "$status"
fi

url="$(grep -E "^HEALTHCHECK_${name}=" healthchecks.env 2>/dev/null | head -n 1 | cut -d= -f2-)"
if [ -z "$url" ]; then
  echo "job $name succeeded; HEALTHCHECK_${name} is not set, so no ping was sent" >&2
  exit 0
fi
if curl --fail --silent --show-error --max-time 10 --retry 3 --output /dev/null "$url" 2>/dev/null; then
  echo "job $name succeeded; ping sent"
else
  echo "job $name succeeded; the ping could not be delivered" >&2
fi
exit 0
