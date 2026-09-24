#!/usr/bin/env bash
# Run one MintFlow command in a fresh container of the app image:
#   mintflow-command.sh refresh_exchange_rates
set -euo pipefail
cd "${MINTFLOW_DIR:-$(cd "$(dirname "$0")" && pwd)}"
exec docker compose -f compose.production.yaml run --rm -T app python -m "mintflow.commands.${1:?command name}"
