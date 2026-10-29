"""Regression tests execute the real Django service.

Race tests require PostgreSQL and independent connections; they are explicitly
skipped by the optional SQLite diagnostic configuration.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.db import close_old_connections, connection, connections
from django.db.models import Sum
from django.utils import timezone

from apps.commerce.exceptions import (
    BatchEligibilityError, HoldExpiredError, IdempotencyConflictError,
    InsufficientStockError, InvalidStateTransitionError, PriceChangedError, ReturnLimitExceededError,
    ValidationDomainError,
)
from apps.commerce.models import (
    AllocationStatus, AuditLog, BatchCondition, Brand, Category, DeliveryZone,
    InventoryBatch, Order, OrderBatchAllocation, OrderStatus, Product,
    ProductVariant, ReturnInspectionStatus, ReturnRecord, StockMovement,
)
from apps.commerce.services import InventoryOrderService as Service

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def inventory():
    brand = Brand.objects.create(slug="demo", name_en="Demo", name_ar="تجريبي")
    category = Category.objects.create(slug="care", name_en="Care", name_ar="العناية")
    product = Product.objects.create(brand=brand, category=category, slug="demo-product", name_ar="منتج تجريبي", name_en="Demo", is_published=True)
    variant = ProductVariant.objects.create(product=product, sku="DEMO-30", pack_size_label_ar="30 مل", price_syp=100, min_remaining_validity_days=30)
    zone = DeliveryZone.objects.create(code="test-zone", district_name_ar="حي تجريبي", district_name_en="Demo", fee_syp=10, is_active=True)
    expiry = timezone.localdate()+timedelta(days=180)
    batch = Service.receive_batch(sku=variant.sku, lot_code="LOT-1", supplier_invoice_ref="INV-1", qty_received=10, unit_cost_syp=40, expiry_date=expiry, receipt_ref="RECEIPT-1")
    return variant, batch, zone


def submit(inventory, key="checkout-one", quantity=1, **overrides):
    variant, batch, zone = inventory
    payload = dict(idempotency_key=key, customer_name="زبون تجريبي", customer_phone="+963900000000", delivery_zone_code=zone.code, address_landmark_ar="عنوان تجريبي", items=[dict(sku=variant.sku, quantity=quantity)])
    payload.update(overrides)
    return Service.submit_guest_cod_order(**payload)


def deliver(inventory, quantity=1, key="checkout-deliver"):
    order = submit(inventory, key=key, quantity=quantity)
    Service.confirm_order(order_id=order.pk, actor="owner")
    Service.dispatch_order(order_id=order.pk, courier_name="Courier", actor="owner")
    Service.record_delivery_and_customer_collection(order_id=order.pk, collected_amount_syp=order.total_syp, evidence_ref="CASH-1", actor="owner")
    return order


def test_checkout_snapshot_and_same_key_retry(inventory):
    order = submit(inventory, quantity=2, expected_total_syp=210)
    repeated = submit(inventory, quantity=2, expected_total_syp=210)
    assert repeated.pk == order.pk
    batch = InventoryBatch.objects.get(pk=inventory[1].pk)
    assert (batch.qty_on_hand, batch.qty_reserved) == (10, 2)
    assert StockMovement.objects.filter(action="RESERVE").count() == 1
    inventory[0].price_syp = 200
    inventory[0].save()
    order.refresh_from_db()
    assert order.total_syp == 210
    assert order.lines.get().unit_price_syp_snapshot == 100


def test_same_key_changed_payload_rejected(inventory):
    submit(inventory)
    with pytest.raises(IdempotencyConflictError):
        submit(inventory, quantity=2)
    assert Order.objects.count() == 1


def test_price_change_rolls_back_without_order_or_stock(inventory):
    with pytest.raises(PriceChangedError) as caught:
        submit(inventory, expected_total_syp=90)
    assert caught.value.current_total_syp == 110
    assert not Order.objects.exists()
    inventory[1].refresh_from_db()
    assert inventory[1].qty_reserved == 0


def test_draft_and_inactive_products_cannot_be_ordered(inventory):
    inventory[0].product.is_published = False
    inventory[0].product.save()
    with pytest.raises(ValidationDomainError):
        submit(inventory)
    assert Service.get_available_quantity(inventory[0].sku) == 0


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5, "2"])
def test_invalid_quantity_rejected(inventory, quantity):
    with pytest.raises(ValidationDomainError):
        submit(inventory, quantity=quantity)
    assert not Order.objects.exists()


def test_mixed_effective_validity_fefo(inventory):
    variant, batch, zone = inventory
    early = Service.receive_batch(sku=variant.sku, lot_code="DOCUMENTED-EARLY", supplier_invoice_ref="INV-EARLY", qty_received=2, unit_cost_syp=30, expiry_date=None, documented_unopened_shelflife_date=timezone.localdate()+timedelta(days=90))
    order = submit(inventory, quantity=3)
    allocated = {a.batch_id:a.qty_allocated for a in OrderBatchAllocation.objects.filter(order_line__order=order)}
    assert allocated == {early.pk:2, batch.pk:1}


def test_receipt_reference_prevents_restock_retry(inventory):
    variant, batch, zone = inventory
    repeated = Service.receive_batch(sku=variant.sku, lot_code="LOT-1", supplier_invoice_ref="INV-1", qty_received=10, unit_cost_syp=40, expiry_date=batch.expiry_date.isoformat(), receipt_ref="RECEIPT-1")
    assert repeated.pk == batch.pk
    repeated.refresh_from_db()
    assert repeated.qty_on_hand == 10
    with pytest.raises(IdempotencyConflictError):
        Service.receive_batch(sku=variant.sku, lot_code="LOT-1", supplier_invoice_ref="INV-1", qty_received=9, unit_cost_syp=40, expiry_date=batch.expiry_date, receipt_ref="RECEIPT-1")


def test_expiry_and_cancellation_release_exactly_once(inventory):
    now = timezone.now()
    order = submit(inventory, quantity=2, now=now, hold_hours=1)
    with pytest.raises(HoldExpiredError):
        Service.confirm_order(order_id=order.pk, actor="owner", now=now+timedelta(hours=2))
    Service.expire_pending_hold(order_id=order.pk, now=now+timedelta(hours=2))
    Service.expire_pending_hold(order_id=order.pk, now=now+timedelta(hours=2))
    inventory[1].refresh_from_db()
    assert inventory[1].qty_reserved == 0
    other = submit(inventory, key="cancelled", quantity=3)
    Service.cancel_order_before_dispatch(order_id=other.pk, actor="owner", reason="test")
    Service.cancel_order_before_dispatch(order_id=other.pk, actor="owner", reason="test")
    inventory[1].refresh_from_db()
    assert (inventory[1].qty_reserved, inventory[1].qty_on_hand) == (0,10)


def test_confirmed_order_does_not_expire(inventory):
    now = timezone.now()
    order = submit(inventory, now=now)
    Service.confirm_order(order_id=order.pk, actor="owner", now=now)
    Service.expire_pending_hold(order_id=order.pk, now=now+timedelta(days=2))
    order.refresh_from_db()
    assert order.status == OrderStatus.CONFIRMED
    assert InventoryBatch.objects.get(pk=inventory[1].pk).qty_reserved == 1


def test_dispatch_and_cash_retries_do_not_duplicate(inventory):
    order = deliver(inventory, quantity=2)
    Service.dispatch_order(order_id=order.pk, courier_name="Courier", actor="owner")
    Service.record_delivery_and_customer_collection(order_id=order.pk, collected_amount_syp=order.total_syp, evidence_ref="CASH-1", actor="owner")
    batch = InventoryBatch.objects.get(pk=inventory[1].pk)
    assert (batch.qty_on_hand, batch.qty_reserved) == (8,0)
    assert StockMovement.objects.filter(action="DISPATCH").count() == 1
    assert AuditLog.objects.filter(action="DELIVER_AND_COLLECT").count() == 1
    args = dict(remittance_ref="REMIT-1", courier_name="Courier", order_ids=[order.pk], courier_deductions_syp=10, explanation="delivery", recorded_by="owner")
    first = Service.record_courier_remittance(**args)
    second = Service.record_courier_remittance(**args)
    assert first.pk == second.pk
    assert Service.get_cash_reconciliation_summary()["remitted_net_syp"] == 200


def test_blocked_or_missing_validity_at_dispatch_is_rejected(inventory):
    order = submit(inventory)
    Service.confirm_order(order_id=order.pk, actor="owner")
    batch = inventory[1]
    batch.expiry_date = None
    batch.save()
    with pytest.raises(BatchEligibilityError):
        Service.dispatch_order(order_id=order.pk, courier_name="Courier", actor="owner")
    assert InventoryBatch.objects.get(pk=batch.pk).qty_on_hand == 10


def test_return_wrong_batch_rejected_and_no_stock_created(inventory):
    order = deliver(inventory)
    other = Service.receive_batch(sku=inventory[0].sku, lot_code="NEVER-SHIPPED", supplier_invoice_ref="INV-2", qty_received=2, unit_cost_syp=40, expiry_date=inventory[1].expiry_date)
    with pytest.raises(ReturnLimitExceededError):
        Service.process_physical_return(order_line_id=order.lines.get().pk, batch_id=other.pk, qty_returned=1, seal_intact=True, storage_condition_ok=True)
    other.refresh_from_db()
    assert other.qty_on_hand == 2
    assert not ReturnRecord.objects.exists()


def test_return_quarantine_then_inspect_and_refund_reconciliation(inventory):
    order = deliver(inventory, quantity=2)
    args = dict(order_line_id=order.lines.get().pk, batch_id=inventory[1].pk, qty_returned=1, seal_intact=True, storage_condition_ok=True, refund_authorized_syp=100, refund_paid_syp=90, refund_method="cash", inspected_by="owner", return_ref="RETURN-1")
    record = Service.process_physical_return(**args)
    assert record.inspection_status == ReturnInspectionStatus.QUARANTINED
    assert record.quarantine_batch.qty_on_hand == 1
    inventory[1].refresh_from_db()
    assert inventory[1].qty_on_hand == 8
    assert Service.process_physical_return(**args).pk == record.pk
    Service.inspect_return(return_id=record.pk, approve_restock=True, inspected_by="owner")
    Service.inspect_return(return_id=record.pk, approve_restock=True, inspected_by="owner")
    inventory[1].refresh_from_db()
    record.quarantine_batch.refresh_from_db()
    assert inventory[1].qty_on_hand == 9
    assert record.quarantine_batch.qty_on_hand == 0
    summary = Service.get_cash_reconciliation_summary()
    assert summary["refunds_authorized_syp"] == 100
    assert summary["refunds_paid_syp"] == 90
    assert summary["refunds_outstanding_syp"] == 10


def test_opened_return_and_overrefund_blocked(inventory):
    order = deliver(inventory)
    args = dict(order_line_id=order.lines.get().pk, batch_id=inventory[1].pk, qty_returned=1, seal_intact=False, storage_condition_ok=True)
    with pytest.raises(ReturnLimitExceededError):
        Service.process_physical_return(**args, refund_authorized_syp=111)
    record = Service.process_physical_return(**args, refund_authorized_syp=100)
    with pytest.raises(BatchEligibilityError):
        Service.inspect_return(return_id=record.pk, approve_restock=True, inspected_by="owner")
    Service.inspect_return(return_id=record.pk, approve_restock=False, inspected_by="owner")
    inventory[1].refresh_from_db()
    assert inventory[1].qty_on_hand == 9
    with pytest.raises(ReturnLimitExceededError):
        Service.process_physical_return(**args)


def _race(functions):
    barrier = Barrier(len(functions))
    def run(function):
        close_old_connections()
        try:
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                    cursor.execute("SET statement_timeout = '10s'")
            barrier.wait(timeout=10)
            try:
                return ("ok", function())
            except Exception as exc:
                return (type(exc).__name__, str(exc))
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=len(functions)) as pool:
        return list(pool.map(run, functions))


def _postgres_only():
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL row-lock/concurrency acceptance requires PostgreSQL")


def test_postgres_last_unit_race(inventory):
    _postgres_only()
    InventoryBatch.objects.filter(pk=inventory[1].pk).update(qty_on_hand=1)
    result = _race([lambda:submit(inventory,key="race-a").pk,lambda:submit(inventory,key="race-b").pk])
    assert sorted(r[0] for r in result) == ["InsufficientStockError","ok"]
    assert Order.objects.count() == 1
    assert InventoryBatch.objects.get(pk=inventory[1].pk).qty_reserved == 1


def test_postgres_same_key_race(inventory):
    _postgres_only()
    result = _race([lambda:submit(inventory,key="race-same").pk,lambda:submit(inventory,key="race-same").pk])
    assert all(r[0] == "ok" for r in result), result
    assert result[0][1] == result[1][1]
    assert Order.objects.count() == 1
    assert InventoryBatch.objects.get(pk=inventory[1].pk).qty_reserved == 1


def test_postgres_confirmation_expiry_race(inventory):
    _postgres_only()
    now = timezone.now()
    order = submit(inventory, now=now, hold_hours=1)
    deadline = now+timedelta(hours=1)
    result = _race([lambda:Service.confirm_order(order_id=order.pk,actor="owner",now=deadline-timedelta(microseconds=1)).pk,lambda:Service.expire_pending_hold(order_id=order.pk,now=deadline).pk])
    assert all(r[0] in ("ok","HoldExpiredError") for r in result), result
    order.refresh_from_db()
    reserved = InventoryBatch.objects.get(pk=inventory[1].pk).qty_reserved
    assert (order.status,reserved) in [(OrderStatus.CONFIRMED,1),(OrderStatus.EXPIRED,0)]


def test_refund_payment_remaining_amount_and_retry(inventory):
    order = deliver(inventory)
    record = Service.process_physical_return(order_line_id=order.lines.get().pk, batch_id=inventory[1].pk, qty_returned=1, seal_intact=True, storage_condition_ok=True, refund_authorized_syp=100, refund_paid_syp=20, refund_method="cash")
    kwargs = dict(return_id=record.pk, amount_syp=80, payment_ref="REFUND-REMAINING", refund_method="cash", actor="owner")
    first = Service.record_refund_payment(**kwargs)
    assert Service.record_refund_payment(**kwargs).pk == first.pk
    record.refresh_from_db()
    assert record.refund_paid_syp == 100
    assert record.refund_payments.aggregate(total=Sum("amount_syp"))["total"] == 100
    assert Service.get_cash_reconciliation_summary()["refunds_outstanding_syp"] == 0
    with pytest.raises(ReturnLimitExceededError):
        Service.record_refund_payment(**{**kwargs,"amount_syp":1,"payment_ref":"EXCESS"})
    with pytest.raises(IdempotencyConflictError):
        Service.record_refund_payment(**{**kwargs,"amount_syp":70})


def test_stock_quantity_int32_overflow_is_domain_error(inventory):
    variant,batch,zone = inventory
    with pytest.raises(ValidationDomainError):
        Service.receive_batch(sku=variant.sku, lot_code="TOO-LARGE", supplier_invoice_ref="INV", qty_received=2**31, unit_cost_syp=1, expiry_date=batch.expiry_date)
    InventoryBatch.objects.filter(pk=batch.pk).update(qty_on_hand=2**31-1)
    with pytest.raises(InvalidStateTransitionError):
        Service.receive_batch(sku=variant.sku, lot_code=batch.lot_code, supplier_invoice_ref="INV", qty_received=1, unit_cost_syp=40, expiry_date=batch.expiry_date)
    batch.refresh_from_db()
    assert batch.qty_on_hand == 2**31-1


def test_postgres_checkout_return_race_no_deadlock(inventory):
    _postgres_only()
    order = deliver(inventory)
    def receive_return():
        return Service.process_physical_return(order_line_id=order.lines.get().pk,batch_id=inventory[1].pk,qty_returned=1,seal_intact=True,storage_condition_ok=True,return_ref="RACE-RETURN").pk
    result = _race([lambda:submit(inventory,key="checkout-versus-return").pk,receive_return])
    assert all(r[0] == "ok" for r in result), result
    batch = InventoryBatch.objects.get(pk=inventory[1].pk)
    assert (batch.qty_on_hand,batch.qty_reserved) == (9,1)


def test_postgres_confirmation_checks_deadline_after_lock_wait(inventory):
    """A request that arrived before expiry cannot confirm after its lock wait."""
    from threading import Event
    from django.db import transaction
    _postgres_only()
    order = submit(inventory)
    deadline = timezone.now()+timedelta(seconds=2)
    Order.objects.filter(pk=order.pk).update(hold_expires_at=deadline)
    entered = Event()
    def confirm():
        close_old_connections()
        entered.set()
        try:
            try:
                Service.confirm_order(order_id=order.pk, actor="owner")
            except HoldExpiredError:
                return "expired"
            return "confirmed"
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Order.objects.select_for_update().get(pk=order.pk)
            future = pool.submit(confirm)
            assert entered.wait(timeout=5)
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM (%s::timestamptz - clock_timestamp()))) + 0.1)", [deadline])
        assert future.result(timeout=10) == "expired"
    order.refresh_from_db()
    assert order.status == OrderStatus.PENDING_CONFIRMATION


def test_incoming_quarantine_evidence_review_preserves_quantities(inventory):
    variant,batch,zone = inventory
    incoming = Service.receive_batch(sku=variant.sku,lot_code="PENDING-VALIDITY",supplier_invoice_ref="INV-Q",qty_received=3,unit_cost_syp=40)
    assert incoming.condition == BatchCondition.QUARANTINE
    with pytest.raises(BatchEligibilityError):
        Service.review_batch(batch_id=incoming.pk,validity_evidence_ref="verified label",actor="catalog")
    with pytest.raises(BatchEligibilityError):
        Service.review_batch(batch_id=incoming.pk,validity_evidence_ref="verified label",expiry_date=timezone.localdate()+timedelta(days=5),actor="catalog")
    reviewed = Service.review_batch(batch_id=incoming.pk,validity_evidence_ref="LABEL-Q",expiry_date=timezone.localdate()+timedelta(days=100),actor="catalog")
    assert (reviewed.condition,reviewed.qty_on_hand,reviewed.qty_reserved) == (BatchCondition.SALEABLE,3,0)
    assert Service.get_available_quantity(variant.sku) == 13
    with pytest.raises(BatchEligibilityError):
        Service.review_batch(batch_id=batch.pk,validity_evidence_ref="LABEL",actor="catalog")


def test_incoming_review_cannot_release_customer_return(inventory):
    order = deliver(inventory)
    record = Service.process_physical_return(order_line_id=order.lines.get().pk,batch_id=inventory[1].pk,qty_returned=1,seal_intact=False,storage_condition_ok=True)
    with pytest.raises(BatchEligibilityError):
        Service.review_batch(batch_id=record.quarantine_batch_id,validity_evidence_ref="LABEL",actor="catalog")
    record.quarantine_batch.refresh_from_db()
    assert record.quarantine_batch.condition == BatchCondition.QUARANTINE
