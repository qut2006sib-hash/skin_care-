# Operating the Homs skincare pilot

This application starts with an empty business database. A normal start or restart must never load sample stock or reset delivery fees. Run migrations and initialize role permissions as part of setup; create a staff account using Django's interactive `createsuperuser` command. There is no shipped default password.

## Explicit disposable demo initialization

For a fresh **disposable** demonstration database only:

```bash
python manage.py migrate
python manage.py init_roles
python manage.py seed_section15 --demo
python manage.py createsuperuser
```

All ten demo SKUs, prices, quantities, invoice references, label text and validity dates are synthetic. Brand names illustrate the expected assortment and do not establish product authenticity, supplier approval, composition or efficacy. Product records are labelled as demo data and never attribute a real review to Ab Noah. Replace them with reviewed catalog data before selling real products.

The command inserts missing demo records only. It preserves existing catalog fields, prices, stock, lot conditions and delivery zones. Existing SKUs are skipped entirely so running it again cannot create additional demo lots inside an existing real SKU. It does not wipe or reset a database. Expiry dates are relative to the initial execution date, then remain unchanged on later runs.

All five Homs zones are initially inactive with an unknown fee. An Owner must enter the agreed delivery fee and activate the intended zones using the staff interface. The application does not guess a real delivery price. To rehearse checkout, explicitly enter a clearly recorded fictitious fee in the disposable demo only.

## Roles and operational writes

Run `python manage.py init_roles` after migration or when role definitions change. It creates these exact groups and sets their application permissions to the supported matrix:

| Role | Catalog | Inventory | Orders, delivery and cash | Delivery zones |
|---|---|---|---|---|
| Owner | View/create/edit | View; receive/block through service-backed actions | View; perform supported service-backed actions | View/create/edit |
| Catalog_Inventory_Manager | View/create/edit | View; receive/block through service-backed actions | View | No fee changes |
| Fulfillment_Support_Staff | View | View | View; perform supported service-backed actions | View |

Users also need `is_staff` and an active account. Operational model edits are intentionally not granted by group permissions. Use the staff workflow rather than directly changing order status, reserved stock, allocations, cash or returns in the database. Staff receipt, dispatch and return actions must preserve their request references when retrying after a timeout.

## Pending-hold worker

Run a bounded pass from a scheduler:

```bash
python manage.py expire_overdue_holds --limit 500
```

Or run a dedicated long-running worker process:

```bash
python manage.py expire_overdue_holds --loop --interval 60 --limit 500
```

The worker examines only pending unconfirmed orders whose persisted deadline has passed. Each order is transitioned by the transactional service. Confirmation or cancellation that wins the lock is skipped safely. Confirmed orders are never expired by this timer. Repeating a pass cannot release a reservation twice. A delayed worker does not permit confirmation after a deadline; the confirmation service checks server time itself.

Each pass logs counts of expired, raced/skipped and failed orders. Single-pass failures produce a nonzero exit for monitoring. Unexpected failures in one order do not prevent attempting the rest; long-running mode reports failures and retries on a later pass. Run only one scheduled policy or worker configuration unless you intentionally want redundant workers; the service remains the authority when they overlap.

## Reviewed catalog import

Use `import_catalog` for CSV or JSON catalog rows. It never imports stock quantities, lots, purchase costs, cash events or order records. Receive physical stock through the receiving workflow with its supplier and validity evidence.

```bash
python manage.py import_catalog /path/catalog.json --dry-run
python manage.py import_catalog /path/catalog.json
```

Both passes apply the same validation. The dry run rolls all writes back. A failure in any row rolls back the entire file; an invalid later row cannot leave an earlier row partially imported. Limits are 5 MiB and 1,000 rows per file. CSV must be UTF-8 with unique headers; JSON is a list or an object containing exactly `products` with a list value.

Create the referenced brand and category first. Required fields are `sku`, `brand_slug`, `category_slug`, `name_ar`, `name_en`, `pack_size_label_ar` and positive integer `price_syp`. A SKU can be imported again to update catalog fields. Its product cannot silently change to a different product. Distinct variants can share `product_slug` only when their product descriptions and review fields agree. Existing order snapshots remain separate from current catalog prices.

Publication defaults to false. To publish, provide actual ingredient text, label directions/warnings, retained source reference, reviewer and nonfuture checked date. A demo reference cannot approve publication. Drafts can have empty review metadata; the command does not invent a reviewer or checked date. A recorded reference is a staff assertion: the operator must verify its source contents before publishing.

Optional fields: `product_slug`, `volume_ml`, `weight_g`, `min_remaining_validity_days`, `pao_months`, `ingredients_text`, `directions_ar`, `warnings_ar`, `claim_evidence_source`, `claim_checked_date`, `claim_reviewer`, `concern_tags`, `image_url`, `is_published`, `is_active`. Booleans are JSON booleans or `true`/`false` text in CSV. Tags use a JSON list, including inside a quoted CSV cell. Image paths must use HTTPS or an absolute local path. PAO remains separate from unopened expiry.

Minimal draft example (fictional identity; no stock or product claim):

```json
[
  {
    "sku": "EXAMPLE-30ML",
    "brand_slug": "your-existing-brand",
    "category_slug": "your-existing-category",
    "name_ar": "مسودة منتج",
    "name_en": "Product draft",
    "pack_size_label_ar": "30 مل",
    "price_syp": 10000,
    "volume_ml": 30,
    "is_published": false
  }
]
```

## End-of-day checks

Review stale confirmed orders, pending customer collections, courier-held collections, outstanding remittances, quarantined returns and blocked lots. A delivered order is not proof that cash reached the merchant. Refund approval, refund payment and resale approval are distinct facts. Record evidence and explanations using the supported workflows.

Before real orders, complete the documented backup and fresh-environment restore rehearsal and reconcile product references, stock reservations, orders, allocations, cash, refunds and images. Use only disposable accounts and data during acceptance.
