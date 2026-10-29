from rest_framework import serializers
from .models import Product, ProductVariant, DeliveryZone, Order, OrderLine
from .services import InventoryOrderService


class PublicVariantSerializer(serializers.ModelSerializer):
    """Exposes customer-facing variant fields. NEVER exposes unit_cost_syp."""
    available_quantity = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = [
            "sku",
            "pack_size_label_ar",
            "volume_ml",
            "weight_g",
            "price_syp",
            "currency",
            "pao_months",
            "image_url",
            "available_quantity",
        ]

    def get_available_quantity(self, obj: ProductVariant) -> int:
        return InventoryOrderService.get_available_quantity(obj.sku)


class PublicProductSerializer(serializers.ModelSerializer):
    brand_name_en = serializers.CharField(source="brand.name_en", read_only=True)
    brand_name_ar = serializers.CharField(source="brand.name_ar", read_only=True)
    category_slug = serializers.CharField(source="category.slug", read_only=True)
    category_name_ar = serializers.CharField(source="category.name_ar", read_only=True)
    variants = PublicVariantSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = [
            "slug",
            "name_ar",
            "name_en",
            "brand_name_en",
            "brand_name_ar",
            "category_slug",
            "category_name_ar",
            "description_ar",
            "ingredients_text",
            "directions_ar",
            "warnings_ar",
            "concern_tags",
            "variants",
        ]


class PublicDeliveryZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryZone
        fields = ["code", "city", "district_name_ar", "district_name_en", "fee_syp", "estimated_hours"]


class ProtectedOrderTrackingSerializer(serializers.ModelSerializer):
    """Minimal guest order tracking view protected by unguessable token."""
    status_label_ar = serializers.CharField(source="get_status_display", read_only=True)
    lines = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "order_number",
            "status",
            "status_label_ar",
            "city",
            "district_snapshot_ar",
            "currency",
            "subtotal_syp",
            "delivery_fee_syp",
            "total_syp",
            "hold_expires_at",
            "created_at",
            "lines",
        ]

    def get_lines(self, obj: Order):
        return [
            {
                "sku": l.sku_snapshot,
                "product_name_ar": l.product_name_ar_snapshot,
                "pack_size_ar": l.pack_size_ar_snapshot,
                "unit_price_syp": l.unit_price_syp_snapshot,
                "quantity": l.quantity,
                "line_total_syp": l.line_total_syp_snapshot,
            }
            for l in obj.lines.all()
        ]
