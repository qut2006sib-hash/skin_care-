#!/usr/bin/env python3
"""Upgrade a separate existing local installation, keeping its Compose identity."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('previous_installation', type=Path)
    args = parser.parse_args()
    current = Path(__file__).resolve().parents[1]
    previous = args.previous_installation.expanduser().resolve()
    if not (previous/'docker-compose.yml').is_file() and (previous/'skin_care/docker-compose.yml').is_file():
        previous = previous/'skin_care'
    if previous == current:
        parser.error('Extract the new application into a separate directory before upgrading.')
    if not all((previous/p).is_file() for p in ('.env', 'docker-compose.yml', 'scripts/backup_restore.py')):
        parser.error('The previous directory must contain the installed app and its .env.')
    if (current/'.env').exists():
        parser.error('This new folder already has configuration. Use a freshly extracted bundle; configuration will not be overwritten.')
    config = json.loads(subprocess.check_output(['docker', 'compose', 'config', '--format', 'json'], cwd=previous))
    project = config['name']
    env = dict(os.environ, COMPOSE_PROJECT_NAME=project)
    backup = previous/'backups'/('before-0.4.0-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    def run(command, cwd=previous):
        subprocess.run(command, cwd=cwd, env=env, check=True)
    # Frontend is the only published entry point. Keep backend alive to archive media.
    run(['docker', 'compose', 'stop', 'frontend', 'worker'])
    try:
        run([sys.executable, 'scripts/backup_restore.py', 'backup', str(backup)])
        run([sys.executable, 'scripts/backup_restore.py', 'inspect', str(backup)])
    except subprocess.CalledProcessError:
        run(['docker', 'compose', 'up', '-d', 'frontend', 'worker'])
        raise
    run(['docker', 'compose', 'stop', 'backend'])
    content = (previous/'.env').read_bytes().rstrip(b'\n') + ('\nCOMPOSE_PROJECT_NAME='+project+'\n').encode()
    fd = os.open(current/'.env', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as target:
        target.write(content)
    print('Backup verified:', backup, flush=True)
    print('Reusing the existing database, media, private configuration and Compose project.', flush=True)
    run(['bash', 'START.sh'], cwd=current)
    print('Use this new application directory for future START.sh / STOP.sh commands.')


if __name__ == '__main__':
    try:
        main()
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        print('Upgrade stopped. No database was deleted. Keep the backup and original application folder. '+type(exc).__name__, file=sys.stderr)
        sys.exit(1)
