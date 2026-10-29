#!/usr/bin/env bash
# Registers the current folder; keep it in place after installation.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
python3 - <<'PY'
from pathlib import Path
import os

root = Path.cwd()
applications = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'applications'
applications.mkdir(parents=True, exist_ok=True)
# Escape Desktop Entry Exec quoting (not shell quoting).
def quote_arg(value):
    value = str(value).replace('%', '%%')
    return '"' + ''.join('\\' + c if c in '\\"`$' else c for c in value) + '"'

icon = str(root / 'frontend/public/icon-192.png').replace('\\', '\\\\').replace('\n', '\\n')
entry = '\n'.join([
    '[Desktop Entry]', 'Type=Application', 'Name=Skincare Local',
    'Name[ar]=العناية — متجر حمص',
    'Comment=Local store and staff workspace with 18 distributor products',
    'Exec=/bin/bash ' + quote_arg(root / 'START.sh'),
    'Icon=' + icon, 'Terminal=true', 'Categories=Office;', '',
])
target = applications / 'ab-noah-skincare.desktop'
target.write_text(entry, encoding='utf-8')
target.chmod(0o644)
print('Added Skincare Local to your applications menu.')
print('Keep this application folder in place:', root)
PY
