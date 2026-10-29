"""Archive safety tests do not require a running database."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'backup_restore.py'
spec = importlib.util.spec_from_file_location('skincare_backup_tools', SCRIPT)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def archive(tmp_path, *, member='products/example.txt', kind='file', content=b'image'):
    directory = tmp_path / 'backup'
    directory.mkdir()
    (directory / 'database.dump').write_bytes(b'PGDMP-test')
    with tarfile.open(directory / 'media.tar', 'w:') as tar:
        info = tarfile.TarInfo(member)
        if kind == 'symlink':
            info.type = tarfile.SYMTYPE
            info.linkname = '/outside/secret'
            tar.addfile(info)
        elif kind == 'hardlink':
            info.type = tarfile.LNKTYPE
            info.linkname = '../../outside'
            tar.addfile(info)
        else:
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    manifest = {'format_version': 1, 'source_database': 'active', 'files': {
        name: {'sha256': recovery.digest(directory / name), 'size_bytes': (directory / name).stat().st_size}
        for name in recovery.FILES
    }}
    (directory / 'manifest.json').write_text(json.dumps(manifest))
    return directory


class NeverConnect:
    database = 'active'
    direct = True
    pg_bin = None

    def query(self, *args):
        pytest.fail('Unsafe archives/targets must fail before contacting a database')


def test_valid_archive_verifies_and_extracts_to_fresh_directory(tmp_path):
    directory = archive(tmp_path)
    manifest = recovery.inspect_archive(directory)
    assert manifest['source_database'] == 'active'
    out = tmp_path / 'stage'
    out.mkdir()
    recovery.extract_media(directory / 'media.tar', out)
    assert (out / 'products/example.txt').read_bytes() == b'image'


def test_tampered_database_stops_restore_before_any_writes(tmp_path):
    directory = archive(tmp_path)
    (directory / 'database.dump').write_bytes(b'changed')
    destination = tmp_path / 'not-created'
    with pytest.raises(recovery.RecoveryError, match='Checksum'):
        recovery.restore(directory, 'restore_new', destination, NeverConnect())
    assert not destination.exists()


@pytest.mark.parametrize('name,kind', [('../outside.txt', 'file'), ('/outside.txt', 'file'), ('a\\b', 'file'), ('link', 'symlink'), ('link', 'hardlink')])
def test_media_escape_and_links_rejected_even_with_valid_checksums(tmp_path, name, kind):
    directory = archive(tmp_path, member=name, kind=kind)
    with pytest.raises(recovery.RecoveryError, match='unsafe'):
        recovery.restore(directory, 'restore_new', tmp_path / 'not-created', NeverConnect())
    assert not (tmp_path / 'not-created').exists()
    assert not (tmp_path / 'outside.txt').exists()


def test_payload_symlink_rejected(tmp_path):
    directory = archive(tmp_path)
    dump = directory / 'database.dump'
    original = tmp_path / 'original'
    dump.rename(original)
    dump.symlink_to(original)
    with pytest.raises(recovery.RecoveryError, match='symbolic link'):
        recovery.inspect_archive(directory)


@pytest.mark.parametrize('manifest', [[], None, {'format_version': 1, 'files': ['database.dump', 'media.tar']}])
def test_malformed_manifest_has_clear_error_without_database_access(tmp_path, manifest):
    directory = archive(tmp_path)
    (directory / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(recovery.RecoveryError, match='manifest'):
        recovery.restore(directory, 'new_database', tmp_path / 'stage', NeverConnect())


@pytest.mark.parametrize('target', ['active', 'BadName', 'new-db', "new';drop database active;--", '../target'])
def test_target_database_must_be_new_and_safe(tmp_path, target):
    directory = archive(tmp_path)
    with pytest.raises(recovery.RecoveryError):
        recovery.restore(directory, target, tmp_path / 'stage', NeverConnect())
    assert not (tmp_path / 'stage').exists()


def test_preexisting_media_is_preserved(tmp_path):
    directory = archive(tmp_path)
    media = tmp_path / 'media'
    media.mkdir()
    (media / 'keep.txt').write_text('keep')
    with pytest.raises(recovery.RecoveryError, match='already exists'):
        recovery.restore(directory, 'restored_new', media, NeverConnect())
    assert (media / 'keep.txt').read_text() == 'keep'


def test_concurrent_destination_creation_cannot_be_replaced(tmp_path):
    stage = tmp_path / 'stage'; stage.mkdir()
    (stage / 'proof').write_text('new')
    destination = tmp_path / 'claimed'; destination.mkdir()
    with pytest.raises(recovery.RecoveryError, match='another operation'):
        recovery.publish_directory(stage, destination)
    assert destination.is_dir()
    assert not list(destination.iterdir())
    assert (stage / 'proof').read_text() == 'new'


def test_preexisting_database_refused_without_pg_restore_or_new_directory(tmp_path):
    directory = archive(tmp_path)
    class Exists(NeverConnect):
        def query(self, *args):
            return 't'
    with pytest.raises(recovery.RecoveryError, match='already exists'):
        recovery.restore(directory, 'restored_new', tmp_path / 'stage', Exists())
    assert not (tmp_path / 'stage').exists()


def test_env_parser_never_executes_shell(tmp_path, monkeypatch):
    # CI correctly supplies credentials in the process environment. Isolate the
    # file-parser test from those intentional, higher-precedence values.
    monkeypatch.delenv('POSTGRES_PASSWORD', raising=False)
    marker = tmp_path / 'executed'
    (tmp_path / '.env').write_text(f'POSTGRES_PASSWORD=$(touch {marker})\nSTORE_NAME=ندى للعناية\n')
    env = recovery.load_environment(tmp_path)
    assert env['POSTGRES_PASSWORD'].startswith('$(touch ')
    assert not marker.exists()


def test_failed_dump_leaves_no_partial_backup_or_existing_files_changed(tmp_path):
    class Broken(NeverConnect):
        def run(self, *args, **kwargs):
            raise recovery.RecoveryError('dump failed')
    with pytest.raises(recovery.RecoveryError, match='dump failed'):
        recovery.backup(tmp_path / 'new', Broken())
    assert not (tmp_path / 'new').exists()
    assert not list(tmp_path.glob('.skincare-backup-*'))


@pytest.mark.django_db(transaction=True)
def test_postgres_backup_restore_round_trip(tmp_path):
    """Requires real PostgreSQL and its matching client tools, enabled explicitly."""
    import os
    from django.db import connection
    if connection.vendor != 'postgresql' or os.environ.get('RUN_RECOVERY_INTEGRATION') != '1':
        pytest.skip('Requires PostgreSQL + RUN_RECOVERY_INTEGRATION=1 + pg_dump/pg_restore tools')
    from django.core.management import call_command
    from apps.commerce.models import ProductVariant, InventoryBatch
    import secrets
    call_command('seed_section15', demo=True)
    settings = connection.settings_dict
    env = dict(os.environ, POSTGRES_DB=settings['NAME'], POSTGRES_USER=settings['USER'], POSTGRES_PASSWORD=settings['PASSWORD'], POSTGRES_HOST=settings['HOST'], POSTGRES_PORT=str(settings['PORT'] or 5432))
    transport = recovery.Transport(direct=True, env=env, pg_bin=os.environ.get('PG_BIN'))
    media = tmp_path / 'source-media'; media.mkdir(); (media / 'proof.txt').write_text('recovered')
    directory = tmp_path / 'verified-backup'
    recovery.backup(directory, transport, media_dir=media)
    target = 'skincare_recovery_' + secrets.token_hex(4)
    destination = tmp_path / 'staged-media'
    try:
        counts = recovery.restore(directory, target, destination, transport)
        assert counts['commerce_productvariant'] == ProductVariant.objects.count()
        assert counts['commerce_inventorybatch'] == InventoryBatch.objects.count()
        assert (destination / 'proof.txt').read_text() == 'recovered'
        # A second restore refuses to replace the first recovered DB or files.
        with pytest.raises(recovery.RecoveryError):
            recovery.restore(directory, target, tmp_path / 'another-stage', transport)
    finally:
        transport.run('dropdb', ['--maintenance-db=postgres', '--if-exists', target])
