"""Reference product import cannot become stock or an accepted order."""
import io
import json
from decimal import Decimal
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework.test import APIClient
from apps.commerce.models import (SupplierCatalogItem, Product, ProductVariant, Brand, Category, InventoryBatch, Order, OrderBatchAllocation, CustomerCashCollection, CourierRemittance, StockMovement)
from apps.commerce.management.commands import import_pharmacy_catalog as importer

SOURCE_COMMIT = 'd1384b01e6ac20e05255111783218f3e9b7e76e6'
pytestmark = pytest.mark.django_db

@pytest.fixture
def source_catalog():
    call_command('import_pharmacy_catalog', stdout=io.StringIO())
    return APIClient()

def test_snapshot_has_only_sanitized_product_fields():
    data = json.loads(importer.SNAPSHOT.read_text())
    assert data['source'] == {'repository': 'qut2006sib-hash/pharmacy-distribution', 'commit': SOURCE_COMMIT, 'path': 'pharmacy_distribution/field_data.py'}
    assert len(data['products']) == 18
    assert all(set(p) == {'sku','name','brand','price_usd'} for p in data['products'])

def test_import_has_exact_source_names_prices_without_operational_records(source_catalog):
    expected = json.loads(importer.SNAPSHOT.read_text())['products']
    assert SupplierCatalogItem.objects.count() == 18
    for row in expected:
        item = SupplierCatalogItem.objects.get(sku=row['sku'])
        assert item.source_name == row['name']
        assert item.brand_name == row['brand']
        assert item.reference_price_usd == Decimal(row['price_usd'])
        assert item.source_commit == SOURCE_COMMIT
    assert SupplierCatalogItem.objects.get(sku='MEDEE-VIT-C').reference_price_usd == Decimal('29.40')
    assert SupplierCatalogItem.objects.get(sku='FEME-CAPSULES-60').reference_price_usd == Decimal('35.80')
    for model in (Product, ProductVariant, InventoryBatch, Order, OrderBatchAllocation, CustomerCashCollection, CourierRemittance, StockMovement):
        assert model.objects.count() == 0

def test_import_repeats_without_duplicates_and_preserves_visibility(source_catalog):
    item = SupplierCatalogItem.objects.get(sku='MEDEE-VIT-C')
    item.is_visible = False
    item.save()
    pk = item.pk
    call_command('import_pharmacy_catalog', stdout=io.StringIO())
    item.refresh_from_db()
    assert item.pk == pk and item.is_visible is False
    assert SupplierCatalogItem.objects.count() == 18
    assert len(source_catalog.get('/api/v1/catalog/products/').data['products']) == 17

def test_source_listing_public_contract(source_catalog):
    r = source_catalog.get('/api/v1/catalog/products/')
    assert r.status_code == 200
    assert len(r.data['products']) == 18
    product = next(p for p in r.data['products'] if p['variants'][0]['sku'] == 'MEDEE-VIT-C')
    assert product['catalog_origin'] == 'pharmacy-distribution'
    assert product['name_en'] == 'MEDEE SERUM (Vit C)'
    assert product['source']['commit'] == SOURCE_COMMIT
    assert product['ingredients_text'] == product['directions_ar'] == product['warnings_ar'] == ''
    variant = product['variants'][0]
    assert variant['price_syp'] is None
    assert variant['available_quantity'] == 0 and variant['orderable'] is False
    assert variant['reference_price'] == {'amount':'29.40','currency':'USD'}
    assert variant['pack_size_label_ar'] == 'الحجم غير موثق'
    assert variant['image_url'] == ''
    assert not any(key in str(r.data) for key in ['unit_cost_syp', 'customer_phone', 'pharmacies', 'customer_name'])

def test_source_catalog_filters(source_catalog):
    def get(**params): return source_catalog.get('/api/v1/catalog/products/', params).data['products']
    assert len(get(brand='medee')) == 7
    assert len(get(brand='juve-lab')) == 8
    assert len(get(brand='feme-hair')) == 3
    assert len(get(category='cleansers')) == 3
    assert len(get(category='accessories')) == 2
    assert len(get(q='MEDEE-VIT-C')) == 1
    assert len(get(q='شامبو')) == 1
    assert len(get(brand='medee', category='cleansers')) == 0
    assert len(get(concern='ترطيب')) == 0
    assert len(get(q='no-such-product')) == 0

def local_product(sku='MEDEE-VIT-C', published=True):
    b = Brand.objects.create(slug='medee', name_en='MEDEE', name_ar='ميدي')
    c = Category.objects.create(slug='serums', name_en='Serums', name_ar='سيرومات')
    p = Product.objects.create(slug='approved-medee-vit-c', brand=b, category=c, name_ar='سيروم محلي موثق', name_en='Reviewed local product', is_published=published)
    return ProductVariant.objects.create(product=p, sku=sku, pack_size_label_ar='30 مل', price_syp=25000)

def test_published_same_sku_supersedes_source_without_duplicate(source_catalog):
    v = local_product()
    r = source_catalog.get('/api/v1/catalog/products/')
    assert len(r.data['products']) == 18
    matches = [p for p in r.data['products'] if any(x['sku'] == v.sku for x in p['variants'])]
    assert len(matches) == 1 and matches[0]['catalog_origin'] == 'local'
    assert matches[0]['variants'][0]['price_syp'] == 25000
    call_command('import_pharmacy_catalog', stdout=io.StringIO())
    v.refresh_from_db()
    assert v.price_syp == 25000 and v.product.name_ar == 'سيروم محلي موثق'
    assert v.product.is_published

def test_unpublished_local_draft_does_not_hide_source(source_catalog):
    local_product(published=False)
    matches = source_catalog.get('/api/v1/catalog/products/', {'q':'MEDEE-VIT-C'}).data['products']
    assert len(matches) == 1 and matches[0]['catalog_origin'] == 'pharmacy-distribution'

def test_source_only_sku_cannot_be_bought_even_with_reference_price(source_catalog):
    from apps.commerce.models import DeliveryZone
    DeliveryZone.objects.create(code='test-zone', district_name_ar='حي اختبار', district_name_en='Test', fee_syp=1000, is_active=True)
    payload = {'customer_name':'اختبار','customer_phone':'+963900000000','delivery_zone_code':'test-zone','address_landmark_ar':'عنوان اختبار','items':[{'sku':'MEDEE-VIT-C','quantity':1}],'expected_total_syp':2940}
    response = source_catalog.post('/api/v1/checkout/submit/', payload, format='json', HTTP_IDEMPOTENCY_KEY='source-must-not-order')
    assert response.status_code == 409
    assert response.data['code'] == 'operation_conflict'
    assert Order.objects.count() == 0 and InventoryBatch.objects.count() == 0

def test_source_import_dry_run_rolls_back():
    call_command('import_pharmacy_catalog', dry_run=True, stdout=io.StringIO())
    assert SupplierCatalogItem.objects.count() == 0

def test_source_import_failure_is_atomic(tmp_path, monkeypatch):
    bad = json.loads(importer.SNAPSHOT.read_text())
    bad['products'][-1]['price_usd'] = 'NOT-USD'
    path = tmp_path / 'bad.json'
    path.write_text(json.dumps(bad))
    monkeypatch.setattr(importer, 'SNAPSHOT', path)
    with pytest.raises(CommandError):
        call_command('import_pharmacy_catalog', stdout=io.StringIO())
    assert SupplierCatalogItem.objects.count() == 0
