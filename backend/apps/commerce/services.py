"""
Authoritative Inventory, Order, Delivery, Cash & Return Service.
All stock mutations and order state transitions MUST go through this service
inside a PostgreSQL transaction using explicit row locking (`select_for_update()`).
"""
from datetime import timedelta, date
from typing import List, Dict, Any, Optional
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import (
    ProductVariant, InventoryBatch, BatchCondition,
    DeliveryZone, Order, OrderStatus, OrderLine,
    OrderBatchAllocation, AllocationStatus,
    CustomerCashCollection, CashCollectionStatus,
    CourierRemittance, ReturnRecord, ReturnInspectionStatus,
    AuditLog, generate_tracking_token,
)
from .exceptions import (
    InsufficientStockError, InvalidDeliveryZoneError,
    HoldExpiredError, InvalidStateTransitionError,
    BatchEligibilityError, ReturnLimitExceededError,
)


class InventoryOrderService:
    """Single stock and order authority enforcing Section 7, 8, 9, and 15 invariants."""

    @staticmethod
    def is_batch_eligible(batch: InventoryBatch, variant: ProductVariant, expected_delivery_date: date) -> bool:
        if batch.condition != BatchCondition.SALEABLE:
            return False
        if (batch.qty_on_hand - batch.qty_reserved) <= 0:
            return False
        eff_date = batch.effective_validity_date
        if variant.requires_expiry_date and eff_date is None:
            # PAO months alone never substitute for an unopened expiry/validity date
            return False
        if eff_date is not None:
            min_valid_until = expected_delivery_date + timedelta(days=variant.min_remaining_validity_days)
            if eff_date < min_valid_until:
                return False
        return True

    @classmethod
    def get_available_quantity(cls, sku: str, now=None) -> int:
        now = now or timezone.now()
        variant = ProductVariant.objects.get(sku=sku)
        expected_delivery_date = (now + timedelta(hours=24)).date()
        batches = InventoryBatch.objects.filter(variant=variant, condition=BatchCondition.SALEABLE)
        total = 0
        for b in batches:
            if cls.is_batch_eligible(b, variant, expected_delivery_date):
                total += (b.qty_on_hand - b.qty_reserved)
        return total

    @classmethod
    @transaction.atomic
    def receive_batch(
        cls,
        *,
        sku: str,
        lot_code: str,
        supplier_invoice_ref: str,
        qty_received: int,
        unit_cost_syp: int,
        expiry_date: Optional[date],
        documented_unopened_shelflife_date: Optional[date] = None,
        validity_evidence_ref: str = "",
        actor: str = "system",
    ) -> InventoryBatch:
        if qty_received <= 0 or unit_cost_syp < 0:
            raise ValueError("Quantity must be positive and unit cost non-negative in SYP.")
        variant = ProductVariant.objects.select_for_update().get(sku=sku)
        eff_date = expiry_date or documented_unopened_shelflife_date
        # Never invent an expiry date or use PAO symbol as unopened expiry
        if variant.requires_expiry_date and eff_date is None:
            condition = BatchCondition.QUARANTINE
        elif eff_date is not None and eff_date <= timezone.now().date():
            condition = BatchCondition.EXPIRED
        else:
            condition = BatchCondition.SALEABLE

        batch, created = InventoryBatch.objects.select_for_update().get_or_create(
            variant=variant,
            lot_code=lot_code,
            defaults={
                "supplier_invoice_ref": supplier_invoice_ref,
                "unit_cost_syp": unit_cost_syp,
                "expiry_date": expiry_date,
                "documented_unopened_shelflife_date": documented_unopened_shelflife_date,
                "validity_evidence_ref": validity_evidence_ref,
                "condition": condition,
                "qty_on_hand": qty_received,
                "qty_reserved": 0,
            },
        )
        if not created:
            batch.qty_on_hand += qty_received
            batch.save(update_fields=["qty_on_hand"])

        AuditLog.objects.create(
            actor=actor,
            action="RECEIVE_BATCH",
            entity_type="InventoryBatch",
            entity_id=str(batch.id),
            payload_json={"sku": sku, "lot_code": lot_code, "qty": qty_received, "condition": batch.condition},
        )
        return batch

    @classmethod
    @transaction.atomic
    def submit_guest_cod_order(
        cls,
        *,
        idempotency_key: str,
        customer_name: str,
        customer_phone: str,
        delivery_zone_code: str,
        address_landmark_ar: str,
        items: List[Dict[str, Any]],
        now=None,
        hold_hours: Optional[int] = None,
        replaced_order: Optional[Order] = None,
    ) -> Order:
        """
        Validates Homs delivery zone & fee, locks eligible batches in FEFO order,
        reserves stock atomically, and records an immutable order snapshot.
        Idempotent on `idempotency_key`.
        """
        now = now or timezone.now()
        hold_hours = hold_hours if hold_hours is not None else getattr(settings, "COD_UNCONFIRMED_HOLD_HOURS", 12)

        existing = Order.objects.filter(idempotency_key=idempotency_key).first()
        if existing is not None:
            return existing

        try:
            zone = DeliveryZone.objects.select_for_update().get(code=delivery_zone_code)
        except DeliveryZone.DoesNotExist:
            raise InvalidDeliveryZoneError(f"Delivery zone '{delivery_zone_code}' does not exist.")

        if zone.city != "Homs" or not zone.is_active or zone.fee_syp is None:
            raise InvalidDeliveryZoneError(
                f"المنطقة '{zone.district_name_ar}' غير مفعلة أو لم يتم تحديد أجور التوصيل لها بعد."
            )

        if not items:
            raise ValueError("Cannot submit an empty order.")

        expected_delivery_date = (now + timedelta(hours=zone.estimated_hours)).date()

        # Sort requested items by SKU to prevent deadlocks when locking multiple variants
        sorted_items = sorted(items, key=lambda x: x["sku"])
        subtotal_syp = 0
        prepared_lines = []

        for item in sorted_items:
            sku = item["sku"]
            qty_needed = int(item["quantity"])
            if qty_needed <= 0:
                raise ValueError("Item quantity must be positive.")

            variant = ProductVariant.objects.select_for_update().select_related("product").get(sku=sku, is_active=True)

            # Lock all candidate batches for this variant in deterministic FEFO order
            candidate_batches = list(
                InventoryBatch.objects.select_for_update()
                .filter(variant=variant, condition=BatchCondition.SALEABLE)
                .order_by("expiry_date", "documented_unopened_shelflife_date", "id")
            )

            eligible_batches = [
                b for b in candidate_batches
                if cls.is_batch_eligible(b, variant, expected_delivery_date)
            ]

            total_available = sum(b.qty_on_hand - b.qty_reserved for b in eligible_batches)
            if total_available < qty_needed:
                raise InsufficientStockError(
                    f"الكمية المطلوبة من {variant.sku} غير متوفرة (المتوفر القابل للبيع: {total_available})."
                )

            remaining = qty_needed
            batch_allocations = []
            for batch in eligible_batches:
                if remaining == 0:
                    break
                avail_in_batch = batch.qty_on_hand - batch.qty_reserved
                take = min(avail_in_batch, remaining)
                if take > 0:
                    batch.qty_reserved += take
                    batch.save(update_fields=["qty_reserved"])
                    batch_allocations.append((batch, take))
                    remaining -= take

            line_total_syp = variant.price_syp * qty_needed
            subtotal_syp += line_total_syp
            prepared_lines.append((variant, qty_needed, line_total_syp, batch_allocations))

        delivery_fee_syp = zone.fee_syp
        total_syp = subtotal_syp + delivery_fee_syp
        order_number = f"HOMS-{now.strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"

        order = Order.objects.create(
            order_number=order_number,
            tracking_token=generate_tracking_token(),
            idempotency_key=idempotency_key,
            status=OrderStatus.PENDING_CONFIRMATION,
            customer_name=customer_name,
            customer_phone=customer_phone,
            city="Homs",
            delivery_zone=zone,
            district_snapshot_ar=zone.district_name_ar,
            address_landmark_ar=address_landmark_ar,
            currency="SYP",
            subtotal_syp=subtotal_syp,
            delivery_fee_syp=delivery_fee_syp,
            discount_syp=0,
            total_syp=total_syp,
            created_at=now,
            hold_expires_at=now + timedelta(hours=hold_hours),
            replaced_order=replaced_order,
        )

        for variant, qty_needed, line_total_syp, batch_allocations in prepared_lines:
            order_line = OrderLine.objects.create(
                order=order,
                variant=variant,
                sku_snapshot=variant.sku,
                product_name_ar_snapshot=variant.product.name_ar,
                pack_size_ar_snapshot=variant.pack_size_label_ar,
                unit_price_syp_snapshot=variant.price_syp,
                quantity=qty_needed,
                line_total_syp_snapshot=line_total_syp,
            )
            for batch, take in batch_allocations:
                OrderBatchAllocation.objects.create(
                    order_line=order_line,
                    batch=batch,
                    qty_allocated=take,
                    unit_cost_syp_snapshot=batch.unit_cost_syp,
                    status=AllocationStatus.RESERVED,
                )

        return order

    @classmethod
    @transaction.atomic
    def confirm_order(cls, *, order_id: int, actor: str, now=None) -> Order:
        """
        Guarded state transition: verifies hold still exists and has NOT passed hold_expires_at
        according to server time, even if the background expiry worker hasn't run yet.
        """
        now = now or timezone.now()
        order = Order.objects.select_for_update().get(id=order_id)
        if order.status == OrderStatus.EXPIRED or now >= order.hold_expires_at:
            raise HoldExpiredError(
                "انتهت مهلة الحجز (12 ساعة). لا يمكن إعادة تفعيل حجز منتهٍ؛ يرجى إنشاء عرض بديل."
            )
        if order.status != OrderStatus.PENDING_CONFIRMATION:
            raise InvalidStateTransitionError(f"Cannot confirm order in status {order.status}.")

        order.status = OrderStatus.CONFIRMED
        order.confirmed_at = now
        order.save(update_fields=["status", "confirmed_at"])
        AuditLog.objects.create(
            actor=actor,
            action="CONFIRM_ORDER",
            entity_type="Order",
            entity_id=order.order_number,
        )
        return order

    @classmethod
    @transaction.atomic
    def expire_pending_hold(cls, *, order_id: int, now=None) -> Order:
        """
        Guarded state transition: marks pending order EXPIRED and releases reserved stock
        exactly once. Confirmed orders do not expire under the pending timer.
        """
        now = now or timezone.now()
        order = Order.objects.select_for_update().get(id=order_id)
        if order.status == OrderStatus.EXPIRED:
            return order
        if order.status != OrderStatus.PENDING_CONFIRMATION:
            raise InvalidStateTransitionError(f"Order in status {order.status} cannot expire as pending.")
        if now < order.hold_expires_at:
            raise InvalidStateTransitionError("Hold deadline has not been reached yet.")

        allocations = list(
            OrderBatchAllocation.objects.select_for_update()
            .select_related("batch")
            .filter(order_line__order=order, status=AllocationStatus.RESERVED)
        )
        for alloc in allocations:
            batch = InventoryBatch.objects.select_for_update().get(id=alloc.batch_id)
            batch.qty_reserved -= alloc.qty_allocated
            batch.save(update_fields=["qty_reserved"])
            alloc.status = AllocationStatus.RELEASED
            alloc.save(update_fields=["status"])

        order.status = OrderStatus.EXPIRED
        order.expired_at = now
        order.save(update_fields=["status", "expired_at"])
        return order

    @classmethod
    @transaction.atomic
    def create_replacement_offer_for_expired_order(
        cls,
        *,
        expired_order_id: int,
        idempotency_key: str,
        customer_accepted: bool,
        now=None,
    ) -> Order:
        """
        Keeps the expired order/hold closed and creates a linked replacement order
        with fresh stock validation/FEFO allocation upon customer acceptance.
        """
        if not customer_accepted:
            raise InvalidStateTransitionError("Customer must accept the replacement offer before allocation.")
        expired_order = Order.objects.select_for_update().get(id=expired_order_id)
        if expired_order.status != OrderStatus.EXPIRED:
            raise InvalidStateTransitionError("Original order must be in EXPIRED state.")

        items = [
            {"sku": line.variant.sku, "quantity": line.quantity}
            for line in expired_order.lines.select_related("variant").all()
        ]
        return cls.submit_guest_cod_order(
            idempotency_key=idempotency_key,
            customer_name=expired_order.customer_name,
            customer_phone=expired_order.customer_phone,
            delivery_zone_code=expired_order.delivery_zone.code,
            address_landmark_ar=expired_order.address_landmark_ar,
            items=items,
            now=now,
            replaced_order=expired_order,
        )

    @classmethod
    @transaction.atomic
    def cancel_order_before_dispatch(cls, *, order_id: int, actor: str, reason: str, now=None) -> Order:
        """
        Idempotent cancellation before dispatch: releases reserved units once,
        without fabricating a physical stock receipt.
        """
        now = now or timezone.now()
        order = Order.objects.select_for_update().get(id=order_id)
        if order.status == OrderStatus.CANCELLED:
            return order
        if order.status not in (OrderStatus.PENDING_CONFIRMATION, OrderStatus.CONFIRMED, OrderStatus.PREPARING):
            raise InvalidStateTransitionError(f"Cannot cancel before dispatch when status is {order.status}.")

        allocations = list(
            OrderBatchAllocation.objects.select_for_update()
            .filter(order_line__order=order, status=AllocationStatus.RESERVED)
        )
        for alloc in allocations:
            batch = InventoryBatch.objects.select_for_update().get(id=alloc.batch_id)
            batch.qty_reserved -= alloc.qty_allocated
            batch.save(update_fields=["qty_reserved"])
            alloc.status = AllocationStatus.RELEASED
            alloc.save(update_fields=["status"])

        order.status = OrderStatus.CANCELLED
        order.cancelled_at = now
        order.save(update_fields=["status", "cancelled_at"])
        AuditLog.objects.create(
            actor=actor,
            action="CANCEL_ORDER",
            entity_type="Order",
            entity_id=order.order_number,
            reason=reason,
        )
        return order

    @classmethod
    @transaction.atomic
    def dispatch_order(cls, *, order_id: int, courier_name: str, actor: str, now=None) -> Order:
        """
        Rechecks batch eligibility at packing/dispatch, deducts warehouse qty_on_hand
        and consumes qty_reserved once, and creates an independent PENDING_COLLECTION record.
        """
        now = now or timezone.now()
        order = Order.objects.select_for_update().get(id=order_id)
        if order.status not in (OrderStatus.CONFIRMED, OrderStatus.PREPARING):
            raise InvalidStateTransitionError(f"Order must be CONFIRMED or PREPARING to dispatch (got {order.status}).")

        expected_delivery_date = (now + timedelta(hours=order.delivery_zone.estimated_hours)).date()
        allocations = list(
            OrderBatchAllocation.objects.select_for_update()
            .select_related("batch", "order_line__variant")
            .filter(order_line__order=order, status=AllocationStatus.RESERVED)
        )
        if not allocations:
            raise InvalidStateTransitionError("Order has no active batch reservations to dispatch.")

        for alloc in allocations:
            batch = InventoryBatch.objects.select_for_update().get(id=alloc.batch_id)
            variant = alloc.order_line.variant
            if batch.condition != BatchCondition.SALEABLE:
                raise BatchEligibilityError(f"Batch {batch.lot_code} is no longer SALEABLE (status: {batch.condition}).")
            eff_date = batch.effective_validity_date
            if eff_date is not None and eff_date < (expected_delivery_date + timedelta(days=variant.min_remaining_validity_days)):
                raise BatchEligibilityError(f"Batch {batch.lot_code} fails minimum remaining validity at dispatch.")

            batch.qty_on_hand -= alloc.qty_allocated
            batch.qty_reserved -= alloc.qty_allocated
            batch.save(update_fields=["qty_on_hand", "qty_reserved"])
            alloc.status = AllocationStatus.CONSUMED
            alloc.save(update_fields=["status"])

        order.status = OrderStatus.DISPATCHED
        order.dispatched_at = now
        order.save(update_fields=["status", "dispatched_at"])

        CustomerCashCollection.objects.create(
            order=order,
            expected_amount_syp=order.total_syp,
            collected_amount_syp=0,
            currency="SYP",
            status=CashCollectionStatus.PENDING_COLLECTION,
            courier_name=courier_name,
        )
        AuditLog.objects.create(
            actor=actor,
            action="DISPATCH_ORDER",
            entity_type="Order",
            entity_id=order.order_number,
            payload_json={"courier_name": courier_name},
        )
        return order

    @classmethod
    @transaction.atomic
    def record_delivery_and_customer_collection(
        cls,
        *,
        order_id: int,
        collected_amount_syp: int,
        evidence_ref: str,
        actor: str,
        now=None,
    ) -> CustomerCashCollection:
        """
        Marks the order DELIVERED and records customer cash collected by the courier.
        Courier settlement to the merchant remains independent (outstanding until remitted).
        """
        now = now or timezone.now()
        order = Order.objects.select_for_update().get(id=order_id)
        if order.status != OrderStatus.DISPATCHED:
            raise InvalidStateTransitionError("Order must be DISPATCHED before recording delivery.")

        collection = CustomerCashCollection.objects.select_for_update().get(order=order)
        if collected_amount_syp != collection.expected_amount_syp:
            raise ValueError(
                f"Collected SYP ({collected_amount_syp}) must match payable total ({collection.expected_amount_syp}) unless recorded as an explained adjustment."
            )

        order.status = OrderStatus.DELIVERED
        order.delivered_at = now
        order.save(update_fields=["status", "delivered_at"])

        collection.collected_amount_syp = collected_amount_syp
        collection.status = CashCollectionStatus.COLLECTED
        collection.collected_at = now
        collection.evidence_ref = evidence_ref
        collection.save(update_fields=["collected_amount_syp", "status", "collected_at", "evidence_ref"])
        return collection

    @classmethod
    @transaction.atomic
    def record_failed_delivery(cls, *, order_id: int, actor: str, reason: str) -> Order:
        """
        Marks delivery failed while keeping goods unavailable outside the warehouse
        until physically returned into quarantine and inspected.
        """
        order = Order.objects.select_for_update().get(id=order_id)
        if order.status != OrderStatus.DISPATCHED:
            raise InvalidStateTransitionError("Only DISPATCHED orders can fail delivery.")
        order.status = OrderStatus.FAILED_DELIVERY
        order.save(update_fields=["status"])
        collection = CustomerCashCollection.objects.select_for_update().get(order=order)
        collection.status = CashCollectionStatus.REFUSED
        collection.save(update_fields=["status"])
        AuditLog.objects.create(
            actor=actor,
            action="FAILED_DELIVERY",
            entity_type="Order",
            entity_id=order.order_number,
            reason=reason,
        )
        return order

    @classmethod
    @transaction.atomic
    def record_courier_remittance(
        cls,
        *,
        remittance_ref: str,
        courier_name: str,
        order_ids: List[int],
        courier_deductions_syp: int,
        explanation: str,
        recorded_by: str,
        now=None,
    ) -> CourierRemittance:
        now = now or timezone.now()
        collections = list(
            CustomerCashCollection.objects.select_for_update().filter(
                order_id__in=order_ids,
                status=CashCollectionStatus.COLLECTED,
                remittance__isnull=True,
            )
        )
        if len(collections) != len(order_ids):
            raise InvalidStateTransitionError("All specified orders must have unremitted COLLECTED customer cash.")

        gross_syp = sum(c.collected_amount_syp for c in collections)
        if courier_deductions_syp < 0 or courier_deductions_syp > gross_syp:
            raise ValueError("Invalid courier deductions.")

        remittance = CourierRemittance.objects.create(
            remittance_ref=remittance_ref,
            courier_name=courier_name,
            gross_collected_syp=gross_syp,
            courier_deductions_syp=courier_deductions_syp,
            net_remitted_syp=gross_syp - courier_deductions_syp,
            explanation=explanation,
            received_at=now,
            recorded_by=recorded_by,
        )
        for c in collections:
            c.remittance = remittance
            c.save(update_fields=["remittance"])
        return remittance

    @classmethod
    def get_cash_reconciliation_summary(cls) -> Dict[str, int]:
        """
        Distinguishes:
        1. Dispatched orders awaiting customer delivery/collection
        2. Delivered orders where courier collected cash but has NOT remitted to merchant
        3. Remitted cash received by merchant (gross, deductions, net)
        """
        all_collections = CustomerCashCollection.objects.all()
        pending_from_customers_syp = sum(
            c.expected_amount_syp for c in all_collections if c.status == CashCollectionStatus.PENDING_COLLECTION
        )
        collected_held_by_courier_syp = sum(
            c.collected_amount_syp
            for c in all_collections
            if c.status == CashCollectionStatus.COLLECTED and c.remittance_id is None
        )
        remittances = CourierRemittance.objects.all()
        remitted_gross_syp = sum(r.gross_collected_syp for r in remittances)
        remitted_deductions_syp = sum(r.courier_deductions_syp for r in remittances)
        remitted_net_syp = sum(r.net_remitted_syp for r in remittances)

        return {
            "pending_from_customers_syp": pending_from_customers_syp,
            "collected_held_by_courier_syp": collected_held_by_courier_syp,
            "remitted_gross_syp": remitted_gross_syp,
            "remitted_deductions_syp": remitted_deductions_syp,
            "remitted_net_syp": remitted_net_syp,
        }

    @classmethod
    @transaction.atomic
    def process_physical_return(
        cls,
        *,
        order_line_id: int,
        batch_id: int,
        qty_returned: int,
        seal_intact: bool,
        storage_condition_ok: bool,
        approve_restock: bool,
        refund_authorized_syp: int,
        refund_paid_syp: int,
        refund_method: str,
        inspected_by: str,
    ) -> ReturnRecord:
        """
        Every physically returned cosmetic enters QUARANTINE first.
        Opened, tampered, or heat-damaged goods can NEVER become SALEABLE.
        Cumulative returned qty <= delivered qty; cumulative refund <= collected amount.
        """
        line = OrderLine.objects.select_for_update().select_related("order").get(id=order_line_id)
        order = line.order
        if order.status not in (OrderStatus.DELIVERED, OrderStatus.FAILED_DELIVERY):
            raise InvalidStateTransitionError("Returns require DELIVERED or FAILED_DELIVERY status.")

        existing_returns = ReturnRecord.objects.filter(order_line=line)
        already_returned_qty = sum(r.qty_returned for r in existing_returns)
        if already_returned_qty + qty_returned > line.quantity:
            raise ReturnLimitExceededError("Cumulative returned quantity exceeds delivered quantity.")

        all_order_returns = ReturnRecord.objects.filter(order_line__order=order)
        already_refunded_syp = sum(r.refund_authorized_syp for r in all_order_returns)
        max_refundable = order.total_syp
        if hasattr(order, "cash_collection") and order.cash_collection.status == CashCollectionStatus.COLLECTED:
            max_refundable = order.cash_collection.collected_amount_syp
        elif order.status == OrderStatus.FAILED_DELIVERY:
            max_refundable = 0

        if already_refunded_syp + refund_authorized_syp > max_refundable:
            raise ReturnLimitExceededError("Cumulative refund exceeds refundable amount.")
        if refund_paid_syp > refund_authorized_syp:
            raise ReturnLimitExceededError("Paid refund cannot exceed authorized refund.")

        orig_batch = InventoryBatch.objects.select_for_update().get(id=batch_id)

        # If restock is requested, both seal_intact and storage_condition_ok MUST be True
        if approve_restock and (not seal_intact or not storage_condition_ok):
            raise BatchEligibilityError("Opened, unsealed, or heat-exposed cosmetics cannot be restocked as SALEABLE.")

        if approve_restock and seal_intact and storage_condition_ok:
            inspection_status = ReturnInspectionStatus.APPROVED_RESTOCK
            orig_batch.qty_on_hand += qty_returned
            orig_batch.save(update_fields=["qty_on_hand"])
        else:
            inspection_status = (
                ReturnInspectionStatus.REJECTED_DISPOSED
                if not (seal_intact and storage_condition_ok)
                else ReturnInspectionStatus.QUARANTINED
            )
            # Keep returned unit in a quarantined lot record, never in saleable pool
            q_lot_code = f"{orig_batch.lot_code}-RTN-Q"
            q_batch, _ = InventoryBatch.objects.select_for_update().get_or_create(
                variant=orig_batch.variant,
                lot_code=q_lot_code,
                defaults={
                    "supplier_invoice_ref": orig_batch.supplier_invoice_ref,
                    "unit_cost_syp": orig_batch.unit_cost_syp,
                    "expiry_date": orig_batch.expiry_date,
                    "condition": BatchCondition.QUARANTINE,
                    "qty_on_hand": 0,
                    "qty_reserved": 0,
                },
            )
            q_batch.qty_on_hand += qty_returned
            q_batch.save(update_fields=["qty_on_hand"])

        return ReturnRecord.objects.create(
            order_line=line,
            batch=orig_batch,
            qty_returned=qty_returned,
            seal_intact=seal_intact,
            storage_condition_ok=storage_condition_ok,
            inspection_status=inspection_status,
            refund_authorized_syp=refund_authorized_syp,
            refund_paid_syp=refund_paid_syp,
            refund_method=refund_method,
            inspected_by=inspected_by,
        )

    @classmethod
    @transaction.atomic
    def block_batch_and_trace_orders(cls, *, batch_id: int, reason: str, actor: str) -> List[str]:
        batch = InventoryBatch.objects.select_for_update().get(id=batch_id)
        batch.condition = BatchCondition.BLOCKED
        batch.save(update_fields=["condition"])
        affected_orders = list(
            Order.objects.filter(lines__allocations__batch=batch)
            .values_list("order_number", flat=True)
            .distinct()
        )
        AuditLog.objects.create(
            actor=actor,
            action="BLOCK_BATCH",
            entity_type="InventoryBatch",
            entity_id=batch.lot_code,
            reason=reason,
            payload_json={"affected_orders": affected_orders},
        )
        return affected_orders
