from datetime import date
from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group
from django.db import transaction
from apps.commerce.models import Brand, Category, Product, ProductVariant, InventoryBatch, DeliveryZone
from apps.commerce.seed_data import BRANDS, CATEGORIES, SKUS, HOMS_DELIVERY_ZONES


class Command(BaseCommand):
    help = "Seeds the 10 representative Section 15 SKUs, Homs delivery zones, and RBAC staff groups."

    @transaction.atomic
    def handle(self, *args, **options):
        for role_name in ["Owner", "Catalog_Inventory_Manager", "Fulfillment_Support_Staff"]:
            Group.objects.get_or_create(name=role_name)

        brands_by_slug = {}
        for b in BRANDS:
            obj, _ = Brand.objects.update_or_create(slug=b["slug"], defaults=b)
            brands_by_slug[b["slug"]] = obj

        cats_by_slug = {}
        for c in CATEGORIES:
            obj, _ = Category.objects.update_or_create(slug=c["slug"], defaults=c)
            cats_by_slug[c["slug"]] = obj

        for item in SKUS:
            prod, _ = Product.objects.update_or_create(
                slug=item["sku"].lower(),
                defaults={
                    "brand": brands_by_slug[item["brand"]],
                    "category": cats_by_slug[item["category"]],
                    "name_ar": item["name_ar"],
                    "name_en": item["name_en"],
                    "description_ar": item["name_ar"],
                    "ingredients_text": item["ingredients_text"],
                    "directions_ar": item["directions_ar"],
                    "warnings_ar": item["warnings_ar"],
                    "concern_tags": item["concern_tags"],
                    "claim_evidence_source": item["claim_evidence_source"],
                    "claim_checked_date": date(2026, 10, 29),
                    "claim_reviewer": "Ab Noah",
                    "is_published": True,
                },
            )
            variant, _ = ProductVariant.objects.update_or_create(
                sku=item["sku"],
                defaults={
                    "product": prod,
                    "pack_size_label_ar": item["pack_size_label_ar"],
                    "volume_ml": item.get("volume_ml"),
                    "weight_g": item.get("weight_g"),
                    "price_syp": item["price_syp"],
                    "currency": "SYP",
                    "requires_expiry_date": True,
                    "min_remaining_validity_days": item["min_remaining_validity_days"],
                    "pao_months": item["pao_months"],
                    "is_active": True,
                },
            )
            for batch in item["batches"]:
                InventoryBatch.objects.update_or_create(
                    variant=variant,
                    lot_code=batch["lot_code"],
                    defaults={
                        "supplier_invoice_ref": batch["supplier_invoice_ref"],
                        "unit_cost_syp": batch["unit_cost_syp"],
                        "expiry_date": batch["expiry_date"],
                        "condition": batch["condition"],
                        "qty_on_hand": batch["qty_on_hand"],
                        "validity_evidence_ref": batch["validity_evidence_ref"],
                    },
                )

        for z in HOMS_DELIVERY_ZONES:
            DeliveryZone.objects.update_or_create(code=z["code"], defaults=z)

        self.stdout.write(self.style.SUCCESS("Seeded 10 Section 15 SKUs, Homs delivery zones, and RBAC groups."))
