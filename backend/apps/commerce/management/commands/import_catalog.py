"""Validate catalog input atomically; never imports physical stock or money events."""
import csv
import json
import re
from datetime import date
from pathlib import Path
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from apps.commerce.models import Brand, Category, Product, ProductVariant

FIELDS = {
    "sku", "product_slug", "brand_slug", "category_slug", "name_ar", "name_en",
    "pack_size_label_ar", "price_syp", "volume_ml", "weight_g", "min_remaining_validity_days",
    "pao_months", "ingredients_text", "directions_ar", "warnings_ar", "claim_evidence_source",
    "claim_checked_date", "claim_reviewer", "concern_tags", "image_url", "is_published", "is_active",
}
REQUIRED = ("sku", "brand_slug", "category_slug", "name_ar", "name_en", "pack_size_label_ar", "price_syp")
TEXT_LIMITS = {"sku": 64, "product_slug": 120, "brand_slug": 64, "category_slug": 64, "name_ar": 200, "name_en": 200, "pack_size_label_ar": 80, "claim_reviewer": 120, "image_url": 500}


def integer(value, name, minimum=0, nullable=False):
    if nullable and value in (None, ""):
        return None
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError(f"{name} must be a whole non-negative integer")
    result = int(value)
    if result < minimum or result > 9_000_000_000_000:
        raise ValueError(f"{name} is outside the accepted range")
    return result


def boolean(value, name, default=False):
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise ValueError(f"{name} must be true or false")


class Command(BaseCommand):
    help = "Import reviewed catalog CSV/JSON in one transaction. --dry-run validates and rolls everything back. No inventory is imported."

    def add_arguments(self, parser):
        parser.add_argument("file", help="UTF-8 CSV or JSON file (maximum 5 MiB / 1000 rows)")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        path = Path(options["file"])
        try:
            if path.stat().st_size > 5 * 1024 * 1024:
                raise ValueError("file exceeds 5 MiB")
            with path.open(encoding="utf-8-sig", newline="") as source:
                if path.suffix.lower() == ".csv":
                    reader = csv.DictReader(source)
                    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                        raise ValueError("CSV requires unique column headers")
                    rows = list(reader)
                elif path.suffix.lower() == ".json":
                    rows = json.load(source)
                    if isinstance(rows, dict) and set(rows) == {"products"}:
                        rows = rows["products"]
                else:
                    raise ValueError("use .csv or .json")
            if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
                raise ValueError("provide between 1 and 1000 catalog rows")
        except (OSError, UnicodeError, csv.Error, ValueError) as exc:
            raise CommandError(f"Cannot read catalog: {exc}") from exc
        seen = set()
        product_values = {}
        try:
            with transaction.atomic():
                for index, row in enumerate(rows, 1):
                    try:
                        if not isinstance(row, dict) or any(k not in FIELDS for k in row):
                            raise ValueError("unknown field or malformed row; stock/lot fields are not permitted")
                        for key in REQUIRED:
                            if key not in row or row[key] in (None, ""):
                                raise ValueError(f"{key} is required")
                        for key, value in row.items():
                            if key in TEXT_LIMITS and (not isinstance(value, str) or len(value) > TEXT_LIMITS[key]):
                                raise ValueError(f"{key} must be text of at most {TEXT_LIMITS[key]} characters")
                        sku = row["sku"].strip()
                        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", sku):
                            raise ValueError("sku must contain Latin letters, digits, dot, underscore or hyphen")
                        if sku in seen:
                            raise ValueError("duplicate SKU in input")
                        seen.add(sku)
                        brand = Brand.objects.get(slug=row["brand_slug"])
                        category = Category.objects.get(slug=row["category_slug"])
                        existing = ProductVariant.objects.select_related("product").filter(sku=sku).first()
                        slug = row.get("product_slug") or (existing.product.slug if existing else slugify(sku))
                        if not slug or not re.fullmatch(r"[a-zA-Z0-9_-]{1,120}", slug):
                            raise ValueError("product_slug must be a valid slug")
                        if existing and existing.product.slug != slug:
                            raise ValueError("an existing SKU cannot be moved to another product by import")
                        published = boolean(row.get("is_published"), "is_published", default=False)
                        review_date = row.get("claim_checked_date")
                        review_date = date.fromisoformat(review_date) if review_date else None
                        if review_date and review_date > timezone.localdate():
                            raise ValueError("claim_checked_date cannot be in the future")
                        if published:
                            for key in ("ingredients_text", "directions_ar", "warnings_ar", "claim_evidence_source", "claim_reviewer"):
                                if not isinstance(row.get(key), str) or not row[key].strip():
                                    raise ValueError(f"published products require {key}")
                            if not review_date:
                                raise ValueError("published products require claim_checked_date")
                            if row["claim_evidence_source"].upper().startswith("DEMO"):
                                raise ValueError("demo evidence cannot approve a real product for publication")
                        tags = row.get("concern_tags", [])
                        if isinstance(tags, str):
                            tags = json.loads(tags) if tags.strip() else []
                        if not isinstance(tags, list) or len(tags) > 30 or any(not isinstance(t, str) or len(t) > 80 for t in tags):
                            raise ValueError("concern_tags must be a JSON list of short strings")
                        image = row.get("image_url", "")
                        if image and not (image.startswith("https://") or image.startswith("/")):
                            raise ValueError("image_url must be HTTPS or a local absolute path")
                        defaults = {
                            "brand": brand, "category": category, "name_ar": row["name_ar"].strip(),
                            "name_en": row["name_en"].strip(), "description_ar": row["name_ar"].strip(),
                            "ingredients_text": row.get("ingredients_text", ""), "directions_ar": row.get("directions_ar", ""),
                            "warnings_ar": row.get("warnings_ar", ""), "concern_tags": tags,
                            "claim_evidence_source": row.get("claim_evidence_source", ""),
                            "claim_checked_date": review_date,
                            "claim_reviewer": row.get("claim_reviewer", ""), "is_published": published,
                        }
                        comparable = {k: (v.pk if k in ("brand", "category") else v) for k, v in defaults.items()}
                        if slug in product_values and product_values[slug] != comparable:
                            raise ValueError("rows for the same product have conflicting catalog information")
                        product_values[slug] = comparable
                        product = Product.objects.filter(slug=slug).first() or Product(slug=slug)
                        for key, value in defaults.items():
                            setattr(product, key, value)
                        product.full_clean(exclude=["concern_tags"] if not tags else None)
                        product.save()
                        variant = existing or ProductVariant(sku=sku, product=product)
                        variant.pack_size_label_ar = row["pack_size_label_ar"].strip()
                        variant.price_syp = integer(row["price_syp"], "price_syp", minimum=1)
                        variant.volume_ml = integer(row.get("volume_ml"), "volume_ml", minimum=1, nullable=True)
                        variant.weight_g = integer(row.get("weight_g"), "weight_g", minimum=1, nullable=True)
                        variant.min_remaining_validity_days = integer(row.get("min_remaining_validity_days", 90), "min_remaining_validity_days")
                        variant.pao_months = integer(row.get("pao_months"), "pao_months", minimum=1, nullable=True)
                        variant.is_active = boolean(row.get("is_active"), "is_active", default=True)
                        variant.image_url = image
                        variant.currency = "SYP"
                        variant.full_clean()
                        variant.save()
                    except (ValueError, TypeError, ValidationError, Brand.DoesNotExist, Category.DoesNotExist) as exc:
                        raise CommandError(f"Row {index}: {exc}") from exc
                if options["dry_run"]:
                    transaction.set_rollback(True)
        except CommandError:
            raise
        self.stdout.write(self.style.SUCCESS(f"{'Validated (no changes)' if options['dry_run'] else 'Imported'} {len(rows)} catalog rows. No stock changed."))
