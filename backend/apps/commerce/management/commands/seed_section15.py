from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from apps.commerce.models import Brand, Category, Product, ProductVariant, InventoryBatch, DeliveryZone
from apps.commerce.seed_data import BRANDS, CATEGORIES, demo_skus, HOMS_DELIVERY_ZONES
from apps.commerce.roles import configure_roles
from apps.commerce.services import InventoryOrderService


class Command(BaseCommand):
    help = "Insert clearly labelled synthetic demo data without overwriting existing records. Requires --demo."

    def add_arguments(self, parser):
        parser.add_argument("--demo", action="store_true", help="Acknowledge this is synthetic data for a disposable demonstration.")

    @transaction.atomic
    def handle(self, *args, **options):
        if not options["demo"]:
            raise CommandError("Demo fixtures require --demo. Do not seed a real business database.")
        configure_roles()
        brands = {b["slug"]: Brand.objects.get_or_create(slug=b["slug"], defaults={k: v for k, v in b.items() if k != "slug"})[0] for b in BRANDS}
        categories = {c["slug"]: Category.objects.get_or_create(slug=c["slug"], defaults={k: v for k, v in c.items() if k != "slug"})[0] for c in CATEGORIES}
        inserted = 0
        for item in demo_skus():
            # Existing SKU belongs to its current owner/catalog. Do not introduce
            # new demo lots into an existing real SKU on a repeat invocation.
            if ProductVariant.objects.filter(sku=item["sku"]).exists():
                continue
            product, created_product = Product.objects.get_or_create(slug=item["sku"].lower(), defaults={
                "brand": brands[item["brand"]], "category": categories[item["category"]],
                "name_ar": item["name_ar"], "name_en": item["name_en"],
                "description_ar": "سجل تجريبي لا يمثل بيانات مورد أو منتج موثقة.",
                "ingredients_text": item["ingredients_text"], "directions_ar": item["directions_ar"],
                "warnings_ar": item["warnings_ar"], "concern_tags": item["concern_tags"],
                "claim_evidence_source": "DEMO-NOT-VERIFIED", "claim_checked_date": timezone.localdate(),
                "claim_reviewer": "DEMO fixture (not reviewed)", "is_published": True,
            })
            if not created_product:
                # A pre-existing product must not acquire a synthetic variant.
                continue
            variant = ProductVariant.objects.create(
                sku=item["sku"], product=product, pack_size_label_ar=item["pack_size_label_ar"],
                volume_ml=item["volume_ml"], weight_g=item["weight_g"], price_syp=item["price_syp"],
                currency="SYP", requires_expiry_date=True, min_remaining_validity_days=item["min_remaining_validity_days"],
                pao_months=item["pao_months"], is_active=True,
            )
            for batch in item["batches"]:
                if not InventoryBatch.objects.filter(variant=variant, lot_code=batch["lot_code"]).exists():
                    InventoryOrderService.receive_batch(
                        sku=variant.sku, lot_code=batch["lot_code"], supplier_invoice_ref=batch["supplier_invoice_ref"],
                        qty_received=batch["qty_on_hand"], unit_cost_syp=batch["unit_cost_syp"],
                        expiry_date=batch["expiry_date"], validity_evidence_ref=batch["validity_evidence_ref"], actor="DEMO initializer",
                    )
            inserted += 1
        for zone in HOMS_DELIVERY_ZONES:
            DeliveryZone.objects.get_or_create(code=zone["code"], defaults={k: v for k, v in zone.items() if k != "code"})
        self.stdout.write(self.style.WARNING(f"DEMO ONLY: inserted {inserted} synthetic SKUs. Existing catalog, stock and delivery settings preserved. Delivery remains unconfigured."))
