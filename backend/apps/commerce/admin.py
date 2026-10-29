from django.contrib import admin
from .models import (
    Brand, Category, Product, ProductVariant,
    InventoryBatch, DeliveryZone, Order, OrderLine,
    OrderBatchAllocation, CustomerCashCollection,
    CourierRemittance, ReturnRecord, AuditLog
)

@admin.register(DeliveryZone)
class DeliveryZoneAdmin(admin.ModelAdmin):
    list_display = ("district_name_ar", "code", "city", "fee_syp", "is_active", "estimated_hours")
    list_editable = ("fee_syp", "is_active")
    search_fields = ("district_name_ar", "district_name_en", "code")

@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ("sku", "product", "pack_size_label_ar", "price_syp", "currency", "min_remaining_validity_days", "is_active")
    search_fields = ("sku", "barcode", "product__name_ar", "product__name_en")

@admin.register(InventoryBatch)
class InventoryBatchAdmin(admin.ModelAdmin):
    list_display = ("variant", "lot_code", "condition", "expiry_date", "qty_on_hand", "qty_reserved", "supplier_invoice_ref")
    list_filter = ("condition", "variant__product__brand")
    search_fields = ("lot_code", "variant__sku", "supplier_invoice_ref")

class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0
    readonly_fields = ("sku_snapshot", "product_name_ar_snapshot", "pack_size_ar_snapshot", "unit_price_syp_snapshot", "quantity", "line_total_syp_snapshot")

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "status", "customer_name", "district_snapshot_ar", "total_syp", "hold_expires_at", "created_at")
    list_filter = ("status", "delivery_zone")
    readonly_fields = ("order_number", "tracking_token", "idempotency_key", "subtotal_syp", "delivery_fee_syp", "total_syp")
    inlines = [OrderLineInline]

admin.site.register(Brand)
admin.site.register(Category)
admin.site.register(Product)
admin.site.register(OrderBatchAllocation)
admin.site.register(CustomerCashCollection)
admin.site.register(CourierRemittance)
admin.site.register(ReturnRecord)
admin.site.register(AuditLog)
