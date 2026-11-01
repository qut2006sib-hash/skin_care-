"""Tenant-isolated pharmacy purchases and paid-in-full cash sales.

Every mutation locks its Store row before checking status and retry references.
This serializes same-store writes and approval/suspension on PostgreSQL. No
operation here changes the central online warehouse or online order ledger.
"""
import secrets
from datetime import date
from django.db import transaction
from django.utils import timezone
from apps.commerce.services import _fingerprint, _integer, _quantity, _text, _date, _audit
from apps.commerce.exceptions import ValidationDomainError, IdempotencyConflictError, InsufficientStockError, PriceChangedError
from .models import Store, StoreProduct, StoreBatch, StoreReceipt, StoreSale, StoreSaleLine, StoreSaleAllocation


def approved_store(store_id):
    store = Store.objects.select_for_update().get(pk=store_id)
    if store.status != 'APPROVED':
        raise ValidationDomainError('يلزم تفعيل حساب الصيدلية من المدير قبل تسجيل العمليات.')
    return store


def checked_items(items, *, receiving=False):
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValidationDomainError('أدخل من سطر واحد إلى 100 سطر.')
    result, seen = [], set()
    for row in items:
        if not isinstance(row, dict):
            raise ValidationDomainError('سطر غير صالح.')
        sku = _text(row.get('sku'), 'SKU', 64)
        quantity = _quantity(row.get('quantity'), 'الكمية', 1)
        if quantity > 1000000:
            raise ValidationDomainError('الكمية أكبر من الحد المسموح.')
        if receiving:
            lot = _text(row.get('lot_code'), 'رقم الدفعة', 80)
            key = (sku, lot)
            entry = dict(sku=sku, quantity=quantity, lot_code=lot, expiry_date=_date(row.get('expiry_date'), 'الانتهاء'), unit_cost_syp=_integer(row.get('unit_cost_syp'), 'التكلفة'))
        else:
            key, entry = sku, dict(sku=sku, quantity=quantity)
        if key in seen:
            raise ValidationDomainError('يوجد سطر مكرر؛ اجمع الكمية في سطر واحد.')
        seen.add(key)
        result.append(entry)
    return sorted(result, key=lambda x: (x['sku'], x.get('lot_code', '')))


@transaction.atomic
def save_product(*, store_id, sku, name, selling_price_syp, low_stock_threshold=5, actor='store'):
    store = approved_store(store_id)
    sku, name = _text(sku, 'SKU', 64), _text(name, 'الاسم', 200)
    price = _integer(selling_price_syp, 'سعر البيع', 1)
    threshold = _quantity(low_stock_threshold, 'حد التنبيه')
    product, _ = StoreProduct.objects.update_or_create(store=store, sku=sku, defaults={'name': name, 'selling_price_syp': price, 'low_stock_threshold': threshold})
    _audit(actor[:120], 'STORE_PRODUCT', product)
    return product


@transaction.atomic
def receive_stock(*, store_id, reference, items, actor='store'):
    store = approved_store(store_id)
    reference = _text(reference, 'مرجع الفاتورة', 128)
    rows = checked_items(items, receiving=True)
    fingerprint = _fingerprint(rows)
    existing = StoreReceipt.objects.filter(store=store, reference=reference).first()
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise IdempotencyConflictError('مرجع الفاتورة مستخدم لبيانات مختلفة.')
        return existing
    products = {p.sku: p for p in StoreProduct.objects.filter(store=store, sku__in=[r['sku'] for r in rows])}
    if len(products) != len(set(r['sku'] for r in rows)):
        raise ValidationDomainError('أضف الأصناف إلى كتالوج صيدليتك أولًا.')
    total = _integer(sum(r['quantity']*r['unit_cost_syp'] for r in rows), 'إجمالي الفاتورة')
    snapshot = [{**r, 'expiry_date': r['expiry_date'].isoformat() if r['expiry_date'] else None} for r in rows]
    receipt = StoreReceipt.objects.create(store=store, reference=reference, request_fingerprint=fingerprint, total_syp=total, items=snapshot, actor=actor)
    for row in rows:
        expiry = row['expiry_date']
        StoreBatch.objects.create(product=products[row['sku']], lot_code=row['lot_code'], expiry_date=expiry, quantity=row['quantity'], unit_cost_syp=row['unit_cost_syp'], condition='SALEABLE' if expiry and expiry > timezone.localdate() else 'QUARANTINE', receipt=receipt)
    _audit(actor[:120], 'STORE_RECEIVE', receipt)
    return receipt


@transaction.atomic
def make_sale(*, store_id, reference, items, actor='store', expected_total_syp=None):
    store = approved_store(store_id)
    reference = _text(reference, 'مرجع البيع', 128)
    rows = checked_items(items)
    if expected_total_syp is not None:
        _integer(expected_total_syp, 'الإجمالي المتوقع')
    fingerprint = _fingerprint({'items': rows, 'expected_total_syp': expected_total_syp})
    existing = StoreSale.objects.filter(store=store, reference=reference).first()
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise IdempotencyConflictError('مرجع البيع مستخدم لسلة مختلفة.')
        return existing
    products = {p.sku: p for p in StoreProduct.objects.filter(store=store, sku__in=[r['sku'] for r in rows])}
    if len(products) != len(rows):
        raise ValidationDomainError('الصنف غير موجود في صيدليتك.')
    plan = []
    for row in rows:
        product, remaining, allocations = products[row['sku']], row['quantity'], []
        batches = StoreBatch.objects.select_for_update().filter(product=product, condition='SALEABLE', expiry_date__gt=timezone.localdate(), quantity__gt=0).order_by('expiry_date', 'id')
        for batch in batches:
            take = min(remaining, batch.quantity)
            allocations.append((batch, take))
            remaining -= take
            if remaining == 0:
                break
        if remaining:
            raise InsufficientStockError(f"المخزون الصالح من {product.sku} غير كافٍ.")
        cost = sum(batch.unit_cost_syp*qty for batch, qty in allocations)
        plan.append((product, row['quantity'], cost, allocations))
    total = _integer(sum(p.selling_price_syp*qty for p, qty, _, _ in plan), 'إجمالي البيع')
    if expected_total_syp is not None and expected_total_syp != total:
        raise PriceChangedError(total)
    cost = _integer(sum(cost for _, _, cost, _ in plan), 'تكلفة البيع')
    sale = StoreSale.objects.create(store=store, reference=reference, request_fingerprint=fingerprint, receipt_number=f'PH-{store.pk}-{secrets.token_hex(8).upper()}', total_syp=total, cost_syp=cost, actor=actor)
    for product, qty, line_cost, allocations in plan:
        line = StoreSaleLine.objects.create(sale=sale, sku=product.sku, name=product.name, quantity=qty, unit_price_syp=product.selling_price_syp, cost_syp=line_cost)
        for batch, take in allocations:
            batch.quantity -= take
            batch.save(update_fields=['quantity'])
            StoreSaleAllocation.objects.create(line=line, batch=batch, quantity=take, unit_cost_syp=batch.unit_cost_syp)
    _audit(actor[:120], 'STORE_CASH_SALE', sale)
    return sale


@transaction.atomic
def return_sale(*, store_id, sale_id, reason, actor='store'):
    store = approved_store(store_id)
    reason = _text(reason, 'سبب المرتجع', 1000)
    sale = StoreSale.objects.get(pk=sale_id, store=store)
    if sale.is_returned:
        return sale
    for line in sale.lines.all():
        for allocation in line.allocations.select_related('batch__product'):
            batch = allocation.batch
            # Returned cosmetics are physically retained in quarantine, never
            # silently reintroduced to the shelf or the central warehouse.
            StoreBatch.objects.create(product=batch.product, lot_code=batch.lot_code, expiry_date=batch.expiry_date, quantity=allocation.quantity, unit_cost_syp=allocation.unit_cost_syp, condition='QUARANTINE')
    sale.is_returned, sale.returned_at, sale.return_reason = True, timezone.now(), reason
    sale.save(update_fields=['is_returned', 'returned_at', 'return_reason'])
    _audit(actor[:120], 'STORE_FULL_RETURN', sale, reason=reason)
    return sale
