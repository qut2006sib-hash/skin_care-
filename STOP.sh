#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
docker compose stop
printf 'Application stopped. Saved stock, orders and accounts are retained.\n'
