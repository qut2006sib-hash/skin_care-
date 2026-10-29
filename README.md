# متجر العناية — Homs skincare local MVP

Arabic-first mobile storefront and staff workspace for a single seller in Homs. Django/DRF + PostgreSQL 16 hold the operational records; Next.js serves the customer experience. Setup includes the 18 products from `pharmacy-distribution` for browsing, with their original USD reference prices. Selling prices, stock and delivery stay unconfigured until entered by the operator.

## Run on Ubuntu

For the downloadable application bundle, use `bash START.sh`; it checks Docker, loads the products, prompts for the first owner account and opens the store. Optionally run `bash install-desktop.sh` to add it to the Ubuntu applications menu. Arabic instructions: [QUICKSTART_AR.md](QUICKSTART_AR.md). `bash STOP.sh` stops the app without deleting data.

Install Docker Engine with the Docker Compose plugin and Python 3. Keep this project in one folder; business data lives in Docker volumes, not the source tree.

```bash
bash scripts/local_setup.sh
docker compose exec backend python manage.py createsuperuser
```

Open **http://localhost:3000** for customers, **http://localhost:3000/staff/** for operations, and **http://localhost:3000/admin/** for catalog/user setup. Use the owner account you created; no default password ships. The setup script generates secrets only when `.env` does not exist. It never resets a database or seeds stock on restart.

Setup imports the bundled supplier catalog automatically and safely on repeat runs. It preserves local catalog edits and stock. The imported products are reference listings: the source has no actual stock, verified pack sizes, label ingredients or SYP selling prices. They are searchable and browsable, and cannot be checked out. A published operational product with the same SKU replaces its reference listing after the operator enters reviewed product details, an actual SYP price and receives physical stock. Source prices are never silently converted into SYP.

To load just the catalog on an existing installation:

```bash
docker compose exec backend python manage.py import_pharmacy_catalog
```

See [docs/PHARMACY_CATALOG.md](docs/PHARMACY_CATALOG.md) for the source snapshot, all 18 SKUs and activation steps.

For disposable demo data only, enable `DEMO_MODE=1` in that disposable installation before using:

```bash
docker compose exec backend python manage.py seed_section15 --demo
```

The ten sample SKUs are visibly synthetic; their product information, prices, invoice references and stock are fixtures. Delivery zones remain inactive with no fee. Log in to `/staff/`, open **أحياء التوصيل**, enter an actual agreed fee/duration and enable the desired zone. Never mistake a demo fee for your courier's fee.

For business use, set `DEMO_MODE=0` in `.env`, restart the services, and add reviewed products through Admin or the validated import command. Configure actual policy/support details before accepting customers; the help page explains the local pilot without invented business promises. Keep unverified products unpublished. Demo records remain demo records even if the banner setting changes.

## What works in this candidate

- Arabic RTL, responsive catalog, search/brand/category/availability filters and product detail.
- Editable persistent cart; complete products-plus-delivery total; guest COD checkout.
- Stable checkout attempt after a lost response, explicit changed-price review, private tracking and locally saved order links.
- Staff login, catalog/fulfillment roles, batch receipt, validity quarantine review, FEFO allocation, picklists, confirmation, cancellation, dispatch and delivery outcome.
- Separate customer collection and courier remittance, bounded returns, physical quarantine then inspection, and later refund payment.
- Stock movements/audit records, scheduled unconfirmed-hold expiry, explicit non-destructive demo initialization, validated CSV/JSON import.
- PostgreSQL backup, checksums and restore into a new database with separate staged media. Active data is never overwritten automatically.

The storefront manifest includes install icons. The service worker caches only the offline fallback, never catalog/API/order/staff data. Installability requires a browser-supported secure context; a LAN HTTP address may not support installation. Browsing and ordering remain online web features.

## Ordinary use

`/admin/`: add brands/categories and reviewed catalog data; manage users, `is_staff`, and their role group. Operational balances and order states are read-only here. Use `/staff/` actions to change them through the inventory service.

`/staff/`: receive goods, inspect pending orders, print exact lots, dispatch, record delivery and collection, settle courier cash, receive returns into quarantine, inspect them and record refunds. A stock count is not customer cash, and delivery is not courier settlement.

The worker runs every minute by default. Unconfirmed orders have a configurable 12-hour hold; confirmed orders are excluded. After an expired order, staff must arrange a fresh customer-accepted order instead of reviving the old reservation.

```bash
docker compose logs --tail=100 worker
docker compose restart backend worker frontend
```

Restart preserves saved data. **Do not use `docker compose down -v` with data you want to keep.**

For Android on your trusted local network, set `BIND_ADDRESS=0.0.0.0`, add your laptop's LAN IP to `DJANGO_ALLOWED_HOSTS`, and add its full `http://IP:3000` origin to `CSRF_TRUSTED_ORIGINS` in `.env`. Recreate services with `docker compose up -d`. Open `http://IP:3000` on the phone. Avoid exposing the development backend to the public internet.

## Tests

```bash
bash scripts/run_acceptance.sh
```

That path uses the real PostgreSQL database service and includes last-item, same-key, expiry/confirmation, return/checkout and lock-wait races. CI also runs the PostgreSQL restore roundtrip and a locked frontend production build. A passing standalone SQLite simulator is not PostgreSQL acceptance.

Fast diagnostics, with deliberately separate SQLite settings:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
python -m pytest --ds=config.test_settings -q
```

The PostgreSQL-only tests skip in diagnostic mode. `config.test_settings` must never be used for real customer data. Browser tests in `tests/browser_mvp.py` use intercepted responses to prove timeout/retry/offline/price-review UI behavior; live browser integration is recorded separately in the acceptance report.

## Backup and restore

Stop the worker before a coordinated backup and avoid editing media during it; keep backend running because media is read from its volume. The command writes a private backup directory containing a PostgreSQL dump, media archive and checksum manifest.

```bash
python3 scripts/backup_restore.py backup backups/my-backup
python3 scripts/backup_restore.py inspect backups/my-backup
python3 scripts/backup_restore.py restore backups/my-backup --target-database skin_care_restored --media-destination backups/restored-media
```

Restore is staged. Read [docs/RECOVERY.md](docs/RECOVERY.md) before activation; verify the restored copy, then switch the database/media configuration. Do not paste older source-archive push commands over a populated remote.

See [docs/OPERATIONS.md](docs/OPERATIONS.md) for import examples and the role matrix, and [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) for the evidence, tested versions and remaining local pilot gates.
