#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
docker compose run --rm tests python manage.py check
docker compose run --rm tests python manage.py makemigrations --check --dry-run
docker compose run --rm tests python -m pytest -q -W ignore::DeprecationWarning
