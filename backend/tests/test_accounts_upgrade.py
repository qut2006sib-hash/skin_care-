"""Upgrade a populated 0.3 schema without relabeling legacy guest orders."""
from datetime import timedelta
from decimal import Decimal
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.utils import timezone

pytestmark = pytest.mark.django_db(transaction=True)


def test_populated_pre_accounts_upgrade_preserves_owner_catalog_orders_and_stock():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old_commerce = ('commerce', '0003_suppliercatalogitem')
    auth_leaf = next(node for node in latest if node[0] == 'auth')
    tracked = ('Brand','Category','Product','ProductVariant','InventoryBatch','DeliveryZone','Order','OrderLine','OrderBatchAllocation','SupplierCatalogItem')
    password = 'upgrade-private-owner-7264'
    try:
        executor.migrate([('accounts',None), old_commerce, auth_leaf])
        executor = MigrationExecutor(connection)
        old = executor.loader.project_state([old_commerce,auth_leaf]).apps
        models = {name:old.get_model('commerce',name) for name in tracked}
        User = old.get_model('auth','User')
        Group = old.get_model('auth','Group')
        owner = User.objects.create(username='existing-owner',password=make_password(password),is_active=True,is_staff=True,is_superuser=False)
        group = Group.objects.create(name='Owner')
        owner.groups.add(group)
        brand = models['Brand'].objects.create(slug='upgrade-brand',name_en='Existing brand',name_ar='علامة موجودة')
        category = models['Category'].objects.create(slug='upgrade-category',name_en='Existing',name_ar='فئة موجودة')
        product = models['Product'].objects.create(brand_id=brand.pk,category_id=category.pk,slug='upgrade-product',name_en='Existing product',name_ar='منتج موجود',is_published=True)
        variant = models['ProductVariant'].objects.create(product_id=product.pk,sku='UPGRADE-ONE',pack_size_label_ar='30 مل',price_syp=100)
        batch = models['InventoryBatch'].objects.create(variant_id=variant.pk,lot_code='EXISTING-LOT',supplier_invoice_ref='EXISTING-INVOICE',unit_cost_syp=40,qty_on_hand=10,qty_reserved=2,condition='SALEABLE',expiry_date=timezone.localdate()+timedelta(days=180))
        zone = models['DeliveryZone'].objects.create(code='existing-zone',district_name_ar='حي موجود',district_name_en='Existing',fee_syp=10,is_active=True)
        order = models['Order'].objects.create(order_number='HOMS-LEGACY-001',tracking_token='existing-private-token',idempotency_key='existing-key',request_fingerprint='a'*64,customer_name='عميل قديم',customer_phone='+963900000000',delivery_zone_id=zone.pk,district_snapshot_ar='حي موجود',address_landmark_ar='عنوان محفوظ',subtotal_syp=200,delivery_fee_syp=10,total_syp=210,hold_expires_at=timezone.now()+timedelta(hours=12))
        line = models['OrderLine'].objects.create(order_id=order.pk,variant_id=variant.pk,sku_snapshot=variant.sku,product_name_ar_snapshot=product.name_ar,pack_size_ar_snapshot='30 مل',unit_price_syp_snapshot=100,quantity=2,line_total_syp_snapshot=200)
        models['OrderBatchAllocation'].objects.create(order_line_id=line.pk,batch_id=batch.pk,qty_allocated=2,unit_cost_syp_snapshot=40,status='RESERVED')
        models['SupplierCatalogItem'].objects.create(sku='SOURCE-EXISTING',source_name='Existing source',name_ar='مصدر موجود',brand_name='MEDEE',brand_name_ar='ميدي',brand_slug='medee',category_slug='care',category_name_ar='عناية',reference_price_usd=Decimal('29.40'),source_repository='qut2006sib-hash/pharmacy-distribution',source_commit='d1384b01e6ac20e05255111783218f3e9b7e76e6',source_path='pharmacy_distribution/field_data.py',is_visible=False)
        before = {name:list(model.objects.order_by('pk').values()) for name,model in models.items()}
        owner_before = User.objects.get(pk=owner.pk).password
        executor.migrate(latest)

        from apps.accounts.models import AccountProfile, Store
        from apps.commerce.models import Order
        from django.apps import apps
        for name, rows in before.items():
            actual = list(apps.get_model('commerce',name).objects.order_by('pk').values())
            if name == 'Order':
                assert all(row.pop('customer_user_id') is None for row in actual)
            assert actual == rows, name
        assert get_user_model().objects.get(pk=owner.pk).password == owner_before
        assert get_user_model().objects.get(pk=owner.pk).groups.filter(name='Owner').exists()
        assert not AccountProfile.objects.exists() and not Store.objects.exists()
        assert Order.objects.get(pk=order.pk).customer_user_id is None
        client = Client()
        response = client.post('/api/v1/auth/login/',{'username':'existing-owner','password':password,'account_type':'manager'},content_type='application/json')
        assert response.status_code == 200, response.content
        assert response.json()['account_type'] == 'manager'
        assert client.get('/api/v1/staff/inventory/').status_code == 200
    finally:
        # Subsequent tests and pytest cleanup always see the current schema.
        MigrationExecutor(connection).migrate(latest)
