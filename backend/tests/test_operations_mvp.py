"""Operational acceptance checks run against the actual Django application."""
import io
import json
from datetime import timedelta
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from apps.commerce.models import Brand, Category, Product, ProductVariant, InventoryBatch, DeliveryZone, OrderStatus
from apps.commerce.services import InventoryOrderService
from apps.commerce.roles import configure_roles


@pytest.fixture
def demo(db):
    call_command("seed_section15", demo=True, stdout=io.StringIO())
    return DeliveryZone.objects.get(code="homs-al-waer")


def test_demo_requires_explicit_acknowledgement(db):
    with pytest.raises(CommandError, match="--demo"):
        call_command("seed_section15", stdout=io.StringIO())
    assert ProductVariant.objects.count() == 0


def test_demo_repeated_seed_preserves_business_state(demo):
    batch = InventoryBatch.objects.get(lot_code="MED-B5-2601")
    batch.qty_on_hand = 3
    batch.condition = "BLOCKED"
    batch.save()
    variant = batch.variant
    variant.price_syp = 999999
    variant.save()
    variant.product.description_ar = "معلومات عدلها الموظف"
    variant.product.is_published = False
    variant.product.save()
    demo.is_active = True
    demo.fee_syp = 12000
    demo.save()
    call_command("seed_section15", demo=True, stdout=io.StringIO())
    batch.refresh_from_db(); variant.refresh_from_db(); demo.refresh_from_db()
    variant.product.refresh_from_db()
    assert (batch.qty_on_hand, batch.condition) == (3, "BLOCKED")
    assert variant.price_syp == 999999
    assert variant.product.description_ar == "معلومات عدلها الموظف"
    assert variant.product.is_published is False
    assert (demo.is_active, demo.fee_syp) == (True, 12000)
    assert ProductVariant.objects.count() == 10


def test_demo_fixture_has_no_attributed_real_evidence_and_dates_are_relative(demo):
    assert not Product.objects.filter(claim_reviewer="Ab Noah").exists()
    assert set(Product.objects.values_list("claim_evidence_source", flat=True)) == {"DEMO-NOT-VERIFIED"}
    dated = InventoryBatch.objects.exclude(expiry_date=None)
    assert all(b.expiry_date >= timezone.localdate() + timedelta(days=180) for b in dated)
    assert InventoryBatch.objects.get(lot_code="FEM-MSK-2609-HOLD").condition == "QUARANTINE"
    assert not DeliveryZone.objects.filter(is_active=True).exists()


def test_roles_allow_catalog_management_without_generic_operational_writes(db):
    groups = configure_roles()
    assert groups["Owner"].permissions.filter(codename="change_deliveryzone").exists()
    assert groups["Catalog_Inventory_Manager"].permissions.filter(codename="change_product").exists()
    assert groups["Fulfillment_Support_Staff"].permissions.filter(codename="view_order").exists()
    for group in groups.values():
        assert not group.permissions.filter(codename__in=["change_order", "change_inventorybatch", "delete_order", "change_returnrecord"]).exists()
    configure_roles()
    assert groups["Owner"].permissions.filter(codename="change_deliveryzone").count() == 1


def _order(zone, key, now):
    zone.is_active = True
    zone.fee_syp = 1000
    zone.save()
    return InventoryOrderService.submit_guest_cod_order(
        idempotency_key=key, customer_name="اختبار", customer_phone="+963991234567",
        delivery_zone_code=zone.code, address_landmark_ar="عنوان اختبار", items=[{"sku":"MEDEE-HYDRA-B5-30ML", "quantity":1}], now=now,
    )


def test_worker_expires_pending_once_and_preserves_confirmed(demo):
    past = timezone.now() - timedelta(hours=13)
    pending = _order(demo, "ops-pending", past)
    confirmed = _order(demo, "ops-confirmed", past)
    InventoryOrderService.confirm_order(order_id=confirmed.pk, actor="test", now=past + timedelta(hours=1))
    batch = InventoryBatch.objects.get(lot_code="MED-B5-2601")
    assert batch.qty_reserved == 2
    call_command("expire_overdue_holds", stdout=io.StringIO())
    call_command("expire_overdue_holds", stdout=io.StringIO())
    pending.refresh_from_db(); confirmed.refresh_from_db(); batch.refresh_from_db()
    assert pending.status == OrderStatus.EXPIRED
    assert confirmed.status == OrderStatus.CONFIRMED
    assert batch.qty_reserved == 1


def test_worker_rejects_unbounded_configuration(db):
    with pytest.raises(CommandError):
        call_command("expire_overdue_holds", interval=0, stdout=io.StringIO())
    with pytest.raises(CommandError):
        call_command("expire_overdue_holds", limit=10001, stdout=io.StringIO())


def _catalog_row(**changes):
    row = {
        "sku":"REVIEWED-30ML", "brand_slug":"review-brand", "category_slug":"review-category",
        "name_ar":"منتج موثق", "name_en":"Reviewed product", "pack_size_label_ar":"30 مل",
        "price_syp":25000, "volume_ml":30, "ingredients_text":"Manufacturer INCI reference",
        "directions_ar":"تعليمات الملصق", "warnings_ar":"تحذيرات الملصق",
        "claim_evidence_source":"labels/reviewed.pdf#page=1", "claim_checked_date":timezone.localdate().isoformat(),
        "claim_reviewer":"Catalog reviewer", "concern_tags":["عناية"], "is_published":True,
    }
    row.update(changes)
    return row


@pytest.fixture
def catalog_refs(db):
    Brand.objects.create(slug="review-brand", name_ar="ماركة", name_en="Brand")
    Category.objects.create(slug="review-category", name_ar="فئة", name_en="Category")


def _write(tmp_path, rows):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_catalog_import_dry_run_and_real_import_are_stock_free(catalog_refs, tmp_path):
    path = _write(tmp_path, [_catalog_row()])
    call_command("import_catalog", path, dry_run=True, stdout=io.StringIO())
    assert ProductVariant.objects.count() == 0
    call_command("import_catalog", path, stdout=io.StringIO())
    variant = ProductVariant.objects.get(sku="REVIEWED-30ML")
    assert variant.price_syp == 25000 and variant.product.is_published
    assert InventoryBatch.objects.count() == 0


def test_catalog_import_is_atomic_on_late_row_failure(catalog_refs, tmp_path):
    path = _write(tmp_path, [_catalog_row(), _catalog_row(sku="BAD-PRICE", price_syp=-10)])
    with pytest.raises(CommandError, match="Row 2"):
        call_command("import_catalog", path, stdout=io.StringIO())
    assert Product.objects.count() == ProductVariant.objects.count() == 0


def test_catalog_import_rejects_stock_and_missing_review(catalog_refs, tmp_path):
    for row in [_catalog_row(qty_on_hand=100), _catalog_row(claim_evidence_source=""), _catalog_row(claim_reviewer=""), _catalog_row(claim_evidence_source="DEMO fixture")]:
        with pytest.raises(CommandError):
            call_command("import_catalog", _write(tmp_path, [row]), stdout=io.StringIO())
    assert ProductVariant.objects.count() == 0


def test_catalog_import_validates_entire_duplicate_input(catalog_refs, tmp_path):
    with pytest.raises(CommandError, match="duplicate SKU"):
        call_command("import_catalog", _write(tmp_path, [_catalog_row(), _catalog_row()]), stdout=io.StringIO())
    assert Product.objects.count() == 0


def test_catalog_import_accepts_unreviewed_draft_without_invented_check(catalog_refs, tmp_path):
    row = _catalog_row(is_published=False, claim_checked_date=None, claim_reviewer="", claim_evidence_source="", ingredients_text="", directions_ar="", warnings_ar="", concern_tags=[])
    call_command("import_catalog", _write(tmp_path, [row]), stdout=io.StringIO())
    product = Product.objects.get()
    assert product.is_published is False
    assert product.claim_checked_date is None
    assert product.claim_reviewer == ""
