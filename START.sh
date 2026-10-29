#!/usr/bin/env bash
# Run from any directory; the application and database stay in this installation.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v python3 >/dev/null; then
  echo 'Python 3 is required. On Ubuntu: sudo apt install python3' >&2
  exit 1
fi
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  echo 'Install Docker Engine with the Docker Compose plugin, then run START.sh again.' >&2
  echo 'The app includes its Python/Node dependencies in the Docker build.' >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo 'Docker is not running or your user cannot access it. Start Docker and check: docker info' >&2
  exit 1
fi

bash scripts/local_setup.sh
has_owner=$(docker compose exec -T backend python manage.py shell -c 'from django.contrib.auth import get_user_model; print("OWNER_EXISTS" if get_user_model().objects.filter(is_superuser=True,is_active=True).exists() else "OWNER_MISSING")')
if [[ "$has_owner" != *OWNER_EXISTS* ]]; then
  if [[ -t 0 && -t 1 ]]; then
    printf '\nCreate your private owner account now. No default password is included.\n'
    docker compose exec backend python manage.py createsuperuser
  else
    printf '\nCreate the owner account in a terminal:\n  cd "%s"\n  docker compose exec backend python manage.py createsuperuser\n' "$PWD"
  fi
fi

binding=$(docker compose port frontend 3000)
port=${binding##*:}
url="http://localhost:$port"
printf '\nStore: %s\nStaff: %s/staff/\nCatalog administration: %s/admin/\n' "$url" "$url" "$url"
printf 'Stop without deleting your data: bash STOP.sh\n'
if [[ "${SKINCARE_NO_BROWSER:-0}" != 1 ]] && command -v xdg-open >/dev/null && [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
  xdg-open "$url" >/dev/null 2>&1 || true
fi
