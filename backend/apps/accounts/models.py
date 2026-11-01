"""Pharmacy inventory is independent of the central online warehouse."""
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class AccountProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='account_profile')
    account_type = models.CharField(max_length=16, choices=[('customer', 'زبون'), ('store', 'صيدلية')])

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(account_type__in=['customer', 'store']), name='account_public_types_only')]


class Store(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='owned_store')
    name = models.CharField(max_length=160)
    status = models.CharField(max_length=16, default='PENDING', choices=[('PENDING', 'بانتظار الموافقة'), ('APPROVED', 'مفعّلة'), ('SUSPENDED', 'موقوفة')])
    created_at = models.DateTimeField(default=timezone.now)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='reviewed_stores')

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(status__in=['PENDING', 'APPROVED', 'SUSPENDED']), name='store_status_valid')]

    def __str__(self):
        return self.name


class StoreProduct(models.Model):
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='products')
    sku = models.CharField(max_length=64)
    name = models.CharField(max_length=200)
    selling_price_syp = models.BigIntegerField()
    low_stock_threshold = models.PositiveIntegerField(default=5)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['store', 'sku'], name='store_product_sku_unique'), models.CheckConstraint(condition=Q(selling_price_syp__gt=0), name='store_selling_price_positive')]


class StoreReceipt(models.Model):
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='receipts')
    reference = models.CharField(max_length=128)
    request_fingerprint = models.CharField(max_length=64)
    total_syp = models.BigIntegerField()
    items = models.JSONField(default=list)
    created_at = models.DateTimeField(default=timezone.now)
    actor = models.CharField(max_length=150)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['store', 'reference'], name='store_receipt_reference_unique'), models.CheckConstraint(condition=Q(total_syp__gte=0), name='store_receipt_total_nonnegative')]


class StoreBatch(models.Model):
    product = models.ForeignKey(StoreProduct, on_delete=models.PROTECT, related_name='batches')
    lot_code = models.CharField(max_length=80)
    expiry_date = models.DateField(null=True, blank=True)
    quantity = models.PositiveIntegerField()
    unit_cost_syp = models.BigIntegerField()
    condition = models.CharField(max_length=16, default='QUARANTINE', choices=[('SALEABLE', 'صالح للبيع'), ('QUARANTINE', 'محجور')])
    receipt = models.ForeignKey(StoreReceipt, null=True, blank=True, on_delete=models.PROTECT, related_name='batches')

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(unit_cost_syp__gte=0), name='store_batch_cost_nonnegative'), models.CheckConstraint(condition=Q(condition__in=['SALEABLE', 'QUARANTINE']), name='store_batch_condition_valid')]


class StoreSale(models.Model):
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name='sales')
    reference = models.CharField(max_length=128)
    request_fingerprint = models.CharField(max_length=64)
    receipt_number = models.CharField(max_length=64, unique=True)
    total_syp = models.BigIntegerField()
    cost_syp = models.BigIntegerField()
    created_at = models.DateTimeField(default=timezone.now)
    actor = models.CharField(max_length=150)
    is_returned = models.BooleanField(default=False)
    returned_at = models.DateTimeField(null=True, blank=True)
    return_reason = models.CharField(max_length=1000, blank=True, default='')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['store', 'reference'], name='store_sale_reference_unique'), models.CheckConstraint(condition=Q(total_syp__gte=0) & Q(cost_syp__gte=0), name='store_sale_money_nonnegative')]


class StoreSaleLine(models.Model):
    sale = models.ForeignKey(StoreSale, on_delete=models.PROTECT, related_name='lines')
    sku = models.CharField(max_length=64)
    name = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField()
    unit_price_syp = models.BigIntegerField()
    cost_syp = models.BigIntegerField()

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0) & Q(unit_price_syp__gt=0) & Q(cost_syp__gte=0), name='store_sale_line_values_valid')]


class StoreSaleAllocation(models.Model):
    line = models.ForeignKey(StoreSaleLine, on_delete=models.PROTECT, related_name='allocations')
    batch = models.ForeignKey(StoreBatch, on_delete=models.PROTECT, related_name='allocations')
    quantity = models.PositiveIntegerField()
    unit_cost_syp = models.BigIntegerField()

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gt=0) & Q(unit_cost_syp__gte=0), name='store_sale_allocation_values_valid')]
