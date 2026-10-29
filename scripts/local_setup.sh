#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
command -v docker >/dev/null || { echo 'Docker with the Compose plugin is required.' >&2; exit 1; }
if [[ ! -f .env ]]; then
  python3 - <<'PY'
from pathlib import Path
import secrets,os
s=Path('.env.example').read_text().replace('replace-with-generated-password',secrets.token_urlsafe(32)).replace('replace-with-generated-secret',secrets.token_urlsafe(64))
fd=os.open('.env',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f: f.write(s)
PY
fi
docker compose up --build -d --wait --wait-timeout 180
docker compose exec -T backend python manage.py import_pharmacy_catalog
printf '\nOpen http://localhost:3000\nCreate your owner account: docker compose exec backend python manage.py createsuperuser\n'
printf 'Optional demo catalog: docker compose exec backend python manage.py seed_section15 --demo\n'
printf 'Delivery fees stay disabled until you configure actual fees in /admin/.\n'
printf '18 pharmacy-distribution products are loaded for browsing with original USD reference prices. No selling prices or stock are invented.\n'
