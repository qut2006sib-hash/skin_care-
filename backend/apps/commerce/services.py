"""The sole authority for commerce state and stock mutations.

PostgreSQL transactions serialize shared stock, order transitions and retry keys.
Lock order: retry advisory key, order (when present), variant(s), batch IDs.
No nullable joined FOR UPDATE queries are used.
"""
import hashlib
import json
import secrets
from collections import defaultdict
from datetime import date, timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    ProductVariant, InventoryBatch, BatchCondition, DeliveryZone, Order,
    OrderStatus, OrderLine, OrderBatchAllocation, AllocationStatus,
    CustomerCashCollection, CashCollectionStatus, CourierRemittance,
    ReturnRecord, ReturnInspectionStatus, AuditLog, StockReceipt,
    StockMovement, RefundPayment, generate_tracking_token,
)
from .exceptions import (
    InsufficientStockError, InvalidDeliveryZoneError, HoldExpiredError,
    InvalidStateTransitionError, BatchEligibilityError, ReturnLimitExceededError,
    ValidationDomainError, IdempotencyConflictError, PriceChangedError,
)


def _fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode()).hexdigest()


def _retry_lock(namespace, key):
    """Transaction-scoped, collision-safe serialization before checking absent rows."""
    if connection.vendor == "postgresql":
        value = int.from_bytes(hashlib.sha256(f"{namespace}:{key}".encode()).digest()[:8], "big", signed=True)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [value])


def _integer(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationDomainError(f"{name}: يجب إدخال عدد صحيح لا يقل عن {minimum}.")
    if value > 9_000_000_000_000_000:
        raise ValidationDomainError(f"{name}: القيمة أكبر من الحد المسموح.")
    return value


def _quantity(value, name, minimum=0):
    _integer(value, name, minimum)
    if value > 2_147_483_647:
        raise ValidationDomainError(f"{name}: الكمية أكبر من الحد المسموح.")
    return value


def _text(value, name, maximum=128, required=True):
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise ValidationDomainError(f"{name}: يرجى إدخال قيمة صحيحة بطول لا يتجاوز {maximum}.")
    return value.strip()


def _date(value, name):
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError as exc:
            raise ValidationDomainError(f"{name}: التاريخ يجب أن يكون YYYY-MM-DD.") from exc
    if not isinstance(value, date):
        raise ValidationDomainError(f"{name}: تاريخ غير صالح.")
    return value


def _audit(actor, action, obj, payload=None, reason=""):
    AuditLog.objects.create(actor=actor, action=action, entity_type=type(obj).__name__, entity_id=str(obj.pk), payload_json=payload or {}, reason=reason)


def _move(batch, action, *, on_hand=0, reserved=0, actor="system", order=None, reference=""):
    batch.qty_on_hand += on_hand
    batch.qty_reserved += reserved
    if not (0 <= batch.qty_on_hand <= 2_147_483_647) or batch.qty_reserved < 0 or batch.qty_reserved > batch.qty_on_hand:
        raise InvalidStateTransitionError("حالة المخزون غير متوافقة مع الحجز. لم تُحفظ العملية.")
    batch.save(update_fields=["qty_on_hand", "qty_reserved"])
    StockMovement.objects.create(batch=batch, order=order, action=action, qty_on_hand_delta=on_hand, qty_reserved_delta=reserved, actor=actor, reference=reference)


class InventoryOrderService:
    @staticmethod
    def is_batch_eligible(batch, variant, expected_delivery_date, *, require_available=True):
        if batch.condition != BatchCondition.SALEABLE:
            return False
        if require_available and batch.qty_on_hand <= batch.qty_reserved:
            return False
        effective_date = batch.effective_validity_date
        if effective_date is None and (variant.requires_expiry_date or not batch.validity_evidence_ref.strip()):
            return False
        return effective_date is None or effective_date >= expected_delivery_date + timedelta(days=variant.min_remaining_validity_days)

    @classmethod
    def get_available_quantity(cls, sku, now=None):
        now = now or timezone.now()
        variant = ProductVariant.objects.get(sku=sku)
        if not variant.is_active or not variant.product.is_published:
            return 0
        expected = timezone.localdate(now + timedelta(hours=24))
        return sum(b.qty_on_hand-b.qty_reserved for b in variant.batches.filter(condition=BatchCondition.SALEABLE) if cls.is_batch_eligible(b, variant, expected))

    @classmethod
    @transaction.atomic
    def receive_batch(cls, *, sku, lot_code, supplier_invoice_ref, qty_received, unit_cost_syp, expiry_date=None, documented_unopened_shelflife_date=None, validity_evidence_ref="", actor="system", receipt_ref=None):
        sku = _text(sku, "SKU", 64)
        lot_code = _text(lot_code, "رمز التشغيلة", 80)
        supplier_invoice_ref = _text(supplier_invoice_ref, "فاتورة المورد", 120)
        _quantity(qty_received, "الكمية", 1)
        _integer(unit_cost_syp, "التكلفة")
        expiry_date = _date(expiry_date, "الانتهاء")
        documented_unopened_shelflife_date = _date(documented_unopened_shelflife_date, "الصلاحية الموثقة")
        receipt_ref = _text(receipt_ref, "مرجع الاستلام", 128) if receipt_ref else None
        fingerprint = _fingerprint([sku, lot_code, supplier_invoice_ref, qty_received, unit_cost_syp, expiry_date, documented_unopened_shelflife_date, validity_evidence_ref])
        if receipt_ref:
            _retry_lock("receipt", receipt_ref)
            existing = StockReceipt.objects.filter(receipt_ref=receipt_ref).first()
            if existing:
                if existing.request_fingerprint != fingerprint:
                    raise IdempotencyConflictError("مرجع الاستلام مستخدم لبيانات مختلفة.")
                return existing.batch
        try:
            variant = ProductVariant.objects.select_for_update().get(sku=sku)
        except ProductVariant.DoesNotExist as exc:
            raise ValidationDomainError("المنتج غير موجود.") from exc
        effective = expiry_date or documented_unopened_shelflife_date
        condition = BatchCondition.SALEABLE
        if effective is None and (variant.requires_expiry_date or not validity_evidence_ref.strip()):
            condition = BatchCondition.QUARANTINE
        elif effective and effective <= timezone.localdate():
            condition = BatchCondition.EXPIRED
        batch, created = InventoryBatch.objects.select_for_update().get_or_create(variant=variant, lot_code=lot_code, defaults=dict(supplier_invoice_ref=supplier_invoice_ref, unit_cost_syp=unit_cost_syp, expiry_date=expiry_date, documented_unopened_shelflife_date=documented_unopened_shelflife_date, validity_evidence_ref=validity_evidence_ref, condition=condition, qty_on_hand=0, qty_reserved=0))
        if not created and (batch.unit_cost_syp != unit_cost_syp or batch.expiry_date != expiry_date or batch.documented_unopened_shelflife_date != documented_unopened_shelflife_date):
            raise ValidationDomainError("بيانات التكلفة أو الصلاحية تختلف عن التشغيلة المسجلة؛ راجع سجل الاستلام.")
        _move(batch, "RECEIVE", on_hand=qty_received, actor=actor, reference=receipt_ref or supplier_invoice_ref)
        if receipt_ref:
            StockReceipt.objects.create(receipt_ref=receipt_ref, request_fingerprint=fingerprint, batch=batch, qty_received=qty_received, actor=actor)
        _audit(actor, "RECEIVE_BATCH", batch, {"quantity": qty_received, "receipt_ref": receipt_ref})
        return batch

    @classmethod
    @transaction.atomic
    def review_batch(cls, *, batch_id, validity_evidence_ref, actor, expiry_date=None, documented_unopened_shelflife_date=None, approve_saleable=True, reason=""):
        """Review incoming quarantined stock; customer returns have their own gate."""
        evidence = _text(validity_evidence_ref, "مستند الصلاحية", 4000)
        if type(approve_saleable) is not bool:
            raise ValidationDomainError("قرار مراجعة التشغيلة غير صالح.")
        info = InventoryBatch.objects.get(pk=batch_id)
        variant = ProductVariant.objects.select_for_update().get(pk=info.variant_id)
        batch = InventoryBatch.objects.select_for_update().get(pk=batch_id)
        if batch.condition != BatchCondition.QUARANTINE:
            raise BatchEligibilityError("المراجعة متاحة للتشغيلة الواردة المحجورة فقط؛ لا ترفع الحظر عن التالف أو الموقوف أو المنتهي.")
        if ReturnRecord.objects.filter(quarantine_batch_id=batch.pk).exists():
            raise BatchEligibilityError("حجر المرتجعات يتطلب فحص المرتجع المرتبط به.")
        if expiry_date is not None:
            batch.expiry_date = _date(expiry_date, "الانتهاء")
        if documented_unopened_shelflife_date is not None:
            batch.documented_unopened_shelflife_date = _date(documented_unopened_shelflife_date, "الصلاحية الموثقة")
        batch.validity_evidence_ref = evidence
        effective = batch.effective_validity_date
        if approve_saleable:
            if effective is None and variant.requires_expiry_date:
                raise BatchEligibilityError("يجب توثيق صلاحية العبوة غير المفتوحة؛ رمز مدة ما بعد الفتح لا يكفي.")
            if effective and effective < timezone.localdate()+timedelta(days=1+variant.min_remaining_validity_days):
                raise BatchEligibilityError("التشغيلة لا تحقق الحد الأدنى للصلاحية عند التسليم.")
            batch.condition = BatchCondition.SALEABLE
        batch.save(update_fields=["expiry_date","documented_unopened_shelflife_date","validity_evidence_ref","condition"])
        _audit(actor, "REVIEW_INCOMING_BATCH", batch, {"approved_saleable":approve_saleable,"validity_evidence_ref":evidence}, reason=reason)
        return batch

    @classmethod
    @transaction.atomic
    def submit_guest_cod_order(cls, *, idempotency_key, customer_name, customer_phone, delivery_zone_code, address_landmark_ar, items, now=None, hold_hours=None, replaced_order=None, expected_total_syp=None):
        now = now or timezone.now()
        key = _text(idempotency_key, "مفتاح الطلب", 128)
        customer_name = _text(customer_name, "الاسم", 160)
        customer_phone = _text(customer_phone, "الهاتف", 40)
        address_landmark_ar = _text(address_landmark_ar, "العنوان", 2000)
        delivery_zone_code = _text(delivery_zone_code, "الحي", 64)
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            raise ValidationDomainError("السلة يجب أن تحتوي على منتج واحد على الأقل وبحد أقصى 100 سطر.")
        quantities = defaultdict(int)
        for item in items:
            if not isinstance(item, dict):
                raise ValidationDomainError("بيانات السلة غير صالحة.")
            quantities[_text(item.get("sku"), "SKU", 64)] += _integer(item.get("quantity"), "الكمية", 1)
        for quantity in quantities.values():
            if quantity > 1000:
                raise ValidationDomainError("الحد الأقصى للكمية هو 1000 عبوة للمنتج.")
        if expected_total_syp is not None:
            _integer(expected_total_syp, "الإجمالي")
        hold_hours = settings.COD_UNCONFIRMED_HOLD_HOURS if hold_hours is None else hold_hours
        _integer(hold_hours, "مهلة الحجز", 1)
        fingerprint = _fingerprint(dict(name=customer_name, phone=customer_phone, zone=delivery_zone_code, address=address_landmark_ar, items=sorted(quantities.items()), expected_total_syp=expected_total_syp, replacement=replaced_order.pk if replaced_order else None))
        _retry_lock("checkout", key)
        existing = Order.objects.filter(idempotency_key=key).first()
        if existing:
            if existing.request_fingerprint != fingerprint:
                raise IdempotencyConflictError("مفتاح الطلب مستخدم لسلة أو بيانات مختلفة. ابدأ محاولة جديدة.")
            return existing
        try:
            zone = DeliveryZone.objects.select_for_update().get(code=delivery_zone_code)
        except DeliveryZone.DoesNotExist as exc:
            raise InvalidDeliveryZoneError("حي التوصيل غير موجود.") from exc
        if zone.city != "Homs" or not zone.is_active or zone.fee_syp is None:
            raise InvalidDeliveryZoneError("حي التوصيل غير مفعل أو أجور التوصيل غير محددة.")
        variants = list(ProductVariant.objects.select_for_update().filter(sku__in=quantities).order_by("sku"))
        if len(variants) != len(quantities) or any(not v.is_active or not v.product.is_published for v in variants):
            raise ValidationDomainError("أحد منتجات السلة غير متاح للطلب.")
        subtotal = sum(v.price_syp * quantities[v.sku] for v in variants)
        total = subtotal + zone.fee_syp
        _integer(total, "الإجمالي")
        if expected_total_syp is not None and expected_total_syp != total:
            raise PriceChangedError(total)
        # Lock every candidate batch in global ID order before applying FEFO.
        candidates = list(InventoryBatch.objects.select_for_update().filter(variant__in=variants, condition=BatchCondition.SALEABLE).order_by("id"))
        expected_date = timezone.localdate(now + timedelta(hours=zone.estimated_hours))
        plan = []
        for variant in variants:
            eligible = [b for b in candidates if b.variant_id == variant.pk and cls.is_batch_eligible(b, variant, expected_date)]
            eligible.sort(key=lambda b: (b.effective_validity_date or date.max, b.pk))
            needed = quantities[variant.sku]
            if sum(b.qty_on_hand-b.qty_reserved for b in eligible) < needed:
                raise InsufficientStockError(f"الكمية المطلوبة من {variant.sku} غير متوفرة.")
            allocations = []
            for batch in eligible:
                take = min(needed, batch.qty_on_hand-batch.qty_reserved)
                if take:
                    allocations.append((batch, take))
                    needed -= take
                if not needed:
                    break
            plan.append((variant, allocations))
        order = Order.objects.create(order_number=f"HOMS-{now.strftime('%Y%m%d')}-{secrets.token_hex(6).upper()}", tracking_token=generate_tracking_token(), idempotency_key=key, request_fingerprint=fingerprint, status=OrderStatus.PENDING_CONFIRMATION, customer_name=customer_name, customer_phone=customer_phone, delivery_zone=zone, district_snapshot_ar=zone.district_name_ar, address_landmark_ar=address_landmark_ar, subtotal_syp=subtotal, delivery_fee_syp=zone.fee_syp, total_syp=total, created_at=now, hold_expires_at=now+timedelta(hours=hold_hours), replaced_order=replaced_order)
        for variant, allocations in plan:
            quantity = quantities[variant.sku]
            line = OrderLine.objects.create(order=order, variant=variant, sku_snapshot=variant.sku, product_name_ar_snapshot=variant.product.name_ar, pack_size_ar_snapshot=variant.pack_size_label_ar, unit_price_syp_snapshot=variant.price_syp, quantity=quantity, line_total_syp_snapshot=variant.price_syp*quantity)
            for batch, take in allocations:
                _move(batch, "RESERVE", reserved=take, order=order, reference=key)
                OrderBatchAllocation.objects.create(order_line=line, batch=batch, qty_allocated=take, unit_cost_syp_snapshot=batch.unit_cost_syp)
        _audit("customer", "SUBMIT_ORDER", order)
        return order

    @staticmethod
    def _allocations_and_batches(order):
        allocations = list(OrderBatchAllocation.objects.filter(order_line__order=order, status=AllocationStatus.RESERVED).order_by("batch_id", "id"))
        batches = {b.pk: b for b in InventoryBatch.objects.select_for_update().filter(pk__in=[a.batch_id for a in allocations]).order_by("pk")}
        return allocations, batches

    @classmethod
    @transaction.atomic
    def confirm_order(cls, *, order_id, actor, now=None):
        order = Order.objects.select_for_update().get(pk=order_id)
        # Read wall-clock time after any lock wait, never revive a hold using
        # the request's pre-lock timestamp. Explicit now remains testable.
        now = now or timezone.now()
        if order.status in (OrderStatus.CONFIRMED, OrderStatus.PREPARING):
            return order
        if order.status == OrderStatus.EXPIRED or (order.status == OrderStatus.PENDING_CONFIRMATION and now >= order.hold_expires_at):
            raise HoldExpiredError("انتهت مهلة الحجز. أنشئ طلباً بديلاً بموافقة العميل.")
        if order.status != OrderStatus.PENDING_CONFIRMATION:
            raise InvalidStateTransitionError("لا يمكن تأكيد الطلب في حالته الحالية.")
        order.status, order.confirmed_at = OrderStatus.CONFIRMED, now
        order.save(update_fields=["status", "confirmed_at"])
        _audit(actor, "CONFIRM_ORDER", order)
        return order

    @classmethod
    def _release(cls, order, actor, action):
        allocations, batches = cls._allocations_and_batches(order)
        for allocation in allocations:
            _move(batches[allocation.batch_id], action, reserved=-allocation.qty_allocated, actor=actor, order=order)
            allocation.status = AllocationStatus.RELEASED
            allocation.save(update_fields=["status"])

    @classmethod
    @transaction.atomic
    def expire_pending_hold(cls, *, order_id, now=None):
        now = now or timezone.now()
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status != OrderStatus.PENDING_CONFIRMATION:
            return order
        if now < order.hold_expires_at:
            raise InvalidStateTransitionError("مهلة الحجز لم تنته بعد.")
        cls._release(order, "hold-worker", "EXPIRE_RELEASE")
        order.status, order.expired_at = OrderStatus.EXPIRED, now
        order.save(update_fields=["status", "expired_at"])
        _audit("hold-worker", "EXPIRE_ORDER", order)
        return order

    @classmethod
    @transaction.atomic
    def create_replacement_offer_for_expired_order(cls, *, expired_order_id, idempotency_key, customer_accepted, now=None, expected_total_syp=None):
        if customer_accepted is not True:
            raise InvalidStateTransitionError("تجب موافقة العميل على الطلب البديل.")
        order = Order.objects.select_for_update().get(pk=expired_order_id)
        if order.status != OrderStatus.EXPIRED:
            raise InvalidStateTransitionError("الطلب الأصلي يجب أن يكون منتهياً.")
        return cls.submit_guest_cod_order(idempotency_key=idempotency_key, customer_name=order.customer_name, customer_phone=order.customer_phone, delivery_zone_code=order.delivery_zone.code, address_landmark_ar=order.address_landmark_ar, items=[dict(sku=l.sku_snapshot, quantity=l.quantity) for l in order.lines.all()], now=now, replaced_order=order, expected_total_syp=expected_total_syp)

    @classmethod
    @transaction.atomic
    def cancel_order_before_dispatch(cls, *, order_id, actor, reason, now=None):
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status == OrderStatus.CANCELLED:
            return order
        if order.status not in (OrderStatus.PENDING_CONFIRMATION, OrderStatus.CONFIRMED, OrderStatus.PREPARING):
            raise InvalidStateTransitionError("لا يمكن إلغاء الطلب بعد الشحن.")
        cls._release(order, actor, "CANCEL_RELEASE")
        order.status, order.cancelled_at = OrderStatus.CANCELLED, now or timezone.now()
        order.save(update_fields=["status", "cancelled_at"])
        _audit(actor, "CANCEL_ORDER", order, reason=reason)
        return order

    @classmethod
    @transaction.atomic
    def dispatch_order(cls, *, order_id, courier_name, actor, now=None):
        now = now or timezone.now()
        courier_name = _text(courier_name, "المندوب", 120)
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status in (OrderStatus.DISPATCHED, OrderStatus.DELIVERED, OrderStatus.FAILED_DELIVERY):
            if order.cash_collection.courier_name != courier_name:
                raise IdempotencyConflictError("الطلب شُحن مع مندوب آخر.")
            return order
        if order.status not in (OrderStatus.CONFIRMED, OrderStatus.PREPARING):
            raise InvalidStateTransitionError("يجب تأكيد الطلب قبل الشحن.")
        allocations, batches = cls._allocations_and_batches(order)
        if not allocations:
            raise InvalidStateTransitionError("لا توجد كميات محجوزة للشحن.")
        expected = timezone.localdate(now+timedelta(hours=order.delivery_zone.estimated_hours))
        for allocation in allocations:
            batch = batches[allocation.batch_id]
            if not cls.is_batch_eligible(batch, allocation.order_line.variant, expected, require_available=False):
                raise BatchEligibilityError(f"التشغيلة {batch.lot_code} غير صالحة للشحن.")
        for allocation in allocations:
            _move(batches[allocation.batch_id], "DISPATCH", on_hand=-allocation.qty_allocated, reserved=-allocation.qty_allocated, actor=actor, order=order)
            allocation.status = AllocationStatus.CONSUMED
            allocation.save(update_fields=["status"])
        order.status, order.dispatched_at = OrderStatus.DISPATCHED, now
        order.save(update_fields=["status", "dispatched_at"])
        CustomerCashCollection.objects.create(order=order, expected_amount_syp=order.total_syp, courier_name=courier_name)
        _audit(actor, "DISPATCH_ORDER", order, {"courier_name": courier_name})
        return order

    @classmethod
    @transaction.atomic
    def record_delivery_and_customer_collection(cls, *, order_id, collected_amount_syp, evidence_ref, actor, now=None):
        _integer(collected_amount_syp, "المبلغ المحصل")
        evidence_ref = _text(evidence_ref, "مرجع التحصيل", 2000)
        order = Order.objects.select_for_update().get(pk=order_id)
        collection = CustomerCashCollection.objects.select_for_update().get(order=order)
        if order.status == OrderStatus.DELIVERED:
            if collection.collected_amount_syp != collected_amount_syp or collection.evidence_ref != evidence_ref:
                raise IdempotencyConflictError("التسليم مسجل ببيانات تحصيل مختلفة.")
            return collection
        if order.status != OrderStatus.DISPATCHED:
            raise InvalidStateTransitionError("يجب شحن الطلب قبل تسجيل التسليم.")
        if collected_amount_syp != collection.expected_amount_syp:
            raise ValidationDomainError("المبلغ المحصل يجب أن يطابق المبلغ المستحق.")
        now = now or timezone.now()
        order.status, order.delivered_at = OrderStatus.DELIVERED, now
        order.save(update_fields=["status", "delivered_at"])
        collection.collected_amount_syp, collection.status = collected_amount_syp, CashCollectionStatus.COLLECTED
        collection.collected_at, collection.evidence_ref = now, evidence_ref
        collection.save(update_fields=["collected_amount_syp", "status", "collected_at", "evidence_ref"])
        _audit(actor, "DELIVER_AND_COLLECT", order, {"amount_syp": collected_amount_syp})
        return collection

    @classmethod
    @transaction.atomic
    def record_failed_delivery(cls, *, order_id, actor, reason):
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status == OrderStatus.FAILED_DELIVERY:
            return order
        if order.status != OrderStatus.DISPATCHED:
            raise InvalidStateTransitionError("حالة الطلب لا تسمح بتسجيل تعذر التسليم.")
        order.status = OrderStatus.FAILED_DELIVERY
        order.save(update_fields=["status"])
        CustomerCashCollection.objects.filter(order=order).update(status=CashCollectionStatus.REFUSED)
        _audit(actor, "FAILED_DELIVERY", order, reason=reason)
        return order

    @classmethod
    @transaction.atomic
    def record_courier_remittance(cls, *, remittance_ref, courier_name, order_ids, courier_deductions_syp, explanation, recorded_by, now=None):
        remittance_ref = _text(remittance_ref, "مرجع التسوية", 64)
        courier_name = _text(courier_name, "المندوب", 120)
        _integer(courier_deductions_syp, "حسميات المندوب")
        if not isinstance(order_ids, list) or not order_ids or len(set(order_ids)) != len(order_ids):
            raise ValidationDomainError("اختر طلبات فريدة للتسوية.")
        for pk in order_ids:
            _integer(pk, "رقم الطلب", 1)
        if courier_deductions_syp and not str(explanation).strip():
            raise ValidationDomainError("يجب توضيح سبب حسميات المندوب.")
        fingerprint = _fingerprint([courier_name, sorted(order_ids), courier_deductions_syp, explanation])
        _retry_lock("remittance", remittance_ref)
        existing = CourierRemittance.objects.filter(remittance_ref=remittance_ref).first()
        if existing:
            if existing.request_fingerprint != fingerprint:
                raise IdempotencyConflictError("مرجع التسوية مستخدم لبيانات أخرى.")
            return existing
        list(Order.objects.select_for_update().filter(pk__in=order_ids).order_by("pk"))
        collections = list(CustomerCashCollection.objects.select_for_update().filter(order_id__in=order_ids).order_by("order_id"))
        if len(collections) != len(order_ids) or any(c.status != CashCollectionStatus.COLLECTED or c.remittance_id or c.courier_name != courier_name for c in collections):
            raise InvalidStateTransitionError("اختر طلبات محصلة وغير مسوّاة للمندوب نفسه.")
        gross = sum(c.collected_amount_syp for c in collections)
        if courier_deductions_syp > gross:
            raise ValidationDomainError("حسميات المندوب تتجاوز المبلغ المحصل.")
        remittance = CourierRemittance.objects.create(remittance_ref=remittance_ref, request_fingerprint=fingerprint, courier_name=courier_name, gross_collected_syp=gross, courier_deductions_syp=courier_deductions_syp, net_remitted_syp=gross-courier_deductions_syp, explanation=explanation, recorded_by=recorded_by, received_at=now or timezone.now())
        for collection in collections:
            collection.remittance = remittance
            collection.save(update_fields=["remittance"])
        _audit(recorded_by, "COURIER_REMITTANCE", remittance, {"order_ids": sorted(order_ids)})
        return remittance

    @classmethod
    def get_cash_reconciliation_summary(cls):
        """Read all cash totals in one statement snapshot, without loading ledgers."""
        quote = connection.ops.quote_name
        collections = quote(CustomerCashCollection._meta.db_table)
        remittances = quote(CourierRemittance._meta.db_table)
        returns = quote(ReturnRecord._meta.db_table)
        sql = f"""
            SELECT
              (SELECT COALESCE(SUM(expected_amount_syp),0) FROM {collections} WHERE status=%s),
              (SELECT COALESCE(SUM(collected_amount_syp),0) FROM {collections} WHERE status=%s AND remittance_id IS NULL),
              (SELECT COALESCE(SUM(gross_collected_syp),0) FROM {remittances}),
              (SELECT COALESCE(SUM(courier_deductions_syp),0) FROM {remittances}),
              (SELECT COALESCE(SUM(net_remitted_syp),0) FROM {remittances}),
              (SELECT COALESCE(SUM(refund_authorized_syp),0) FROM {returns}),
              (SELECT COALESCE(SUM(refund_paid_syp),0) FROM {returns})
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, [CashCollectionStatus.PENDING_COLLECTION,CashCollectionStatus.COLLECTED])
            pending,held,gross,deductions,net,authorized,paid = cursor.fetchone()
        return dict(pending_from_customers_syp=pending, collected_held_by_courier_syp=held, remitted_gross_syp=gross, remitted_deductions_syp=deductions, remitted_net_syp=net, refunds_authorized_syp=authorized, refunds_paid_syp=paid, refunds_outstanding_syp=authorized-paid, net_received_after_refunds_syp=net-paid)

    @classmethod
    @transaction.atomic
    def process_physical_return(cls, *, order_line_id, batch_id, qty_returned, seal_intact, storage_condition_ok, approve_restock=False, refund_authorized_syp=0, refund_paid_syp=0, refund_method="", inspected_by="system", return_ref=None):
        _quantity(qty_returned, "الكمية المرتجعة", 1)
        _integer(refund_authorized_syp, "المبلغ المعتمد")
        _integer(refund_paid_syp, "المبلغ المدفوع")
        if refund_paid_syp > refund_authorized_syp:
            raise ReturnLimitExceededError("المدفوع يتجاوز المبلغ المعتمد.")
        if refund_paid_syp and not refund_method.strip():
            raise ValidationDomainError("حدد طريقة دفع المبلغ المرتجع.")
        if type(seal_intact) is not bool or type(storage_condition_ok) is not bool or type(approve_restock) is not bool:
            raise ValidationDomainError("قيم حالة الختم والتخزين غير صالحة.")
        if approve_restock and not (seal_intact and storage_condition_ok):
            raise BatchEligibilityError("العبوة المفتوحة أو المتضررة لا تعاد للبيع.")
        return_ref = _text(return_ref, "مرجع المرتجع", 128) if return_ref else None
        fingerprint = _fingerprint([order_line_id, batch_id, qty_returned, seal_intact, storage_condition_ok, refund_authorized_syp, refund_paid_syp, refund_method])
        if return_ref:
            _retry_lock("return", return_ref)
            existing = ReturnRecord.objects.filter(return_ref=return_ref).first()
            if existing:
                if existing.request_fingerprint != fingerprint:
                    raise IdempotencyConflictError("مرجع المرتجع مستخدم لبيانات أخرى.")
                return existing
        line_info = OrderLine.objects.get(pk=order_line_id)
        order = Order.objects.select_for_update().get(pk=line_info.order_id)
        line = OrderLine.objects.get(pk=order_line_id)
        if order.status not in (OrderStatus.DELIVERED, OrderStatus.FAILED_DELIVERY):
            raise InvalidStateTransitionError("المرتجع يتطلب طلباً مسلماً أو متعذر التسليم.")
        consumed = OrderBatchAllocation.objects.filter(order_line=line, batch_id=batch_id, status=AllocationStatus.CONSUMED).aggregate(total=Sum("qty_allocated"))["total"] or 0
        already = ReturnRecord.objects.filter(order_line=line, batch_id=batch_id).aggregate(total=Sum("qty_returned"))["total"] or 0
        if qty_returned + already > consumed:
            raise ReturnLimitExceededError("الكمية أو التشغيلة لا تطابق ما شُحن في هذا السطر.")
        authorized = ReturnRecord.objects.filter(order_line__order=order).aggregate(total=Sum("refund_authorized_syp"))["total"] or 0
        collection = CustomerCashCollection.objects.filter(order=order, status=CashCollectionStatus.COLLECTED).first()
        refundable = collection.collected_amount_syp if collection else 0
        if authorized+refund_authorized_syp > refundable or refund_authorized_syp > line.unit_price_syp_snapshot*qty_returned:
            raise ReturnLimitExceededError("المبلغ يتجاوز قيمة البضاعة المرتجعة أو النقد المحصل.")
        # Lock the variant before its batches: PostgreSQL FK checks for the
        # new quarantine lot must not invert checkout's variant→batch order.
        ProductVariant.objects.select_for_update().get(pk=line.variant_id)
        original = InventoryBatch.objects.select_for_update().get(pk=batch_id)
        if original.variant_id != line.variant_id:
            raise ReturnLimitExceededError("التشغيلة لا تخص المنتج المشحون.")
        # A separate physical quarantine lot keeps received goods unavailable until inspection.
        quarantine = InventoryBatch.objects.create(variant_id=original.variant_id, lot_code=f"RTN-{secrets.token_hex(12)}", supplier_invoice_ref=original.supplier_invoice_ref, unit_cost_syp=original.unit_cost_syp, expiry_date=original.expiry_date, documented_unopened_shelflife_date=original.documented_unopened_shelflife_date, validity_evidence_ref=original.validity_evidence_ref, storage_instructions_ar=original.storage_instructions_ar, condition=BatchCondition.QUARANTINE)
        _move(quarantine, "RETURN_QUARANTINE", on_hand=qty_returned, actor=inspected_by, order=order, reference=return_ref or "")
        record = ReturnRecord.objects.create(return_ref=return_ref, request_fingerprint=fingerprint, order_line=line, batch=original, quarantine_batch=quarantine, qty_returned=qty_returned, seal_intact=seal_intact, storage_condition_ok=storage_condition_ok, inspection_status=ReturnInspectionStatus.QUARANTINED, refund_authorized_syp=refund_authorized_syp, refund_paid_syp=refund_paid_syp, refund_method=refund_method, inspected_by=inspected_by)
        if refund_paid_syp:
            initial_ref = f"return:{record.pk}:initial"
            RefundPayment.objects.create(payment_ref=initial_ref, request_fingerprint=_fingerprint([record.pk, refund_paid_syp, refund_method]), return_record=record, amount_syp=refund_paid_syp, refund_method=refund_method, actor=inspected_by)
        _audit(inspected_by, "RECEIVE_RETURN_QUARANTINE", record)
        if approve_restock:
            record = cls.inspect_return(return_id=record.pk, approve_restock=True, inspected_by=inspected_by)
        return record

    @classmethod
    @transaction.atomic
    def record_refund_payment(cls, *, return_id, amount_syp, payment_ref, refund_method, actor):
        _integer(amount_syp, "مبلغ رد النقد", 1)
        payment_ref = _text(payment_ref, "مرجع رد النقد", 128)
        if payment_ref.startswith("return:"):
            raise ValidationDomainError("مرجع رد النقد يستخدم بادئة محجوزة للنظام.")
        refund_method = _text(refund_method, "طريقة رد النقد", 64)
        fingerprint = _fingerprint([return_id, amount_syp, refund_method])
        _retry_lock("refund-payment", payment_ref)
        existing = RefundPayment.objects.filter(payment_ref=payment_ref).first()
        if existing:
            if existing.request_fingerprint != fingerprint:
                raise IdempotencyConflictError("مرجع رد النقد مستخدم لبيانات أخرى.")
            return existing
        info = ReturnRecord.objects.get(pk=return_id)
        Order.objects.select_for_update().get(pk=info.order_line.order_id)
        record = ReturnRecord.objects.select_for_update().get(pk=return_id)
        if record.refund_paid_syp + amount_syp > record.refund_authorized_syp:
            raise ReturnLimitExceededError("مجموع النقد المعاد يتجاوز المبلغ المعتمد للمرتجع.")
        payment = RefundPayment.objects.create(payment_ref=payment_ref, request_fingerprint=fingerprint, return_record=record, amount_syp=amount_syp, refund_method=refund_method, actor=actor)
        record.refund_paid_syp += amount_syp
        if not record.refund_method:
            record.refund_method = refund_method
        record.save(update_fields=["refund_paid_syp", "refund_method"])
        _audit(actor, "PAY_RETURN_REFUND", record, {"payment_ref":payment_ref,"amount_syp":amount_syp,"refund_method":refund_method})
        return payment

    @classmethod
    @transaction.atomic
    def inspect_return(cls, *, return_id, approve_restock, inspected_by):
        if type(approve_restock) is not bool:
            raise ValidationDomainError("قرار الفحص غير صالح.")
        info = ReturnRecord.objects.get(pk=return_id)
        order = Order.objects.select_for_update().get(pk=info.order_line.order_id)
        record = ReturnRecord.objects.select_for_update().get(pk=return_id)
        target = ReturnInspectionStatus.APPROVED_RESTOCK if approve_restock else ReturnInspectionStatus.REJECTED_DISPOSED
        if record.inspection_status != ReturnInspectionStatus.QUARANTINED:
            if record.inspection_status != target:
                raise InvalidStateTransitionError("تم فحص المرتجع بقرار آخر.")
            return record
        batches = {b.pk:b for b in InventoryBatch.objects.select_for_update().filter(pk__in=[record.batch_id, record.quarantine_batch_id]).order_by("pk")}
        original, quarantine = batches[record.batch_id], batches.get(record.quarantine_batch_id)
        if quarantine is None or quarantine.qty_on_hand < record.qty_returned:
            raise InvalidStateTransitionError("سجل مخزون الحجر غير متوافق.")
        if approve_restock:
            variant = record.order_line.variant
            if not record.seal_intact or not record.storage_condition_ok or not cls.is_batch_eligible(original, variant, timezone.localdate()+timedelta(days=1), require_available=False):
                raise BatchEligibilityError("المرتجع غير مختوم أو غير صالح لإعادة البيع.")
            _move(quarantine, "RETURN_RELEASE_QUARANTINE", on_hand=-record.qty_returned, actor=inspected_by, order=order, reference=record.return_ref or "")
            _move(original, "RETURN_RESTOCK", on_hand=record.qty_returned, actor=inspected_by, order=order, reference=record.return_ref or "")
        else:
            _move(quarantine, "RETURN_DISPOSE", on_hand=-record.qty_returned, actor=inspected_by, order=order, reference=record.return_ref or "")
            quarantine.condition = BatchCondition.DAMAGED
            quarantine.save(update_fields=["condition"])
        record.inspection_status, record.inspected_by, record.inspected_at = target, inspected_by, timezone.now()
        record.save(update_fields=["inspection_status", "inspected_by", "inspected_at"])
        _audit(inspected_by, "INSPECT_RETURN", record, {"decision": target})
        return record

    @classmethod
    @transaction.atomic
    def block_batch_and_trace_orders(cls, *, batch_id, reason, actor):
        batch = InventoryBatch.objects.select_for_update().get(pk=batch_id)
        batch.condition = BatchCondition.BLOCKED
        batch.save(update_fields=["condition"])
        affected = list(Order.objects.filter(lines__allocations__batch=batch).values_list("order_number", flat=True).distinct())
        _audit(actor, "BLOCK_BATCH", batch, {"affected_orders": affected}, reason=reason)
        return affected
