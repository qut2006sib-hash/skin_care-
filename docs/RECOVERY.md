# Backup and restore

The tools preserve the full PostgreSQL database (including users, orders, allocations, stock, collections and remittances) and uploaded media. The application source, `.env` secrets and external image URLs are not backed up. Keep the source release and `.env` separately in a private location. Linked external pictures must remain available at their original provider; importing them into managed media is a separate catalog task.

Backups contain customer and financial information. Store them privately; output directories are created with owner-only permissions. The SHA-256 manifest detects accidental corruption. It is not an authenticity signature, and archives should come only from your trusted backups.

## Create and verify

Run from the repository root after local setup:

```bash
python3 scripts/backup_restore.py backup backups/before-upgrade-2026-10-29
python3 scripts/backup_restore.py inspect backups/before-upgrade-2026-10-29
```

The directory must not already exist. PostgreSQL's `pg_dump` takes a consistent database snapshot. Media is captured separately. Pause catalog/media edits while backing up if media files are being changed; this tool does not coordinate a distributed snapshot between database transactions and files. Stock/order writes without image changes do not alter media.

A backup contains `database.dump` (custom-format PostgreSQL archive), `media.tar` and `manifest.json`. Copy the complete directory to your external drive, run `inspect` against the copy, and keep at least one copy away from the laptop.

## Restore into a separate staging environment

Restore never overwrites the configured database or an existing target database. It checks every payload checksum and rejects media traversal, links and special files before database creation. It also refuses an existing media destination.

```bash
python3 scripts/backup_restore.py restore backups/before-upgrade-2026-10-29 \
  --target-database skin_care_restored_20261029 \
  --media-destination backups/restored-media-20261029
```

The new database is created in the same PostgreSQL service. The media directory is staged on the host. Restoration uses `pg_restore --single-transaction --exit-on-error`, then checks that expected commerce tables exist and validates basic stock and remittance arithmetic. Output includes restored row counts for comparison. Failures attempt to remove only the new staging database created by that command. If cleanup fails, it is reported and the active database remains untouched.

After this step, the running application still uses its original database/media. Inspect the staged records before activation: compare order counts, sample order totals, lot quantities/reservations, collections, remittances, returns and images. For full rehearsal, launch a second checkout of the same source release with its own Compose project and host port, connect it to a copy of the restored database, and import the staged media into that project's volume. A new `docker compose -p ...` project has a different database service; a database created in the original project does not automatically appear there.

For activation on this single computer:

1. Stop frontend and worker (`docker compose stop frontend worker`). Keep database and backend running temporarily: the backup command reads media through the backend container. The supplied Compose file does not publish the backend port; ensure no other client or operator can write directly during this maintenance window.
2. Make and verify a fresh backup of the current active database/media, then stop the backend (`docker compose stop backend`).
3. Set `POSTGRES_DB` in `.env` to the verified restored database name. Preserve the original name for rollback.
4. Replace the application media volume contents with the verified staged media using a one-off backend container. Preserve the old media as a separate rollback copy. Never copy media while the live application is writing to it.
5. Restart backend, worker and frontend. Run the exact same source release's acceptance checks and compare stock, orders and cash records again.
6. Keep both the original database and media rollback copy until the restored application has been accepted.

Database activation is intentionally a separate explicit operating action. The script does not change `.env`, replace live volumes or delete the original database. Activation and copying across computers need a rehearsed check with your actual Docker installation before relying on this process for real records.

## Direct PostgreSQL tools and CI

For a host PostgreSQL installation, export `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB`, then use:

```bash
python3 scripts/backup_restore.py --direct backup /private/backups/new-backup
python3 scripts/backup_restore.py --direct restore /private/backups/new-backup \
  --target-database skin_care_restored_test --media-destination /private/staged-media
```

Use `--pg-bin /path/to/postgresql/bin` if tools are not on `PATH`. The PostgreSQL user needs permission to create a separate database. `pg_dump`/`pg_restore` must be compatible with the server and backup version. Passwords are supplied through environment variables and are never printed in command arguments by this tool.

The integration test runs only against real PostgreSQL when explicitly enabled:

```bash
cd backend
RUN_RECOVERY_INTEGRATION=1 python -m pytest tests/test_backup_tools.py -q
```

It performs a database/media round trip into a random new database and removes only that test database afterward. Without PostgreSQL and client binaries, the safety tests still run and the round-trip test is reported as skipped. A passing checksum check alone is not proof of a successful recovery.
