"""Explicit public output and validated command contracts."""
from django.conf import settings
from rest_framework import serializers
from .models import Product, ProductVariant, DeliveryZone, Order, InventoryBatch, ReturnRecord, CourierRemittance, RefundPayment
from .services import InventoryOrderService


class CommandSerializer(serializers.Serializer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.error_messages.update(required="هذا الحقل مطلوب.", null="يرجى إدخال قيمة.", blank="يرجى إدخال قيمة.", invalid="القيمة غير صحيحة.")


class CheckoutItemSerializer(CommandSerializer):
    sku = serializers.CharField(max_length=64)
    quantity = serializers.IntegerField(min_value=1, max_value=1000)


class CheckoutSerializer(CommandSerializer):
    customer_name = serializers.CharField(max_length=160)
    customer_phone = serializers.RegexField(r"^\+?[0-9 ()-]{7,40}$", max_length=40)
    delivery_zone_code = serializers.SlugField(max_length=64)
    address_landmark_ar = serializers.CharField(max_length=1000)
    items = CheckoutItemSerializer(many=True, allow_empty=False, max_length=100)
    expected_total_syp = serializers.IntegerField(min_value=0, required=False)

    def validate_items(self, items):
        skus = [i['sku'] for i in items]
        if len(skus) != len(set(skus)):
            raise serializers.ValidationError("لا تكرر الصنف في الطلب؛ عدّل كميته بدلاً من ذلك.")
        return items


class ReceiveBatchSerializer(CommandSerializer):
    sku = serializers.CharField(max_length=64)
    lot_code = serializers.CharField(max_length=80)
    supplier_invoice_ref = serializers.CharField(max_length=120)
    qty_received = serializers.IntegerField(min_value=1, max_value=1000000)
    unit_cost_syp = serializers.IntegerField(min_value=0)
    expiry_date = serializers.DateField(required=False, allow_null=True, default=None)
    documented_unopened_shelflife_date = serializers.DateField(required=False, allow_null=True, default=None)
    validity_evidence_ref = serializers.CharField(max_length=2000, required=False, allow_blank=True, default='')
    receipt_ref = serializers.CharField(max_length=120, required=False, allow_blank=True)


class ReasonSerializer(CommandSerializer):
    reason = serializers.CharField(max_length=1000)


class DispatchSerializer(CommandSerializer):
    courier_name = serializers.CharField(max_length=120)


class CollectionSerializer(CommandSerializer):
    collected_amount_syp = serializers.IntegerField(min_value=0)
    evidence_ref = serializers.CharField(max_length=2000)


class RemittanceSerializer(CommandSerializer):
    remittance_ref = serializers.CharField(max_length=64)
    courier_name = serializers.CharField(max_length=120)
    order_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False, max_length=200)
    courier_deductions_syp = serializers.IntegerField(min_value=0, default=0)
    explanation = serializers.CharField(max_length=2000, required=False, allow_blank=True, default='')

    def validate_order_ids(self, ids):
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError('يوجد طلب مكرر في التسوية.')
        return ids

    def validate(self, data):
        if data['courier_deductions_syp'] and not data['explanation'].strip():
            raise serializers.ValidationError('يجب توضيح سبب اقتطاع المندوب.')
        return data


class ReturnReceiptSerializer(CommandSerializer):
    order_line_id = serializers.IntegerField(min_value=1)
    batch_id = serializers.IntegerField(min_value=1)
    qty_returned = serializers.IntegerField(min_value=1, max_value=1000000)
    seal_intact = serializers.BooleanField()
    storage_condition_ok = serializers.BooleanField()
    refund_authorized_syp = serializers.IntegerField(min_value=0, default=0)
    refund_paid_syp = serializers.IntegerField(min_value=0, default=0)
    refund_method = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
    return_ref = serializers.CharField(max_length=120, required=False, allow_blank=True)

    def validate(self, data):
        if data['refund_paid_syp'] > data['refund_authorized_syp']:
            raise serializers.ValidationError('المبلغ المعاد لا يمكن أن يتجاوز المبلغ المصرّح به.')
        if data['refund_paid_syp'] and not data['refund_method'].strip():
            raise serializers.ValidationError('سجّل طريقة دفع المبلغ المعاد.')
        return data


class ReturnInspectionSerializer(CommandSerializer):
    approve_restock = serializers.BooleanField()


class ZoneUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryZone
        fields = ['fee_syp', 'is_active', 'estimated_hours']

    def validate(self, data):
        active = data.get('is_active', self.instance.is_active)
        fee = data.get('fee_syp', self.instance.fee_syp)
        if fee is not None and fee < 0:
            raise serializers.ValidationError('أجور التوصيل يجب أن تكون صفراً أو أكثر.')
        if active and fee is None:
            raise serializers.ValidationError('حدد أجور التوصيل قبل تفعيل الحي.')
        hours = data.get('estimated_hours', self.instance.estimated_hours)
        if hours < 1 or hours > 720:
            raise serializers.ValidationError('مدة التوصيل يجب أن تكون بين ساعة و720 ساعة.')
        return data


class LoginSerializer(CommandSerializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=256, trim_whitespace=False)


class PublicVariantSerializer(serializers.ModelSerializer):
    available_quantity = serializers.SerializerMethodField()
    orderable = serializers.SerializerMethodField()
    class Meta:
        model = ProductVariant
        fields = ['sku', 'pack_size_label_ar', 'volume_ml', 'weight_g', 'price_syp', 'currency', 'pao_months', 'image_url', 'available_quantity', 'orderable']
    def get_available_quantity(self, obj):
        return InventoryOrderService.get_available_quantity(obj.sku)
    def get_orderable(self, obj):
        return bool(obj.is_active and obj.product.is_published and self.get_available_quantity(obj) > 0)


class PublicProductSerializer(serializers.ModelSerializer):
    catalog_origin = serializers.SerializerMethodField()
    brand_slug = serializers.CharField(source='brand.slug', read_only=True)
    brand_name_en = serializers.CharField(source='brand.name_en', read_only=True)
    brand_name_ar = serializers.CharField(source='brand.name_ar', read_only=True)
    category_slug = serializers.CharField(source='category.slug', read_only=True)
    category_name_ar = serializers.CharField(source='category.name_ar', read_only=True)
    variants = serializers.SerializerMethodField()
    class Meta:
        model = Product
        fields = ['slug', 'name_ar', 'name_en', 'brand_name_en', 'brand_name_ar', 'category_slug', 'category_name_ar', 'description_ar', 'ingredients_text', 'directions_ar', 'warnings_ar', 'concern_tags', 'variants', 'catalog_origin', 'brand_slug']
    def get_catalog_origin(self, obj):
        return 'local'
    def get_variants(self, obj):
        return PublicVariantSerializer([v for v in obj.variants.all() if v.is_active], many=True).data


class PublicDeliveryZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryZone
        fields = ['code', 'city', 'district_name_ar', 'district_name_en', 'fee_syp', 'estimated_hours']


class ProtectedOrderTrackingSerializer(serializers.ModelSerializer):
    status_label_ar = serializers.CharField(source='get_status_display', read_only=True)
    lines = serializers.SerializerMethodField()
    class Meta:
        model = Order
        fields = ['order_number', 'status', 'status_label_ar', 'city', 'district_snapshot_ar', 'currency', 'subtotal_syp', 'delivery_fee_syp', 'total_syp', 'hold_expires_at', 'created_at', 'lines']
    def get_lines(self, obj):
        return [{'sku': l.sku_snapshot, 'product_name_ar': l.product_name_ar_snapshot, 'pack_size_ar': l.pack_size_ar_snapshot, 'unit_price_syp': l.unit_price_syp_snapshot, 'quantity': l.quantity, 'line_total_syp': l.line_total_syp_snapshot} for l in obj.lines.all()]


class StaffBatchSerializer(serializers.ModelSerializer):
    is_return_quarantine = serializers.SerializerMethodField()
    def get_is_return_quarantine(self,obj): return obj.quarantine_returns.exists()
    sku = serializers.CharField(source='variant.sku', read_only=True)
    product_name_ar = serializers.CharField(source='variant.product.name_ar', read_only=True)
    class Meta:
        model = InventoryBatch
        fields = ['id', 'sku', 'product_name_ar', 'lot_code', 'condition', 'is_return_quarantine', 'expiry_date', 'documented_unopened_shelflife_date', 'qty_on_hand', 'qty_reserved', 'unit_cost_syp', 'supplier_invoice_ref', 'validity_evidence_ref']
    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self.context.get('include_cost', False):
            data.pop('unit_cost_syp', None)
        return data


class StaffReturnSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source='order_line.sku_snapshot', read_only=True)
    order_number = serializers.CharField(source='order_line.order.order_number', read_only=True)
    class Meta:
        model = ReturnRecord
        fields = ['id', 'order_line_id', 'batch_id', 'sku', 'order_number', 'qty_returned', 'seal_intact', 'storage_condition_ok', 'inspection_status', 'refund_authorized_syp', 'refund_paid_syp', 'refund_method', 'inspected_by', 'created_at']


class StaffRemittanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourierRemittance
        fields = ['id', 'remittance_ref', 'courier_name', 'gross_collected_syp', 'courier_deductions_syp', 'net_remitted_syp', 'explanation', 'received_at', 'recorded_by']


class RefundPaymentCommandSerializer(CommandSerializer):
    amount_syp = serializers.IntegerField(min_value=1)
    payment_ref = serializers.CharField(max_length=128)
    refund_method = serializers.CharField(max_length=64)

class StaffRefundPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model=RefundPayment
        fields=['id','return_record_id','payment_ref','amount_syp','refund_method','actor','created_at']


class ReviewBatchSerializer(CommandSerializer):
    approve_saleable = serializers.BooleanField(default=True)
    reason = serializers.CharField(max_length=1000, required=False, allow_blank=True, default='')
    expiry_date = serializers.DateField(required=False, allow_null=True, default=None)
    documented_unopened_shelflife_date = serializers.DateField(required=False, allow_null=True, default=None)
    validity_evidence_ref = serializers.CharField(max_length=2000)
