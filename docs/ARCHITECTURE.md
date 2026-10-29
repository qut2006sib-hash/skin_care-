# Skincare Shopping App — Architecture, PostgreSQL Schema, Invariants & Acceptance Criteria

## 1. System Architecture Overview

The system uses a single modular **Django + Django REST Framework** backend backed by **PostgreSQL** as the sole operational authority, paired with an installable **Next.js Arabic RTL Progressive Web App (PWA)** storefront and **Django Admin** with server-enforced group permissions.

- **Backend**: Python 3.11+, Django 5.x, Django REST Framework (`backend/`)
- **Database**: PostgreSQL 16 (`db` service in Docker Compose)
- **Storefront**: Next.js 14 App Router PWA (`frontend/`), default `lang="ar" dir="rtl"`
- **Administration**: Django Admin with 3 RBAC roles (`Owner`, `Catalog_Inventory_Manager`, `Fulfillment_Support_Staff`)
- **Currency**: Syrian Pound (`SYP`) stored exclusively as integer minor units (`BIGINT`, 0 decimal places; `1 SYP = 1 integer unit`)
- **Launch Geography**: Homs city neighborhoods only, with admin-configured delivery fees (`fee_syp` must be explicitly configured before a zone can accept orders)

## 2. Authoritative PostgreSQL Schema (DDL Summary)

All monetary amounts (`unit_cost_syp`, `price_syp`, `fee_syp`, `subtotal_syp`, `total_syp`, `collected_amount_syp`, `remitted_amount_syp`) are stored as `BIGINT` (`PositiveBigIntegerField` / `BigIntegerField`) with `CHECK` constraints prohibiting negative values.

```sql
-- 1. Catalog & Skincare Trust
CREATE TABLE commerce_brand (
    id BIGSERIAL PRIMARY KEY,
    slug VARCHAR(64) UNIQUE NOT NULL,
    name_en VARCHAR(120) NOT NULL,
    name_ar VARCHAR(120) NOT NULL,
    is_authorized_distributor BOOLEAN NOT NULL DEFAULT FALSE,
    authorization_evidence_ref TEXT NOT NULL DEFAULT ''
);

CREATE TABLE commerce_category (
    id BIGSERIAL PRIMARY KEY,
    slug VARCHAR(64) UNIQUE NOT NULL,
    name_ar VARCHAR(120) NOT NULL,
    name_en VARCHAR(120) NOT NULL
);

CREATE TABLE commerce_product (
    id BIGSERIAL PRIMARY KEY,
    brand_id BIGINT NOT NULL REFERENCES commerce_brand(id),
    category_id BIGINT NOT NULL REFERENCES commerce_category(id),
    slug VARCHAR(120) UNIQUE NOT NULL,
    name_ar VARCHAR(200) NOT NULL,
    name_en VARCHAR(200) NOT NULL,
    description_ar TEXT NOT NULL,
    ingredients_text TEXT NOT NULL,
    directions_ar TEXT NOT NULL,
    warnings_ar TEXT NOT NULL,
    concern_tags JSONB NOT NULL DEFAULT '[]',
    claim_evidence_source TEXT NOT NULL,
    claim_checked_date DATE NOT NULL,
    claim_reviewer VARCHAR(120) NOT NULL,
    is_published BOOLEAN NOT NULL DEFAULT TRUE
);

-- Each pack size is its own distinct SKU
CREATE TABLE commerce_productvariant (
    id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES commerce_product(id),
    sku VARCHAR(64) UNIQUE NOT NULL,
    barcode VARCHAR(64) NOT NULL DEFAULT '',
    pack_size_label_ar VARCHAR(80) NOT NULL,
    volume_ml INTEGER NULL,
    weight_g INTEGER NULL,
    price_syp BIGINT NOT NULL CHECK (price_syp > 0),
    currency VARCHAR(3) NOT NULL DEFAULT 'SYP' CHECK (currency = 'SYP'),
    requires_expiry_date BOOLEAN NOT NULL DEFAULT TRUE,
    min_remaining_validity_days INTEGER NOT NULL DEFAULT 90 CHECK (min_remaining_validity_days >= 0),
    pao_months INTEGER NULL CHECK (pao_months IS NULL OR pao_months > 0),
    image_url TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

-- 2. Inventory Batches / Lots (Single Stock Authority)
CREATE TABLE commerce_inventorybatch (
    id BIGSERIAL PRIMARY KEY,
    variant_id BIGINT NOT NULL REFERENCES commerce_productvariant(id),
    lot_code VARCHAR(80) NOT NULL,
    supplier_invoice_ref VARCHAR(120) NOT NULL,
    unit_cost_syp BIGINT NOT NULL CHECK (unit_cost_syp >= 0),
    received_at TIMESTAMPTZ NOT NULL,
    expiry_date DATE NULL,
    documented_unopened_shelflife_date DATE NULL,
    storage_instructions_ar TEXT NOT NULL DEFAULT '',
    validity_evidence_ref TEXT NOT NULL DEFAULT '',
    condition VARCHAR(24) NOT NULL CHECK (
        condition IN ('SALEABLE', 'QUARANTINE', 'EXPIRED', 'BLOCKED', 'DAMAGED')
    ),
    qty_on_hand INTEGER NOT NULL CHECK (qty_on_hand >= 0),
    qty_reserved INTEGER NOT NULL DEFAULT 0 CHECK (qty_reserved >= 0 AND qty_reserved <= qty_on_hand),
    UNIQUE (variant_id, lot_code)
);

-- 3. Configurable Homs Delivery Zones
CREATE TABLE commerce_deliveryzone (
    id BIGSERIAL PRIMARY KEY,
    city VARCHAR(64) NOT NULL DEFAULT 'Homs' CHECK (city = 'Homs'),
    code VARCHAR(64) UNIQUE NOT NULL,
    district_name_ar VARCHAR(120) NOT NULL,
    district_name_en VARCHAR(120) NOT NULL,
    fee_syp BIGINT NULL CHECK (fee_syp IS NULL OR fee_syp >= 0),
    estimated_hours INTEGER NOT NULL DEFAULT 24,
    is_active BOOLEAN NOT NULL DEFAULT FALSE
);

-- 4. Orders, Immutable Line Snapshots & Lot Allocations
CREATE TABLE commerce_order (
    id BIGSERIAL PRIMARY KEY,
    order_number VARCHAR(32) UNIQUE NOT NULL,
    tracking_token VARCHAR(64) UNIQUE NOT NULL,
    idempotency_key VARCHAR(128) UNIQUE NOT NULL,
    status VARCHAR(32) NOT NULL CHECK (
        status IN (
            'PENDING_CONFIRMATION', 'CONFIRMED', 'PREPARING',
            'DISPATCHED', 'DELIVERED', 'FAILED_DELIVERY',
            'EXPIRED', 'CANCELLED'
        )
    ),
    customer_name VARCHAR(160) NOT NULL,
    customer_phone VARCHAR(40) NOT NULL,
    city VARCHAR(64) NOT NULL DEFAULT 'Homs' CHECK (city = 'Homs'),
    delivery_zone_id BIGINT NOT NULL REFERENCES commerce_deliveryzone(id),
    district_snapshot_ar VARCHAR(120) NOT NULL,
    address_landmark_ar TEXT NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'SYP' CHECK (currency = 'SYP'),
    subtotal_syp BIGINT NOT NULL CHECK (subtotal_syp >= 0),
    delivery_fee_syp BIGINT NOT NULL CHECK (delivery_fee_syp >= 0),
    discount_syp BIGINT NOT NULL DEFAULT 0 CHECK (discount_syp >= 0),
    total_syp BIGINT NOT NULL CHECK (total_syp = subtotal_syp + delivery_fee_syp - discount_syp),
    created_at TIMESTAMPTZ NOT NULL,
    hold_expires_at TIMESTAMPTZ NOT NULL,
    confirmed_at TIMESTAMPTZ NULL,
    dispatched_at TIMESTAMPTZ NULL,
    delivered_at TIMESTAMPTZ NULL,
    cancelled_at TIMESTAMPTZ NULL,
    expired_at TIMESTAMPTZ NULL,
    replaced_order_id BIGINT NULL REFERENCES commerce_order(id)
);

CREATE TABLE commerce_orderline (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES commerce_order(id) ON DELETE CASCADE,
    variant_id BIGINT NOT NULL REFERENCES commerce_productvariant(id),
    sku_snapshot VARCHAR(64) NOT NULL,
    product_name_ar_snapshot VARCHAR(200) NOT NULL,
    pack_size_ar_snapshot VARCHAR(80) NOT NULL,
    unit_price_syp_snapshot BIGINT NOT NULL CHECK (unit_price_syp_snapshot > 0),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    line_total_syp_snapshot BIGINT NOT NULL CHECK (line_total_syp_snapshot = unit_price_syp_snapshot * quantity)
);

CREATE TABLE commerce_orderbatchallocation (
    id BIGSERIAL PRIMARY KEY,
    order_line_id BIGINT NOT NULL REFERENCES commerce_orderline(id) ON DELETE CASCADE,
    batch_id BIGINT NOT NULL REFERENCES commerce_inventorybatch(id),
    qty_allocated INTEGER NOT NULL CHECK (qty_allocated > 0),
    unit_cost_syp_snapshot BIGINT NOT NULL CHECK (unit_cost_syp_snapshot >= 0),
    status VARCHAR(20) NOT NULL CHECK (status IN ('RESERVED', 'CONSUMED', 'RELEASED'))
);

-- 5. Independent Cash Collection, Courier Remittance & Returns
CREATE TABLE commerce_customercashcollection (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT UNIQUE NOT NULL REFERENCES commerce_order(id),
    expected_amount_syp BIGINT NOT NULL CHECK (expected_amount_syp >= 0),
    collected_amount_syp BIGINT NOT NULL DEFAULT 0 CHECK (collected_amount_syp >= 0),
    currency VARCHAR(3) NOT NULL DEFAULT 'SYP',
    status VARCHAR(24) NOT NULL CHECK (status IN ('PENDING_COLLECTION', 'COLLECTED', 'REFUSED', 'ADJUSTED')),
    courier_name VARCHAR(120) NOT NULL,
    collected_at TIMESTAMPTZ NULL,
    evidence_ref TEXT NOT NULL DEFAULT ''
);

CREATE TABLE commerce_courierremittance (
    id BIGSERIAL PRIMARY KEY,
    remittance_ref VARCHAR(64) UNIQUE NOT NULL,
    courier_name VARCHAR(120) NOT NULL,
    gross_collected_syp BIGINT NOT NULL CHECK (gross_collected_syp >= 0),
    courier_deductions_syp BIGINT NOT NULL DEFAULT 0 CHECK (courier_deductions_syp >= 0),
    net_remitted_syp BIGINT NOT NULL CHECK (net_remitted_syp = gross_collected_syp - courier_deductions_syp),
    explanation TEXT NOT NULL DEFAULT '',
    received_at TIMESTAMPTZ NOT NULL,
    recorded_by VARCHAR(120) NOT NULL
);

CREATE TABLE commerce_returnrecord (
    id BIGSERIAL PRIMARY KEY,
    order_line_id BIGINT NOT NULL REFERENCES commerce_orderline(id),
    batch_id BIGINT NOT NULL REFERENCES commerce_inventorybatch(id),
    qty_returned INTEGER NOT NULL CHECK (qty_returned > 0),
    seal_intact BOOLEAN NOT NULL DEFAULT FALSE,
    storage_condition_ok BOOLEAN NOT NULL DEFAULT FALSE,
    inspection_status VARCHAR(24) NOT NULL CHECK (
        inspection_status IN ('QUARANTINED', 'APPROVED_RESTOCK', 'REJECTED_DISPOSED')
    ),
    refund_authorized_syp BIGINT NOT NULL DEFAULT 0 CHECK (refund_authorized_syp >= 0),
    refund_paid_syp BIGINT NOT NULL DEFAULT 0 CHECK (refund_paid_syp >= 0 AND refund_paid_syp <= refund_authorized_syp),
    refund_method VARCHAR(64) NOT NULL DEFAULT '',
    inspected_by VARCHAR(120) NOT NULL DEFAULT ''
);
```

## 3. Critical Workflows & Data Invariants

1. **Invariant I1 — Single Stock Authority**: Available quantity for any SKU is dynamically computed as `SUM(qty_on_hand - qty_reserved)` across batches where `condition = 'SALEABLE'` and `effective_expiry_date >= expected_delivery_date + min_remaining_validity_days`. No separate aggregate stock counter exists to drift out of sync.
2. **Invariant I2 — Period After Opening (PAO) vs. Expiry**: `pao_months` is stored solely as customer guidance. Receiving stock without a dated expiry or documented unopened shelf life automatically forces `condition = 'QUARANTINE'` until reviewed.
3. **Invariant I3 — FEFO & Row-Locked Reservation**: `submit_guest_cod_order` acquires row locks on candidate batches in `ORDER BY COALESCE(expiry_date, documented_unopened_shelflife_date) ASC, id ASC FOR UPDATE`. Concurrent buyers for the last item serialize cleanly; one succeeds and the second receives an `InsufficientStockError`.
4. **Invariant I4 — Guarded Hold Expiration vs. Confirmation**: Confirmation and expiration both lock the `Order` row (`SELECT ... FOR UPDATE`) and evaluate `now < hold_expires_at` against server time. Even if the background expiry worker is delayed, a late confirmation after `hold_expires_at` is rejected and requires `create_replacement_offer_for_expired_order()`.
5. **Invariant I5 — Independent Delivery, Cash Collection, and Courier Remittance**: Order delivery (`DELIVERED`), customer payment (`CustomerCashCollection.status = 'COLLECTED'`), and merchant bank/cash receipt (`CourierRemittance`) are tracked as separate records so daily reconciliation distinguishes cash held by couriers from unexplained shortages.
6. **Invariant I6 — Quarantine-First Returns & Bounded Refunds**: Every physical return enters `QUARANTINE` first. Opened or heat-damaged cosmetics cannot transition to `SALEABLE`. Cumulative returned units per order line cannot exceed delivered units, and cumulative refunds cannot exceed customer cash collected.
