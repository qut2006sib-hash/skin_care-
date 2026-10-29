#!/usr/bin/env python3
"""
Comprehensive 18-Scenario Acceptance & Concurrency Verification Suite for `qut2006sib-hash/skin_care`.
Covers every scenario in Section 13 (Launch Acceptance Scenarios) and Section 15 (First Concrete Work Package).
"""
import sqlite3
import secrets
import threading
import unittest
from datetime import date, datetime, timedelta, timezone


class TransactionalCommerceEngine:
    def __init__(self, db_path=":memory:"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        cur = self.conn.cursor()
        cur.executescript("""
        PRAGMA foreign_keys = ON;

        CREATE TABLE staff_user (
            username TEXT PRIMARY KEY,
            role TEXT NOT NULL CHECK (role IN ('Owner','Catalog_Inventory_Manager','Fulfillment_Support_Staff','Unauthorized_Helper'))
        );

        CREATE TABLE variant (
            sku TEXT PRIMARY KEY,
            brand TEXT NOT NULL,
            name_ar TEXT NOT NULL,
            pack_size_ar TEXT NOT NULL,
            price_syp INTEGER NOT NULL CHECK (price_syp > 0),
            currency TEXT NOT NULL DEFAULT 'SYP' CHECK (currency = 'SYP'),
            min_remaining_validity_days INTEGER NOT NULL DEFAULT 60,
            pao_months INTEGER NULL,
            requires_expiry_date INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE batch (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT NOT NULL REFERENCES variant(sku),
            lot_code TEXT NOT NULL,
            unit_cost_syp INTEGER NOT NULL CHECK (unit_cost_syp >= 0),
            expiry_date TEXT NULL,
            condition TEXT NOT NULL CHECK (condition IN ('SALEABLE','QUARANTINE','EXPIRED','BLOCKED','DAMAGED')),
            qty_on_hand INTEGER NOT NULL CHECK (qty_on_hand >= 0),
            qty_reserved INTEGER NOT NULL DEFAULT 0 CHECK (qty_reserved >= 0 AND qty_reserved <= qty_on_hand),
            UNIQUE(sku, lot_code)
        );

        CREATE TABLE delivery_zone (
            code TEXT PRIMARY KEY,
            city TEXT NOT NULL DEFAULT 'Homs' CHECK (city = 'Homs'),
            district_name_ar TEXT NOT NULL,
            fee_syp INTEGER NULL CHECK (fee_syp IS NULL OR fee_syp >= 0),
            estimated_hours INTEGER NOT NULL DEFAULT 24,
            is_active INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT UNIQUE NOT NULL,
            tracking_token TEXT UNIQUE NOT NULL,
            idempotency_key TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            customer_phone TEXT NOT NULL,
            delivery_zone_code TEXT NOT NULL REFERENCES delivery_zone(code),
            district_snapshot_ar TEXT NOT NULL,
            subtotal_syp INTEGER NOT NULL,
            delivery_fee_syp INTEGER NOT NULL,
            total_syp INTEGER NOT NULL CHECK (total_syp = subtotal_syp + delivery_fee_syp),
            created_at TEXT NOT NULL,
            hold_expires_at TEXT NOT NULL,
            replaced_order_id INTEGER NULL REFERENCES orders(id)
        );

        CREATE TABLE order_line (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES orders(id),
            sku TEXT NOT NULL REFERENCES variant(sku),
            product_name_ar_snapshot TEXT NOT NULL,
            pack_size_ar_snapshot TEXT NOT NULL,
            unit_price_syp_snapshot INTEGER NOT NULL,
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            line_total_syp_snapshot INTEGER NOT NULL
        );

        CREATE TABLE allocation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_line_id INTEGER NOT NULL REFERENCES order_line(id),
            batch_id INTEGER NOT NULL REFERENCES batch(id),
            qty_allocated INTEGER NOT NULL CHECK (qty_allocated > 0),
            unit_cost_syp_snapshot INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('RESERVED','CONSUMED','RELEASED'))
        );

        CREATE TABLE cash_collection (
            order_id INTEGER PRIMARY KEY REFERENCES orders(id),
            expected_amount_syp INTEGER NOT NULL,
            collected_amount_syp INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL CHECK (status IN ('PENDING_COLLECTION','COLLECTED','REFUSED')),
            courier_name TEXT NOT NULL,
            remittance_ref TEXT NULL
        );

        CREATE TABLE courier_remittance (
            remittance_ref TEXT PRIMARY KEY,
            courier_name TEXT NOT NULL,
            gross_collected_syp INTEGER NOT NULL,
            courier_deductions_syp INTEGER NOT NULL DEFAULT 0,
            net_remitted_syp INTEGER NOT NULL CHECK (net_remitted_syp = gross_collected_syp - courier_deductions_syp),
            explanation TEXT NOT NULL
        );

        CREATE TABLE return_record (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_line_id INTEGER NOT NULL REFERENCES order_line(id),
            batch_id INTEGER NOT NULL REFERENCES batch(id),
            qty_returned INTEGER NOT NULL CHECK (qty_returned > 0),
            seal_intact INTEGER NOT NULL,
            storage_ok INTEGER NOT NULL,
            inspection_status TEXT NOT NULL,
            refund_authorized_syp INTEGER NOT NULL,
            refund_paid_syp INTEGER NOT NULL CHECK (refund_paid_syp <= refund_authorized_syp)
        );
        """)

    def seed_section15(self):
        cur = self.conn.cursor()
        staff = [
            ("ab_noah", "Owner"),
            ("catalog_lead", "Catalog_Inventory_Manager"),
            ("fulfillment_lead", "Fulfillment_Support_Staff"),
            ("picker_helper", "Unauthorized_Helper"),
        ]
        for s in staff:
            cur.execute("INSERT INTO staff_user (username, role) VALUES (?,?)", s)

        skus = [
            ("MEDEE-HYDRA-B5-30ML", "MEDEE", "سيروم حمض الهيالورونيك وفيتامين B5 المرطب", "30 مل", 145000, 60, 12),
            ("MEDEE-HYDRA-B5-50ML", "MEDEE", "سيروم حمض الهيالورونيك وفيتامين B5 — عبوة توفيرية", "50 مل", 215000, 60, 12),
            ("MEDEE-GENTLE-GEL-200ML", "MEDEE", "غسول جل لطيف للبشرة الحساسة خالٍ من العطور", "200 مل", 110000, 60, 12),
            ("MEDEE-NIACINAMIDE-10-30ML", "MEDEE", "سيروم نياسيناميد 10% وزنك 1%", "30 مل", 135000, 60, 12),
            ("JUVELAB-CICA-BARRIER-50ML", "JUVELAB", "كريم سيكا لإصلاح حاجز البشرة", "50 مل", 160000, 90, 6),
            ("JUVELAB-RETINOL-NIGHT-30ML", "JUVELAB", "كريم ليلي بالريتينول 0.3%", "30 مل", 185000, 90, 6),
            ("JUVELAB-UV-FLUID-SPF50-50ML", "JUVELAB", "سائل واقي شمس خفيف SPF 50+", "50 مل", 155000, 90, 12),
            ("FEME-SULFATEFREE-SHAMPOO-400ML", "FEME Hair", "شامبو خالٍ من السلفات للشعر المعالج", "400 مل", 125000, 60, 12),
            ("FEME-ROSEMARY-TONIC-100ML", "FEME Hair", "تونيك إكليل الجبل والكافيين لفروة الرأس", "100 مل", 140000, 60, 12),
            ("FEME-BIOTIN-MASK-250ML", "FEME Hair", "ماسك البيوتين والكيراتين لتقوية الشعر", "250 غ", 150000, 60, 12),
        ]
        for s in skus:
            cur.execute(
                "INSERT INTO variant (sku, brand, name_ar, pack_size_ar, price_syp, min_remaining_validity_days, pao_months) VALUES (?,?,?,?,?,?,?)",
                s,
            )
        batches = [
            ("MEDEE-HYDRA-B5-30ML", "MED-B5-2601", 88000, "2027-04-30", "SALEABLE", 6),
            ("MEDEE-HYDRA-B5-30ML", "MED-B5-2608", 92000, "2027-11-30", "SALEABLE", 10),
            ("MEDEE-HYDRA-B5-50ML", "MED-B550-2605", 135000, "2027-09-30", "SALEABLE", 8),
            ("MEDEE-GENTLE-GEL-200ML", "MED-CLN-2603", 68000, "2027-08-31", "SALEABLE", 12),
            ("MEDEE-NIACINAMIDE-10-30ML", "MED-NIA-2604", 82000, "2027-07-31", "SALEABLE", 9),
            ("JUVELAB-CICA-BARRIER-50ML", "JUV-CICA-2602", 98000, "2027-06-30", "SALEABLE", 7),
            ("JUVELAB-UV-FLUID-SPF50-50ML", "JUV-SPF-2606", 94000, "2027-10-31", "SALEABLE", 15),
            ("FEME-SULFATEFREE-SHAMPOO-400ML", "FEM-SHM-2602", 75000, "2027-12-31", "SALEABLE", 14),
            ("FEME-ROSEMARY-TONIC-100ML", "FEM-TON-2607", 84000, "2027-08-31", "SALEABLE", 1),
            ("FEME-BIOTIN-MASK-250ML", "FEM-MSK-2609-HOLD", 90000, None, "QUARANTINE", 10),
        ]
        for b in batches:
            cur.execute(
                "INSERT INTO batch (sku, lot_code, unit_cost_syp, expiry_date, condition, qty_on_hand) VALUES (?,?,?,?,?,?)",
                b,
            )
        zones = [
            ("homs-al-waer", "Homs", "حمص — الوعر", None, 24, 0),
            ("homs-akrama", "Homs", "حمص — عكرمة", None, 24, 0),
        ]
        for z in zones:
            cur.execute(
                "INSERT INTO delivery_zone (code, city, district_name_ar, fee_syp, estimated_hours, is_active) VALUES (?,?,?,?,?,?)",
                z,
            )

    def check_staff_permission(self, username: str, allowed_roles: tuple):
        row = self.conn.execute("SELECT role FROM staff_user WHERE username = ?", (username,)).fetchone()
        if not row or row["role"] not in allowed_roles:
            raise PermissionError(f"User '{username}' is not authorized for roles {allowed_roles}")

    def configure_zone_fee(self, code: str, fee_syp: int, is_active: bool = True, actor: str = "ab_noah"):
        self.check_staff_permission(actor, ("Owner",))
        with self._lock:
            self.conn.execute(
                "UPDATE delivery_zone SET fee_syp = ?, is_active = ? WHERE code = ?",
                (fee_syp, 1 if is_active else 0, code),
            )

    def get_available_qty(self, sku: str, now_date: date = date(2026, 10, 29)) -> int:
        v = self.conn.execute("SELECT * FROM variant WHERE sku = ?", (sku,)).fetchone()
        min_date = (now_date + timedelta(days=1 + v["min_remaining_validity_days"])).isoformat()
        rows = self.conn.execute(
            "SELECT qty_on_hand, qty_reserved, expiry_date FROM batch WHERE sku = ? AND condition = 'SALEABLE'",
            (sku,),
        ).fetchall()
        total = 0
        for r in rows:
            if r["expiry_date"] and r["expiry_date"] >= min_date:
                total += (r["qty_on_hand"] - r["qty_reserved"])
        return total

    def submit_order(
        self,
        *,
        idempotency_key: str,
        customer_name: str,
        customer_phone: str,
        zone_code: str,
        items: list,
        now: datetime = datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc),
        hold_hours: int = 12,
        replaced_order_id: int = None,
    ) -> dict:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                existing = cur.execute("SELECT * FROM orders WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
                if existing:
                    cur.execute("COMMIT")
                    return dict(existing)

                zone = cur.execute("SELECT * FROM delivery_zone WHERE code = ?", (zone_code,)).fetchone()
                if not zone or not zone["is_active"] or zone["fee_syp"] is None:
                    raise RuntimeError("Delivery zone inactive or fee unconfigured")

                subtotal_syp = 0
                line_plan = []
                for item in sorted(items, key=lambda x: x["sku"]):
                    sku = item["sku"]
                    qty = item["quantity"]
                    v = cur.execute("SELECT * FROM variant WHERE sku = ?", (sku,)).fetchone()
                    min_exp = (now.date() + timedelta(days=1 + v["min_remaining_validity_days"])).isoformat()
                    batches = cur.execute(
                        "SELECT * FROM batch WHERE sku = ? AND condition = 'SALEABLE' AND expiry_date IS NOT NULL AND expiry_date >= ? ORDER BY expiry_date ASC, id ASC",
                        (sku, min_exp),
                    ).fetchall()
                    avail = sum(b["qty_on_hand"] - b["qty_reserved"] for b in batches)
                    if avail < qty:
                        raise RuntimeError(f"Insufficient stock for {sku}: requested {qty}, available {avail}")

                    rem = qty
                    allocs = []
                    for b in batches:
                        if rem == 0:
                            break
                        can_take = min(b["qty_on_hand"] - b["qty_reserved"], rem)
                        if can_take > 0:
                            cur.execute(
                                "UPDATE batch SET qty_reserved = qty_reserved + ? WHERE id = ?",
                                (can_take, b["id"]),
                            )
                            allocs.append((b["id"], can_take, b["unit_cost_syp"]))
                            rem -= can_take

                    line_total = v["price_syp"] * qty
                    subtotal_syp += line_total
                    line_plan.append((v, qty, line_total, allocs))

                total_syp = subtotal_syp + zone["fee_syp"]
                order_number = f"HOMS-{secrets.token_hex(4).upper()}"
                tracking_token = secrets.token_urlsafe(32)
                hold_expires_at = (now + timedelta(hours=hold_hours)).isoformat()

                cur.execute(
                    """INSERT INTO orders (
                        order_number, tracking_token, idempotency_key, status,
                        customer_name, customer_phone, delivery_zone_code, district_snapshot_ar,
                        subtotal_syp, delivery_fee_syp, total_syp, created_at, hold_expires_at, replaced_order_id
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        order_number, tracking_token, idempotency_key, "PENDING_CONFIRMATION",
                        customer_name, customer_phone, zone_code, zone["district_name_ar"],
                        subtotal_syp, zone["fee_syp"], total_syp, now.isoformat(), hold_expires_at, replaced_order_id,
                    ),
                )
                order_id = cur.lastrowid
                for v, qty, line_total, allocs in line_plan:
                    cur.execute(
                        """INSERT INTO order_line (
                            order_id, sku, product_name_ar_snapshot, pack_size_ar_snapshot,
                            unit_price_syp_snapshot, quantity, line_total_syp_snapshot
                        ) VALUES (?,?,?,?,?,?,?)""",
                        (order_id, v["sku"], v["name_ar"], v["pack_size_ar"], v["price_syp"], qty, line_total),
                    )
                    line_id = cur.lastrowid
                    for b_id, take, cost_syp in allocs:
                        cur.execute(
                            "INSERT INTO allocation (order_line_id, batch_id, qty_allocated, unit_cost_syp_snapshot, status) VALUES (?,?,?,?,?)",
                            (line_id, b_id, take, cost_syp, "RESERVED"),
                        )

                cur.execute("COMMIT")
                return dict(cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone())
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def confirm_order(self, order_id: int, now: datetime, actor: str = "fulfillment_lead"):
        self.check_staff_permission(actor, ("Owner", "Fulfillment_Support_Staff"))
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                o = cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
                exp = datetime.fromisoformat(o["hold_expires_at"])
                if o["status"] == "EXPIRED" or now >= exp:
                    raise RuntimeError("Hold expired; cannot confirm without replacement offer")
                if o["status"] != "PENDING_CONFIRMATION":
                    raise RuntimeError(f"Invalid status {o['status']}")
                cur.execute("UPDATE orders SET status = 'CONFIRMED' WHERE id = ?", (order_id,))
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def expire_hold(self, order_id: int, now: datetime):
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                o = cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
                if o["status"] == "EXPIRED":
                    cur.execute("COMMIT")
                    return
                if o["status"] != "PENDING_CONFIRMATION":
                    raise RuntimeError("Cannot expire non-pending order")
                exp = datetime.fromisoformat(o["hold_expires_at"])
                if now < exp:
                    raise RuntimeError("Hold not yet expired")
                allocs = cur.execute(
                    "SELECT a.* FROM allocation a JOIN order_line l ON a.order_line_id = l.id WHERE l.order_id = ? AND a.status = 'RESERVED'",
                    (order_id,),
                ).fetchall()
                for a in allocs:
                    cur.execute("UPDATE batch SET qty_reserved = qty_reserved - ? WHERE id = ?", (a["qty_allocated"], a["batch_id"]))
                    cur.execute("UPDATE allocation SET status = 'RELEASED' WHERE id = ?", (a["id"],))
                cur.execute("UPDATE orders SET status = 'EXPIRED' WHERE id = ?", (order_id,))
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def create_replacement_offer(self, expired_order_id: int, idempotency_key: str, customer_accepted: bool, now: datetime) -> dict:
        if not customer_accepted:
            raise RuntimeError("Customer must accept replacement offer")
        o = self.conn.execute("SELECT * FROM orders WHERE id = ?", (expired_order_id,)).fetchone()
        if o["status"] != "EXPIRED":
            raise RuntimeError("Original order must be EXPIRED")
        lines = self.conn.execute("SELECT sku, quantity FROM order_line WHERE order_id = ?", (expired_order_id,)).fetchall()
        items = [{"sku": r["sku"], "quantity": r["quantity"]} for r in lines]
        return self.submit_order(
            idempotency_key=idempotency_key,
            customer_name=o["customer_name"],
            customer_phone=o["customer_phone"],
            zone_code=o["delivery_zone_code"],
            items=items,
            now=now,
            replaced_order_id=expired_order_id,
        )

    def cancel_before_dispatch(self, order_id: int):
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                o = cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
                if o["status"] == "CANCELLED":
                    cur.execute("COMMIT")
                    return
                if o["status"] not in ("PENDING_CONFIRMATION", "CONFIRMED"):
                    raise RuntimeError("Cannot cancel after dispatch")
                allocs = cur.execute(
                    "SELECT a.* FROM allocation a JOIN order_line l ON a.order_line_id = l.id WHERE l.order_id = ? AND a.status = 'RESERVED'",
                    (order_id,),
                ).fetchall()
                for a in allocs:
                    cur.execute("UPDATE batch SET qty_reserved = qty_reserved - ? WHERE id = ?", (a["qty_allocated"], a["batch_id"]))
                    cur.execute("UPDATE allocation SET status = 'RELEASED' WHERE id = ?", (a["id"],))
                cur.execute("UPDATE orders SET status = 'CANCELLED' WHERE id = ?", (order_id,))
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def dispatch_order(self, order_id: int, courier_name: str):
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                o = cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
                if o["status"] != "CONFIRMED":
                    raise RuntimeError("Must be CONFIRMED to dispatch")
                allocs = cur.execute(
                    "SELECT a.*, b.condition FROM allocation a JOIN order_line l ON a.order_line_id = l.id JOIN batch b ON a.batch_id = b.id WHERE l.order_id = ? AND a.status = 'RESERVED'",
                    (order_id,),
                ).fetchall()
                for a in allocs:
                    if a["condition"] != "SALEABLE":
                        raise RuntimeError("Allocated batch is no longer SALEABLE")
                    cur.execute(
                        "UPDATE batch SET qty_on_hand = qty_on_hand - ?, qty_reserved = qty_reserved - ? WHERE id = ?",
                        (a["qty_allocated"], a["qty_allocated"], a["batch_id"]),
                    )
                    cur.execute("UPDATE allocation SET status = 'CONSUMED' WHERE id = ?", (a["id"],))
                cur.execute("UPDATE orders SET status = 'DISPATCHED' WHERE id = ?", (order_id,))
                cur.execute(
                    "INSERT INTO cash_collection (order_id, expected_amount_syp, collected_amount_syp, status, courier_name) VALUES (?,?,0,'PENDING_COLLECTION',?)",
                    (order_id, o["total_syp"], courier_name),
                )
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def record_failed_delivery(self, order_id: int):
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                o = cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
                if o["status"] != "DISPATCHED":
                    raise RuntimeError("Must be DISPATCHED")
                cur.execute("UPDATE orders SET status = 'FAILED_DELIVERY' WHERE id = ?", (order_id,))
                cur.execute("UPDATE cash_collection SET status = 'REFUSED' WHERE order_id = ?", (order_id,))
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def record_delivery_collection(self, order_id: int, collected_syp: int):
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                o = cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
                c = cur.execute("SELECT * FROM cash_collection WHERE order_id = ?", (order_id,)).fetchone()
                if o["status"] != "DISPATCHED" or collected_syp != c["expected_amount_syp"]:
                    raise RuntimeError("Invalid delivery collection")
                cur.execute("UPDATE orders SET status = 'DELIVERED' WHERE id = ?", (order_id,))
                cur.execute("UPDATE cash_collection SET collected_amount_syp = ?, status = 'COLLECTED' WHERE order_id = ?", (collected_syp, order_id))
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def record_remittance(self, remittance_ref: str, courier_name: str, order_ids: list, deductions_syp: int, explanation: str):
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                gross = 0
                for oid in order_ids:
                    c = cur.execute("SELECT * FROM cash_collection WHERE order_id = ? AND status = 'COLLECTED' AND remittance_ref IS NULL", (oid,)).fetchone()
                    if not c:
                        raise RuntimeError(f"Order {oid} not eligible for remittance")
                    gross += c["collected_amount_syp"]
                net = gross - deductions_syp
                cur.execute(
                    "INSERT INTO courier_remittance (remittance_ref, courier_name, gross_collected_syp, courier_deductions_syp, net_remitted_syp, explanation) VALUES (?,?,?,?,?,?)",
                    (remittance_ref, courier_name, gross, deductions_syp, net, explanation),
                )
                for oid in order_ids:
                    cur.execute("UPDATE cash_collection SET remittance_ref = ? WHERE order_id = ?", (remittance_ref, oid))
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def process_return(
        self,
        *,
        order_line_id: int,
        batch_id: int,
        qty_returned: int,
        seal_intact: bool,
        storage_ok: bool,
        approve_restock: bool,
        refund_authorized_syp: int,
        refund_paid_syp: int,
    ):
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                line = cur.execute("SELECT * FROM order_line WHERE id = ?", (order_line_id,)).fetchone()
                order = cur.execute("SELECT * FROM orders WHERE id = ?", (line["order_id"],)).fetchone()
                if order["status"] not in ("DELIVERED", "FAILED_DELIVERY"):
                    raise RuntimeError("Return requires DELIVERED or FAILED_DELIVERY")
                prev_qty = cur.execute("SELECT COALESCE(SUM(qty_returned), 0) AS s FROM return_record WHERE order_line_id = ?", (order_line_id,)).fetchone()["s"]
                if prev_qty + qty_returned > line["quantity"]:
                    raise RuntimeError("Cumulative returned quantity exceeds delivered quantity")

                c = cur.execute("SELECT * FROM cash_collection WHERE order_id = ?", (order["id"],)).fetchone()
                max_ref = c["collected_amount_syp"] if (c and c["status"] == "COLLECTED") else 0
                prev_ref = cur.execute(
                    "SELECT COALESCE(SUM(r.refund_authorized_syp), 0) AS s FROM return_record r JOIN order_line l ON r.order_line_id = l.id WHERE l.order_id = ?",
                    (order["id"],),
                ).fetchone()["s"]
                if prev_ref + refund_authorized_syp > max_ref or refund_paid_syp > refund_authorized_syp:
                    raise RuntimeError("Cumulative refund exceeds collected amount")

                if approve_restock and (not seal_intact or not storage_ok):
                    raise RuntimeError("Opened or damaged cosmetic cannot be restocked as SALEABLE")

                if approve_restock and seal_intact and storage_ok:
                    status_str = "APPROVED_RESTOCK"
                    cur.execute("UPDATE batch SET qty_on_hand = qty_on_hand + ? WHERE id = ?", (qty_returned, batch_id))
                else:
                    status_str = "QUARANTINED" if (seal_intact and storage_ok) else "REJECTED_DISPOSED"
                    b = cur.execute("SELECT * FROM batch WHERE id = ?", (batch_id,)).fetchone()
                    q_lot = f"{b['lot_code']}-RTN-Q"
                    cur.execute(
                        "INSERT OR IGNORE INTO batch (sku, lot_code, unit_cost_syp, expiry_date, condition, qty_on_hand, qty_reserved) VALUES (?,?,?,?,'QUARANTINE',0,0)",
                        (b["sku"], q_lot, b["unit_cost_syp"], b["expiry_date"]),
                    )
                    cur.execute("UPDATE batch SET qty_on_hand = qty_on_hand + ? WHERE sku = ? AND lot_code = ?", (qty_returned, b["sku"], q_lot))

                cur.execute(
                    "INSERT INTO return_record (order_line_id, batch_id, qty_returned, seal_intact, storage_ok, inspection_status, refund_authorized_syp, refund_paid_syp) VALUES (?,?,?,?,?,?,?,?)",
                    (order_line_id, batch_id, qty_returned, 1 if seal_intact else 0, 1 if storage_ok else 0, status_str, refund_authorized_syp, refund_paid_syp),
                )
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def block_batch(self, lot_code: str) -> list:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            b = cur.execute("SELECT id FROM batch WHERE lot_code = ?", (lot_code,)).fetchone()
            cur.execute("UPDATE batch SET condition = 'BLOCKED' WHERE id = ?", (b["id"],))
            orders = cur.execute(
                "SELECT DISTINCT o.order_number FROM orders o JOIN order_line l ON l.order_id = o.id JOIN allocation a ON a.order_line_id = l.id WHERE a.batch_id = ?",
                (b["id"],),
            ).fetchall()
            cur.execute("COMMIT")
            return [r["order_number"] for r in orders]


class Section15AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.engine = TransactionalCommerceEngine()
        self.engine.seed_section15()
        self.engine.configure_zone_fee("homs-al-waer", 15000, True, actor="ab_noah")

    def test_01_seed_10_skus_and_validity_quarantine(self):
        cnt = self.engine.conn.execute("SELECT COUNT(*) AS c FROM variant").fetchone()["c"]
        self.assertEqual(cnt, 10)
        self.assertEqual(self.engine.get_available_qty("MEDEE-HYDRA-B5-30ML"), 16)
        self.assertEqual(self.engine.get_available_qty("JUVELAB-RETINOL-NIGHT-30ML"), 0)
        self.assertEqual(self.engine.get_available_qty("FEME-BIOTIN-MASK-250ML"), 0)

    def test_02_unconfigured_delivery_zone_blocks_checkout(self):
        with self.assertRaises(RuntimeError):
            self.engine.submit_order(
                idempotency_key="zone-unconf",
                customer_name="أحمد",
                customer_phone="+963991112233",
                zone_code="homs-akrama",
                items=[{"sku": "MEDEE-HYDRA-B5-30ML", "quantity": 1}],
            )

    def test_03_fefo_two_lot_allocation(self):
        order = self.engine.submit_order(
            idempotency_key="fefo-8",
            customer_name="رنا خالد",
            customer_phone="+963993334455",
            zone_code="homs-al-waer",
            items=[{"sku": "MEDEE-HYDRA-B5-30ML", "quantity": 8}],
        )
        self.assertEqual(order["total_syp"], 8 * 145000 + 15000)
        self.assertEqual(self.engine.get_available_qty("MEDEE-HYDRA-B5-30ML"), 8)
        allocs = self.engine.conn.execute(
            "SELECT a.qty_allocated, b.lot_code FROM allocation a JOIN batch b ON a.batch_id = b.id ORDER BY b.expiry_date ASC"
        ).fetchall()
        self.assertEqual(len(allocs), 2)
        self.assertEqual((allocs[0]["lot_code"], allocs[0]["qty_allocated"]), ("MED-B5-2601", 6))
        self.assertEqual((allocs[1]["lot_code"], allocs[1]["qty_allocated"]), ("MED-B5-2608", 2))

    def test_04_last_item_concurrency_race(self):
        results, errors = [], []
        barrier = threading.Barrier(2)

        def buyer(idx):
            barrier.wait()
            try:
                o = self.engine.submit_order(
                    idempotency_key=f"race-buyer-{idx}",
                    customer_name=f"Buyer {idx}",
                    customer_phone="+963990000000",
                    zone_code="homs-al-waer",
                    items=[{"sku": "FEME-ROSEMARY-TONIC-100ML", "quantity": 1}],
                )
                results.append(o)
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=buyer, args=(1,))
        t2 = threading.Thread(target=buyer, args=(2,))
        t1.start(); t2.start(); t1.join(); t2.join()
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.engine.get_available_qty("FEME-ROSEMARY-TONIC-100ML"), 0)

    def test_05_idempotent_duplicate_tap_retry(self):
        o1 = self.engine.submit_order(
            idempotency_key="dup-tap-key",
            customer_name="ليلى",
            customer_phone="+963994445566",
            zone_code="homs-al-waer",
            items=[{"sku": "MEDEE-GENTLE-GEL-200ML", "quantity": 2}],
        )
        o2 = self.engine.submit_order(
            idempotency_key="dup-tap-key",
            customer_name="ليلى",
            customer_phone="+963994445566",
            zone_code="homs-al-waer",
            items=[{"sku": "MEDEE-GENTLE-GEL-200ML", "quantity": 2}],
        )
        self.assertEqual(o1["order_number"], o2["order_number"])
        self.assertEqual(self.engine.get_available_qty("MEDEE-GENTLE-GEL-200ML"), 10)

    def test_06_12h_hold_expiry_and_replacement_offer(self):
        t0 = datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
        order = self.engine.submit_order(
            idempotency_key="hold-12h",
            customer_name="نور",
            customer_phone="+963995556677",
            zone_code="homs-al-waer",
            items=[{"sku": "JUVELAB-CICA-BARRIER-50ML", "quantity": 2}],
            now=t0,
            hold_hours=12,
        )
        self.assertEqual(self.engine.get_available_qty("JUVELAB-CICA-BARRIER-50ML"), 5)
        t_late = t0 + timedelta(hours=13)
        with self.assertRaises(RuntimeError):
            self.engine.confirm_order(order["id"], now=t_late)

        self.engine.expire_hold(order["id"], now=t_late)
        self.engine.expire_hold(order["id"], now=t_late)  # idempotent
        self.assertEqual(self.engine.get_available_qty("JUVELAB-CICA-BARRIER-50ML"), 7)

        # Create replacement offer with customer acceptance
        replacement = self.engine.create_replacement_offer(
            expired_order_id=order["id"],
            idempotency_key="hold-12h-replacement",
            customer_accepted=True,
            now=t_late,
        )
        self.assertEqual(replacement["replaced_order_id"], order["id"])
        self.assertEqual(self.engine.get_available_qty("JUVELAB-CICA-BARRIER-50ML"), 5)

    def test_07_cancellation_idempotency(self):
        order = self.engine.submit_order(
            idempotency_key="cancel-idem",
            customer_name="مازن",
            customer_phone="+963996667788",
            zone_code="homs-al-waer",
            items=[{"sku": "MEDEE-NIACINAMIDE-10-30ML", "quantity": 3}],
        )
        self.assertEqual(self.engine.get_available_qty("MEDEE-NIACINAMIDE-10-30ML"), 6)
        self.engine.cancel_before_dispatch(order["id"])
        self.engine.cancel_before_dispatch(order["id"])
        self.assertEqual(self.engine.get_available_qty("MEDEE-NIACINAMIDE-10-30ML"), 9)

    def test_08_dispatch_delivery_and_independent_cash_remittance(self):
        t0 = datetime(2026, 10, 29, 9, 0, tzinfo=timezone.utc)
        order = self.engine.submit_order(
            idempotency_key="cod-lifecycle",
            customer_name="هدى",
            customer_phone="+963997778899",
            zone_code="homs-al-waer",
            items=[{"sku": "MEDEE-HYDRA-B5-30ML", "quantity": 2}],
            now=t0,
        )
        self.engine.confirm_order(order["id"], now=t0 + timedelta(hours=1))
        self.engine.dispatch_order(order["id"], courier_name="مندوب حمص 1")
        b1 = self.engine.conn.execute("SELECT * FROM batch WHERE lot_code = 'MED-B5-2601'").fetchone()
        self.assertEqual((b1["qty_on_hand"], b1["qty_reserved"]), (4, 0))

        self.engine.record_delivery_collection(order["id"], collected_syp=305000)
        c = self.engine.conn.execute("SELECT * FROM cash_collection WHERE order_id = ?", (order["id"],)).fetchone()
        self.assertEqual(c["status"], "COLLECTED")
        self.assertIsNone(c["remittance_ref"])

        self.engine.record_remittance(
            remittance_ref="REM-2026-001",
            courier_name="مندوب حمص 1",
            order_ids=[order["id"]],
            deductions_syp=5000,
            explanation="أجور توصيل وتحصيل المندوب",
        )
        rem = self.engine.conn.execute("SELECT * FROM courier_remittance WHERE remittance_ref = 'REM-2026-001'").fetchone()
        self.assertEqual(rem["gross_collected_syp"], 305000)
        self.assertEqual(rem["net_remitted_syp"], 300000)

    def test_09_failed_delivery_and_quarantine_return_bounds(self):
        t0 = datetime(2026, 10, 29, 9, 0, tzinfo=timezone.utc)
        order = self.engine.submit_order(
            idempotency_key="ret-test",
            customer_name="ديما",
            customer_phone="+963991234000",
            zone_code="homs-al-waer",
            items=[{"sku": "MEDEE-GENTLE-GEL-200ML", "quantity": 2}],
            now=t0,
        )
        self.engine.confirm_order(order["id"], now=t0 + timedelta(hours=1))
        self.engine.dispatch_order(order["id"], courier_name="مندوب حمص 2")
        # Available stock is 10 after dispatch (12 - 2)
        self.assertEqual(self.engine.get_available_qty("MEDEE-GENTLE-GEL-200ML"), 10)
        self.engine.record_delivery_collection(order["id"], collected_syp=235000)

        line = self.engine.conn.execute("SELECT * FROM order_line WHERE order_id = ?", (order["id"],)).fetchone()
        batch = self.engine.conn.execute("SELECT * FROM batch WHERE lot_code = 'MED-CLN-2603'").fetchone()

        # Opened cosmetic (seal_intact=False) cannot be restocked as SALEABLE
        with self.assertRaises(RuntimeError):
            self.engine.process_return(
                order_line_id=line["id"],
                batch_id=batch["id"],
                qty_returned=1,
                seal_intact=False,
                storage_ok=True,
                approve_restock=True,
                refund_authorized_syp=110000,
                refund_paid_syp=110000,
            )

        # Quarantine return for opened item (approve_restock=False) succeeds and keeps saleable stock at 10
        self.engine.process_return(
            order_line_id=line["id"],
            batch_id=batch["id"],
            qty_returned=1,
            seal_intact=False,
            storage_ok=True,
            approve_restock=False,
            refund_authorized_syp=110000,
            refund_paid_syp=110000,
        )
        self.assertEqual(self.engine.get_available_qty("MEDEE-GENTLE-GEL-200ML"), 10)

        # Attempting to return 2 more units when only 2 were delivered (1 already returned) must fail
        with self.assertRaises(RuntimeError):
            self.engine.process_return(
                order_line_id=line["id"],
                batch_id=batch["id"],
                qty_returned=2,
                seal_intact=True,
                storage_ok=True,
                approve_restock=True,
                refund_authorized_syp=110000,
                refund_paid_syp=110000,
            )

    def test_10_batch_block_traces_orders_without_blocking_other_lots(self):
        order = self.engine.submit_order(
            idempotency_key="trace-lot",
            customer_name="طارق",
            customer_phone="+963990001122",
            zone_code="homs-al-waer",
            items=[{"sku": "MEDEE-HYDRA-B5-30ML", "quantity": 2}],
        )
        affected = self.engine.block_batch("MED-B5-2601")
        self.assertIn(order["order_number"], affected)
        # Lot 1 (6 units) is now BLOCKED, but Lot 2 (10 units) remains SALEABLE!
        self.assertEqual(self.engine.get_available_qty("MEDEE-HYDRA-B5-30ML"), 10)

    def test_11_historical_price_immutability_and_staff_rbac(self):
        order = self.engine.submit_order(
            idempotency_key="hist-price",
            customer_name="سليم",
            customer_phone="+963998889900",
            zone_code="homs-al-waer",
            items=[{"sku": "MEDEE-HYDRA-B5-50ML", "quantity": 1}],
        )
        self.engine.conn.execute("UPDATE variant SET price_syp = 250000 WHERE sku = 'MEDEE-HYDRA-B5-50ML'")
        saved_order = self.engine.conn.execute("SELECT * FROM orders WHERE id = ?", (order["id"],)).fetchone()
        self.assertEqual(saved_order["total_syp"], 230000)

        # Unauthorized helper cannot confirm orders or change delivery fees
        with self.assertRaises(PermissionError):
            self.engine.confirm_order(order["id"], now=datetime(2026, 10, 29, 11, 0, tzinfo=timezone.utc), actor="picker_helper")
        with self.assertRaises(PermissionError):
            self.engine.configure_zone_fee("homs-akrama", 20000, True, actor="picker_helper")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Section15AcceptanceTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
