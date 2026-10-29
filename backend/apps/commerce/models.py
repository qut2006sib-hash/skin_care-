import secrets
from django.db import models
from django.db.models import Q, F
from django.utils import timezone


class Brand(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    name_en = models.CharField(max_length=120)
    name_ar = models.CharField(max_length=120)
    is_authorized_distributor = models.BooleanField(default=False)
    authorization_evidence_ref = models.TextField(blank=True, default="")

    def __str__(self) -> str:
        return f"{self.name_en} ({self.name_ar})"


class Category(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120)

    def __str__(self) -> str:
        return self.name_ar


class Product(models.Model):
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, related_name="products")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    slug = models.SlugField(max_length=120, unique=True)
    name_ar = models.CharField(max_length=200)
    name_en = models.CharField(max_length=200)
    description_ar = models.TextField(blank=True, default="")
    ingredients_text = models.TextField(blank=True, default="", help_text="Original INCI ingredient script")
    directions_ar = models.TextField(blank=True, default="")
    warnings_ar = models.TextField(blank=True, default="")
    concern_tags = models.JSONField(default=list, help_text="Checked catalog tags e.g. ['ترطيب', 'بشرة حساسة']")
    claim_evidence_source = models.TextField(blank=True, default="", help_text="Manufacturer label or supplier document reference")
    claim_checked_date = models.DateField(null=True, blank=True)
    claim_reviewer = models.CharField(max_length=120, blank=True, default="")
    is_published = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"{self.brand.name_en} - {self.name_ar}"


class ProductVariant(models.Model):
    """Each pack size is its own distinct SKU. All monetary values are integer SYP units."""
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="variants")
    sku = models.CharField(max_length=64, unique=True)
    barcode = models.CharField(max_length=64, blank=True, default="")
    pack_size_label_ar = models.CharField(max_length=80)
    volume_ml = models.PositiveIntegerField(null=True, blank=True)
    weight_g = models.PositiveIntegerField(null=True, blank=True)
    price_syp = models.BigIntegerField(help_text="Selling price in integer SYP minor units")
    currency = models.CharField(max_length=3, default="SYP")
    requires_expiry_date = models.BooleanField(default=True)
    min_remaining_validity_days = models.PositiveIntegerField(default=90)
    pao_months = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Period After Opening (PAO) in months — stored separately, NEVER used as unopened expiry",
    )
    image_url = models.CharField(max_length=500, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(price_syp__gt=0), name="variant_price_syp_gt_zero"),
            models.CheckConstraint(condition=Q(currency="SYP"), name="variant_currency_is_syp"),
        ]

    def __str__(self) -> str:
        return f"{self.sku} ({self.pack_size_label_ar})"


class BatchCondition(models.TextChoices):
    SALEABLE = "SALEABLE", "صالح للبيع (Saleable)"
    QUARANTINE = "QUARANTINE", "حجر فحص الصلاحية/المرتجع (Quarantine)"
    EXPIRED = "EXPIRED", "منتهي الصلاحية (Expired)"
    BLOCKED = "BLOCKED", "موقوف للتتبع (Blocked Batch)"
    DAMAGED = "DAMAGED", "تالف (Damaged)"


class InventoryBatch(models.Model):
    """
    Authoritative stock record per lot.
    Available stock = SUM(qty_on_hand - qty_reserved) for SALEABLE & validity-eligible batches.
    """
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="batches")
    lot_code = models.CharField(max_length=80)
    supplier_invoice_ref = models.CharField(max_length=120)
    unit_cost_syp = models.BigIntegerField(help_text="Internal purchase cost in integer SYP; never exposed publicly")
    received_at = models.DateTimeField(default=timezone.now)
    expiry_date = models.DateField(null=True, blank=True)
    documented_unopened_shelflife_date = models.DateField(null=True, blank=True)
    storage_instructions_ar = models.TextField(blank=True, default="")
    validity_evidence_ref = models.TextField(blank=True, default="")
    condition = models.CharField(max_length=24, choices=BatchCondition.choices, default=BatchCondition.QUARANTINE)
    qty_on_hand = models.IntegerField(default=0)
    qty_reserved = models.IntegerField(default=0)

    class Meta:
        unique_together = [("variant", "lot_code")]
        constraints = [
            models.CheckConstraint(condition=Q(unit_cost_syp__gte=0), name="batch_unit_cost_non_negative"),
            models.CheckConstraint(condition=Q(qty_on_hand__gte=0), name="batch_qty_on_hand_non_negative"),
            models.CheckConstraint(condition=Q(qty_reserved__gte=0), name="batch_qty_reserved_non_negative"),
            models.CheckConstraint(condition=Q(qty_reserved__lte=F("qty_on_hand")), name="batch_reserved_lte_on_hand"),
        ]

    @property
    def effective_validity_date(self):
        return self.expiry_date or self.documented_unopened_shelflife_date

    def __str__(self) -> str:
        return f"{self.variant.sku} | {self.lot_code} ({self.condition})"


class DeliveryZone(models.Model):
    """
    Configurable Homs neighborhood delivery zones.
    fee_syp is nullable by default so no real delivery fee is ever invented;
    a zone cannot accept checkout unless is_active=True and fee_syp is explicitly configured.
    """
    city = models.CharField(max_length=64, default="Homs")
    code = models.SlugField(max_length=64, unique=True)
    district_name_ar = models.CharField(max_length=120)
    district_name_en = models.CharField(max_length=120)
    fee_syp = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Configured delivery fee in integer SYP. Must be set by Admin before enabling zone.",
    )
    estimated_hours = models.PositiveIntegerField(default=24)
    is_active = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(city="Homs"), name="delivery_zone_city_homs_only"),
            models.CheckConstraint(
                condition=Q(fee_syp__isnull=True) | Q(fee_syp__gte=0),
                name="delivery_zone_fee_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.district_name_ar} ({self.city})"


class OrderStatus(models.TextChoices):
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION", "بانتظار التأكيد"
    CONFIRMED = "CONFIRMED", "مؤكد"
    PREPARING = "PREPARING", "قيد التجهيز"
    DISPATCHED = "DISPATCHED", "تم التسليم للمندوب"
    DELIVERED = "DELIVERED", "تم التسليم للعميل"
    FAILED_DELIVERY = "FAILED_DELIVERY", "تعذر التسليم"
    EXPIRED = "EXPIRED", "انتهت مهلة الحجز"
    CANCELLED = "CANCELLED", "ملغى"


def generate_tracking_token() -> str:
    return secrets.token_urlsafe(32)


class Order(models.Model):
    order_number = models.CharField(max_length=32, unique=True)
    tracking_token = models.CharField(max_length=64, unique=True, default=generate_tracking_token)
    idempotency_key = models.CharField(max_length=128, unique=True)
    request_fingerprint = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(
        max_length=32,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING_CONFIRMATION,
    )
    customer_name = models.CharField(max_length=160)
    customer_phone = models.CharField(max_length=40)
    city = models.CharField(max_length=64, default="Homs")
    delivery_zone = models.ForeignKey(DeliveryZone, on_delete=models.PROTECT, related_name="orders")
    district_snapshot_ar = models.CharField(max_length=120)
    address_landmark_ar = models.TextField()
    currency = models.CharField(max_length=3, default="SYP")
    subtotal_syp = models.BigIntegerField()
    delivery_fee_syp = models.BigIntegerField()
    discount_syp = models.BigIntegerField(default=0)
    total_syp = models.BigIntegerField()
    created_at = models.DateTimeField(default=timezone.now)
    hold_expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    expired_at = models.DateTimeField(null=True, blank=True)
    replaced_order = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replacement_offers",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(subtotal_syp__gte=0) & Q(delivery_fee_syp__gte=0) & Q(discount_syp__gte=0) & Q(total_syp__gte=0), name="order_money_nonnegative"),
            models.CheckConstraint(condition=Q(city="Homs"), name="order_city_homs_only"),
            models.CheckConstraint(condition=Q(currency="SYP"), name="order_currency_syp_only"),
            models.CheckConstraint(
                condition=Q(total_syp=F("subtotal_syp") + F("delivery_fee_syp") - F("discount_syp")),
                name="order_total_syp_integrity",
            ),
        ]


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT)
    sku_snapshot = models.CharField(max_length=64)
    product_name_ar_snapshot = models.CharField(max_length=200)
    pack_size_ar_snapshot = models.CharField(max_length=80)
    unit_price_syp_snapshot = models.BigIntegerField()
    quantity = models.PositiveIntegerField()
    line_total_syp_snapshot = models.BigIntegerField()


    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="order_line_quantity_positive"),
            models.CheckConstraint(condition=Q(unit_price_syp_snapshot__gte=0), name="order_line_price_nonnegative"),
            models.CheckConstraint(condition=Q(line_total_syp_snapshot=F("unit_price_syp_snapshot") * F("quantity")), name="order_line_total_integrity"),
        ]


class AllocationStatus(models.TextChoices):
    RESERVED = "RESERVED", "محجوز"
    CONSUMED = "CONSUMED", "تم الصرف عند الشحن"
    RELEASED = "RELEASED", "تم تحرير الحجز"


class OrderBatchAllocation(models.Model):
    order_line = models.ForeignKey(OrderLine, on_delete=models.CASCADE, related_name="allocations")
    batch = models.ForeignKey(InventoryBatch, on_delete=models.PROTECT, related_name="allocations")
    qty_allocated = models.PositiveIntegerField()
    unit_cost_syp_snapshot = models.BigIntegerField()
    status = models.CharField(max_length=20, choices=AllocationStatus.choices, default=AllocationStatus.RESERVED)


    class Meta:
        constraints = [models.CheckConstraint(condition=Q(qty_allocated__gt=0), name="allocation_quantity_positive")]


class CashCollectionStatus(models.TextChoices):
    PENDING_COLLECTION = "PENDING_COLLECTION", "بانتظار التحصيل من العميل"
    COLLECTED = "COLLECTED", "تم التحصيل من العميل (مع المندوب)"
    REFUSED = "REFUSED", "رفض الاستلام/الدفع"
    ADJUSTED = "ADJUSTED", "تسوية مفسرة"


class CustomerCashCollection(models.Model):
    """Tracks customer COD payment independently from courier remittance to the merchant."""
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="cash_collection")
    expected_amount_syp = models.BigIntegerField()
    collected_amount_syp = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="SYP")
    status = models.CharField(
        max_length=24,
        choices=CashCollectionStatus.choices,
        default=CashCollectionStatus.PENDING_COLLECTION,
    )
    courier_name = models.CharField(max_length=120)
    collected_at = models.DateTimeField(null=True, blank=True)
    evidence_ref = models.TextField(blank=True, default="")
    remittance = models.ForeignKey(
        "CourierRemittance",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="collections",
    )


    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(expected_amount_syp__gte=0), name="collection_expected_nonnegative"),
            models.CheckConstraint(condition=Q(collected_amount_syp__gte=0), name="collection_collected_nonnegative"),
            models.CheckConstraint(condition=Q(currency="SYP"), name="collection_currency_syp"),
        ]


class CourierRemittance(models.Model):
    """Tracks actual cash handed over by the courier to the merchant."""
    remittance_ref = models.CharField(max_length=64, unique=True)
    request_fingerprint = models.CharField(max_length=64, blank=True, default="")
    courier_name = models.CharField(max_length=120)
    gross_collected_syp = models.BigIntegerField()
    courier_deductions_syp = models.BigIntegerField(default=0)
    net_remitted_syp = models.BigIntegerField()
    explanation = models.TextField(blank=True, default="")
    received_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.CharField(max_length=120)


    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(gross_collected_syp__gte=0), name="remittance_gross_nonnegative"),
            models.CheckConstraint(condition=Q(courier_deductions_syp__gte=0) & Q(courier_deductions_syp__lte=F("gross_collected_syp")), name="remittance_deductions_bounded"),
            models.CheckConstraint(condition=Q(net_remitted_syp=F("gross_collected_syp")-F("courier_deductions_syp")), name="remittance_net_integrity"),
        ]


class ReturnInspectionStatus(models.TextChoices):
    QUARANTINED = "QUARANTINED", "في الحجر بانتظار الفحص"
    APPROVED_RESTOCK = "APPROVED_RESTOCK", "مقبول لإعادة البيع"
    REJECTED_DISPOSED = "REJECTED_DISPOSED", "مرفوض (مفتوح/تالف)"


class ReturnRecord(models.Model):
    return_ref = models.CharField(max_length=128, unique=True, null=True, blank=True)
    request_fingerprint = models.CharField(max_length=64, blank=True, default="")
    quarantine_batch = models.ForeignKey(InventoryBatch, null=True, blank=True, on_delete=models.PROTECT, related_name="quarantine_returns")
    inspected_at = models.DateTimeField(null=True, blank=True)
    order_line = models.ForeignKey(OrderLine, on_delete=models.PROTECT, related_name="returns")
    batch = models.ForeignKey(InventoryBatch, on_delete=models.PROTECT)
    qty_returned = models.PositiveIntegerField()
    seal_intact = models.BooleanField(default=False)
    storage_condition_ok = models.BooleanField(default=False)
    inspection_status = models.CharField(
        max_length=24,
        choices=ReturnInspectionStatus.choices,
        default=ReturnInspectionStatus.QUARANTINED,
    )
    refund_authorized_syp = models.BigIntegerField(default=0)
    refund_paid_syp = models.BigIntegerField(default=0)
    refund_method = models.CharField(max_length=64, blank=True, default="")
    inspected_by = models.CharField(max_length=120, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)


    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(qty_returned__gt=0), name="return_quantity_positive"),
            models.CheckConstraint(condition=Q(refund_authorized_syp__gte=0), name="return_refund_authorized_nonnegative"),
            models.CheckConstraint(condition=Q(refund_paid_syp__gte=0) & Q(refund_paid_syp__lte=F("refund_authorized_syp")), name="return_refund_paid_bounded"),
        ]


class StockReceipt(models.Model):
    receipt_ref = models.CharField(max_length=128, unique=True)
    request_fingerprint = models.CharField(max_length=64)
    batch = models.ForeignKey(InventoryBatch, on_delete=models.PROTECT, related_name="receipts")
    qty_received = models.PositiveIntegerField()
    actor = models.CharField(max_length=120)
    created_at = models.DateTimeField(default=timezone.now)


class StockMovement(models.Model):
    batch = models.ForeignKey(InventoryBatch, on_delete=models.PROTECT, related_name="movements")
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.PROTECT, related_name="stock_movements")
    action = models.CharField(max_length=40)
    qty_on_hand_delta = models.IntegerField(default=0)
    qty_reserved_delta = models.IntegerField(default=0)
    actor = models.CharField(max_length=120)
    reference = models.CharField(max_length=128, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)


class AuditLog(models.Model):
    actor = models.CharField(max_length=120)
    action = models.CharField(max_length=120)
    entity_type = models.CharField(max_length=80)
    entity_id = models.CharField(max_length=80)
    reason = models.TextField(blank=True, default="")
    payload_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)


class RefundPayment(models.Model):
    payment_ref = models.CharField(max_length=128, unique=True)
    request_fingerprint = models.CharField(max_length=64)
    return_record = models.ForeignKey(ReturnRecord, on_delete=models.PROTECT, related_name="refund_payments")
    amount_syp = models.BigIntegerField()
    refund_method = models.CharField(max_length=64)
    actor = models.CharField(max_length=120)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(amount_syp__gt=0), name="refund_payment_positive")]


class SupplierCatalogItem(models.Model):
    """A source listing, never an inventory/checkout authority.

    USD prices are the supplier's reference values. Final local prices, pack
    sizes, approved labels and physical stock must be entered independently.
    """
    sku = models.CharField(max_length=64, unique=True)
    source_name = models.CharField(max_length=240)
    name_ar = models.CharField(max_length=240)
    brand_name = models.CharField(max_length=120)
    brand_name_ar = models.CharField(max_length=120)
    brand_slug = models.SlugField(max_length=64)
    category_slug = models.SlugField(max_length=64)
    category_name_ar = models.CharField(max_length=120)
    reference_price_usd = models.DecimalField(max_digits=12, decimal_places=2)
    source_repository = models.CharField(max_length=200)
    source_commit = models.CharField(max_length=40)
    source_path = models.CharField(max_length=500)
    is_visible = models.BooleanField(default=True)
    imported_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']
        constraints = [models.CheckConstraint(condition=Q(reference_price_usd__gte=0), name='supplier_reference_price_nonnegative')]
        verbose_name = 'منتج من كتالوج الموزع'
        verbose_name_plural = 'كتالوج الموزع المرجعي'

    def __str__(self):
        return f'{self.sku} — {self.source_name}'
