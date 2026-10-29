# Pharmacy distributor catalog

Local setup includes 18 reference listings from the user's pharmacy distribution repository. These are real source records, not demo products. They are available for browsing; they do not establish availability or a retail offer.

- Repository: [qut2006sib-hash/pharmacy-distribution](https://github.com/qut2006sib-hash/pharmacy-distribution)
- Snapshot commit: `d1384b01e6ac20e05255111783218f3e9b7e76e6`
- Source: `pharmacy_distribution/field_data.py`, `PRODUCTS` only
- Retained payload: `backend/apps/commerce/data/pharmacy_catalog.json`
- No pharmacy contacts, lead notes, orders or customer records were copied.

The upstream importer and its tests identify these prices as USD. Each original decimal value is retained exactly. No exchange rate or SYP price is inferred. Categories and short Arabic names are navigation labels derived from source names; composition, effects and label instructions are not invented.

| SKU | Original name | Source price (USD) |
|---|---|---:|
| MEDEE-VIT-C | MEDEE SERUM (Vit C) | 29.40 |
| MEDEE-EXTRA-C | MEDEE SERUM (EXTRA C) | 30.75 |
| MEDEE-HYDRA | MEDEE HYDRA | 10.40 |
| MEDEE-HA | MEDEE HA | 24.55 |
| MEDEE-RETINOL | MEDEE RETINOL | 24.55 |
| MEDEE-ROLLER-025 | MEDEE DERMA ROLLER 0.25 | 23.50 |
| MEDEE-ROLLER-05 | MEDEE DERMA ROLLER 0.5 | 23.50 |
| JUVE-AQUAFLUID-SPF50 | JUVE LAB AQUAFLUID +SPF50 | 15.60 |
| JUVE-SPF50-WHITENIZER | JUVE LAB SPF50 + WHITENIZER | 19.00 |
| JUVE-TKN-CREAM | JUVE LAB TKN CREAM | 24.60 |
| JUVE-CLEANSER-OILY | JUVE LAB CLEANSER OILY COMBINED SKIN | 9.85 |
| JUVE-CLEANSER-DRY | JUVE LAB CLEANSER DRY COMBINED SKIN | 9.85 |
| JUVE-CLEANSER-SOFT | JUVE LAB CLEANSER SOFT COMBINED SKIN | 9.85 |
| JUVE-DEODORANT | JUVE LAB DEODARANT | 9.85 |
| JUVE-DEODORANT-ANTI | JUVE LAB DEODARANT ANTIPRESPIRANT | 9.85 |
| FEME-CAPSULES-60 | FEME HAIR CAPSULES 60 | 35.80 |
| FEME-SHAMPOO | FEME HAIR SHAMPOO | 13.20 |
| FEME-ROSEMARY-OIL | FEME HAIR ROSEMARY OIL | 18.30 |

## Load and maintain

```bash
docker compose exec backend python manage.py import_pharmacy_catalog
```

The command is atomic and safe to repeat. It loads the bundled snapshot into the reference catalog only. Existing operational products, physical lots, reservations, customer orders, cash and delivery settings are untouched. Visibility changes made by the operator are retained. Source import does not fetch or execute upstream Python code.

## Prepare a product for checkout

1. Create or select the correct brand and category in `/admin/`.
2. Add a normal Product and ProductVariant with exactly the source SKU. Enter the actual pack size, verified ingredient text, label instructions/warnings and retained evidence; have the operator review the record before publishing.
3. Enter the agreed retail price as a positive integer SYP amount. A USD source price is informational and cannot serve as a SYP price.
4. Receive real stock through `/staff/` with its lot, supplier invoice, quantity and unopened-validity evidence.
5. Configure actual Homs delivery zones and fees before accepting orders.

A published local product with the same SKU takes precedence in the storefront, so the source listing is not duplicated. A draft local product does not hide the source listing. An unpublished or unstocked product cannot become buyable through the reference listing. Original transaction snapshots remain unchanged.

## Local application bundle

The Ubuntu bundle runs the complete Docker application with `bash START.sh`. It loads the same 18 source records, offers browser search and brand/category filters, and retains the staff, fulfillment and cash workflows. `install-desktop.sh` adds a launcher for the retained application directory. `STOP.sh` stops the services without deleting data. See `QUICKSTART_AR.md`. This release does not publish a customer-facing website or include an APK.
