"""
Complete Pytest + Django PostgreSQL integration test suite for Section 13 & Section 15 acceptance criteria.
Run inside Docker Compose via:
    docker compose exec backend pytest -v
"""
from datetime import timedelta
import pytest
from django.utils import timezone
from django.core.management import call_command

from apps.commerce.models import (
    ProductVariant, InventoryBatch, BatchCondition,
    DeliveryZone, Order, OrderStatus, OrderBatchAllocation, AllocationStatus,
    CustomerCashCollection, CashCollectionStatus,
)
from apps.commerce.services import InventoryOrderService
from apps.commerce.exceptions import (
    InsufficientStockError, InvalidDeliveryZoneError,
    HoldExpiredError, InvalidStateTransitionError,
    BatchEligibilityError, ReturnLimitExceededError,
)


@pytest.fixture
def seeded_db(db):
    call_command("seed_section15", demo=True)
    zone = DeliveryZone.objects.get(code="homs-al-waer")
    zone.fee_syp = 15000
    zone.is_active = True
    zone.save()
    return zone


@pytest.mark.django_db(transaction=True)
def test_01_seed_10_skus_and_quarantine_validity_hold(seeded_db):
    assert ProductVariant.objects.count() == 10
    assert InventoryOrderService.get_available_quantity("MEDEE-HYDRA-B5-30ML") == 16
    assert InventoryOrderService.get_available_quantity("JUVELAB-RETINOL-NIGHT-30ML") == 0
    # FEME-BIOTIN-MASK-250ML has 10 physical units in QUARANTINE because PAO 12M is not an expiry date
    assert InventoryOrderService.get_available_quantity("FEME-BIOTIN-MASK-250ML") == 0


@pytest.mark.django_db(transaction=True)
def test_02_unconfigured_delivery_zone_blocks_checkout(seeded_db):
    # homs-akrama is seeded with fee_syp=None, is_active=False
    with pytest.raises(InvalidDeliveryZoneError):
        InventoryOrderService.submit_guest_cod_order(
            idempotency_key="unconf-zone-1",
            customer_name="أحمد",
            customer_phone="+963991112233",
            delivery_zone_code="homs-akrama",
            address_landmark_ar="عكرمة — قرب جامعة البعث",
            items=[{"sku": "MEDEE-HYDRA-B5-30ML", "quantity": 1}],
        )


@pytest.mark.django_db(transaction=True)
def test_03_fefo_two_lot_allocation(seeded_db):
    order = InventoryOrderService.submit_guest_cod_order(
        idempotency_key="idem-fefo-1",
        customer_name="سارة العلي",
        customer_phone="+963991234567",
        delivery_zone_code="homs-al-waer",
        address_landmark_ar="الوعر — شارع الفردوس قرب صيدلية الشفاء",
        items=[{"sku": "MEDEE-HYDRA-B5-30ML", "quantity": 8}],
    )
    assert order.subtotal_syp == 8 * 145000
    assert order.delivery_fee_syp == 15000
    assert order.total_syp == 1175000
    assert InventoryOrderService.get_available_quantity("MEDEE-HYDRA-B5-30ML") == 8

    allocs = list(OrderBatchAllocation.objects.filter(order_line__order=order).order_by("batch__expiry_date"))
    assert len(allocs) == 2
    assert allocs[0].batch.lot_code == "MED-B5-2601" and allocs[0].qty_allocated == 6
    assert allocs[1].batch.lot_code == "MED-B5-2608" and allocs[1].qty_allocated == 2


@pytest.mark.django_db(transaction=True)
def test_04_idempotent_duplicate_submission(seeded_db):
    o1 = InventoryOrderService.submit_guest_cod_order(
        idempotency_key="dup-key-99",
        customer_name="ليلى",
        customer_phone="+963994445566",
        delivery_zone_code="homs-al-waer",
        address_landmark_ar="الوعر — الجزيرة السابعة",
        items=[{"sku": "MEDEE-GENTLE-GEL-200ML", "quantity": 2}],
    )
    o2 = InventoryOrderService.submit_guest_cod_order(
        idempotency_key="dup-key-99",
        customer_name="ليلى",
        customer_phone="+963994445566",
        delivery_zone_code="homs-al-waer",
        address_landmark_ar="الوعر — الجزيرة السابعة",
        items=[{"sku": "MEDEE-GENTLE-GEL-200ML", "quantity": 2}],
    )
    assert o1.id == o2.id
    assert InventoryOrderService.get_available_quantity("MEDEE-GENTLE-GEL-200ML") == 10


@pytest.mark.django_db(transaction=True)
def test_05_12h_hold_expiry_and_replacement_offer(seeded_db):
    t0 = timezone.now()
    order = InventoryOrderService.submit_guest_cod_order(
        idempotency_key="hold-exp-1",
        customer_name="نور",
        customer_phone="+963995556677",
        delivery_zone_code="homs-al-waer",
        address_landmark_ar="الوعر — طلعة راكان",
        items=[{"sku": "JUVELAB-CICA-BARRIER-50ML", "quantity": 2}],
        now=t0,
        hold_hours=12,
    )
    assert InventoryOrderService.get_available_quantity("JUVELAB-CICA-BARRIER-50ML") == 5

    t_late = t0 + timedelta(hours=13)
    with pytest.raises(HoldExpiredError):
        InventoryOrderService.confirm_order(order_id=order.id, actor="ab_noah", now=t_late)

    InventoryOrderService.expire_pending_hold(order_id=order.id, now=t_late)
    InventoryOrderService.expire_pending_hold(order_id=order.id, now=t_late)  # idempotent
    assert InventoryOrderService.get_available_quantity("JUVELAB-CICA-BARRIER-50ML") == 7

    replacement = InventoryOrderService.create_replacement_offer_for_expired_order(
        expired_order_id=order.id,
        idempotency_key="hold-exp-1-replace",
        customer_accepted=True,
        now=t_late,
    )
    assert replacement.replaced_order_id == order.id
    assert InventoryOrderService.get_available_quantity("JUVELAB-CICA-BARRIER-50ML") == 5


@pytest.mark.django_db(transaction=True)
def test_06_cancellation_retry_idempotency(seeded_db):
    order = InventoryOrderService.submit_guest_cod_order(
        idempotency_key="cancel-idem-1",
        customer_name="مازن",
        customer_phone="+963996667788",
        delivery_zone_code="homs-al-waer",
        address_landmark_ar="الوعر — دوار المهندسين",
        items=[{"sku": "MEDEE-NIACINAMIDE-10-30ML", "quantity": 3}],
    )
    assert InventoryOrderService.get_available_quantity("MEDEE-NIACINAMIDE-10-30ML") == 6
    InventoryOrderService.cancel_order_before_dispatch(order_id=order.id, actor="ab_noah", reason="Customer cancelled")
    InventoryOrderService.cancel_order_before_dispatch(order_id=order.id, actor="ab_noah", reason="Duplicate retry")
    assert InventoryOrderService.get_available_quantity("MEDEE-NIACINAMIDE-10-30ML") == 9


@pytest.mark.django_db(transaction=True)
def test_07_dispatch_collection_remittance_and_quarantine_return(seeded_db):
    t0 = timezone.now()
    order = InventoryOrderService.submit_guest_cod_order(
        idempotency_key="cod-full-1",
        customer_name="هدى",
        customer_phone="+963997778899",
        delivery_zone_code="homs-al-waer",
        address_landmark_ar="الوعر — مقابل مشفى البر",
        items=[{"sku": "MEDEE-HYDRA-B5-30ML", "quantity": 2}],
        now=t0,
    )
    InventoryOrderService.confirm_order(order_id=order.id, actor="ab_noah", now=t0 + timedelta(hours=1))
    InventoryOrderService.dispatch_order(order_id=order.id, courier_name="مندوب حمص 1", actor="ab_noah", now=t0 + timedelta(hours=2))

    # Delivered & collected by courier, but not yet remitted to merchant
    InventoryOrderService.record_delivery_and_customer_collection(
        order_id=order.id,
        collected_amount_syp=305000,
        evidence_ref="RECEIPT-HOMS-01",
        actor="ab_noah",
        now=t0 + timedelta(hours=5),
    )
    summary_mid = InventoryOrderService.get_cash_reconciliation_summary()
    assert summary_mid["collected_held_by_courier_syp"] == 305000
    assert summary_mid["remitted_net_syp"] == 0

    # Courier remits cash to merchant
    InventoryOrderService.record_courier_remittance(
        remittance_ref="REM-2026-001",
        courier_name="مندوب حمص 1",
        order_ids=[order.id],
        courier_deductions_syp=5000,
        explanation="أجور توصيل المندوب المتفق عليها",
        recorded_by="ab_noah",
        now=t0 + timedelta(hours=8),
    )
    summary_after = InventoryOrderService.get_cash_reconciliation_summary()
    assert summary_after["collected_held_by_courier_syp"] == 0
    assert summary_after["remitted_gross_syp"] == 305000
    assert summary_after["remitted_net_syp"] == 300000

    # Test quarantine return: opened cosmetic cannot be restocked as SALEABLE
    line = order.lines.first()
    batch = InventoryBatch.objects.get(lot_code="MED-B5-2601")
    with pytest.raises(BatchEligibilityError):
        InventoryOrderService.process_physical_return(
            order_line_id=line.id,
            batch_id=batch.id,
            qty_returned=1,
            seal_intact=False,
            storage_condition_ok=True,
            approve_restock=True,
            refund_authorized_syp=145000,
            refund_paid_syp=145000,
            refund_method="CASH_HANDOFF",
            inspected_by="ab_noah",
        )
