"""Account authorization, private customer history and isolated pharmacy stock.

These exercise persisted business outcomes rather than UI role labels. PostgreSQL
race cases use independent connections; SQLite is diagnostic only.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import close_old_connections, connection, connections
from django.test import Client
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import (
    AccountProfile, Store, StoreProduct, StoreBatch, StoreReceipt, StoreSale,
    StoreSaleLine, StoreSaleAllocation,
)
from apps.commerce.exceptions import CommerceDomainError
from apps.commerce.models import (
    Brand, Category, Product, ProductVariant, InventoryBatch, DeliveryZone, Order,
    CustomerCashCollection, CourierRemittance, StockMovement,
)
from apps.commerce.services import InventoryOrderService

pytestmark = pytest.mark.django_db(transaction=True)
User = get_user_model()
PASSWORD = 'test-account-strong-pass-7284'


def client_for(user):
    client = APIClient()
    client.force_login(user)
    return client


def account(username, kind='customer'):
    user = User.objects.create_user(username, password=PASSWORD)
    AccountProfile.objects.create(user=user, account_type=kind)
    return user


@pytest.fixture
def manager():
    user = User.objects.create_user('platform-manager', password=PASSWORD, is_staff=True)
    user.groups.add(Group.objects.get_or_create(name='Owner')[0])
    return user


@pytest.fixture
def pharmacies():
    first_owner = account('pharmacy-one', 'store')
    second_owner = account('pharmacy-two', 'store')
    first = Store.objects.create(owner=first_owner, name='صيدلية أولى', status='APPROVED')
    second = Store.objects.create(owner=second_owner, name='صيدلية ثانية', status='APPROVED')
    return first, second


def add_product(store, sku='LOCAL-ONE', price=100, threshold=2):
    return StoreProduct.objects.create(store=store, sku=sku, name='منتج الصيدلية', selling_price_syp=price, low_stock_threshold=threshold)


def receipt(reference='INV-ONE', sku='LOCAL-ONE', quantity=5, cost=40, days=180, lot='LOT-ONE'):
    expiry = (timezone.localdate() + timedelta(days=days)).isoformat() if days is not None else None
    return {'reference': reference, 'items': [{'sku': sku, 'lot_code': lot, 'expiry_date': expiry, 'quantity': quantity, 'unit_cost_syp': cost}]}


def post(client, path, data):
    return client.post('/api/v1/' + path, data, format='json')


def successful(response):
    assert response.status_code in (200, 201), getattr(response, 'data', response.content)
    return response.json()


@pytest.fixture
def central_stock():
    brand = Brand.objects.create(slug='warehouse-brand', name_en='Warehouse', name_ar='المستودع')
    category = Category.objects.create(slug='warehouse-care', name_en='Care', name_ar='العناية')
    product = Product.objects.create(brand=brand, category=category, slug='warehouse-product', name_ar='منتج المستودع', name_en='Warehouse product', is_published=True)
    variant = ProductVariant.objects.create(product=product, sku='CENTRAL-ONE', pack_size_label_ar='30 مل', price_syp=100)
    batch = InventoryOrderService.receive_batch(sku=variant.sku, lot_code='CENTRAL-LOT', supplier_invoice_ref='CENTRAL-INVOICE', qty_received=20, unit_cost_syp=40, expiry_date=timezone.localdate()+timedelta(days=180))
    zone = DeliveryZone.objects.create(code='central-zone', district_name_ar='حي اختبار', district_name_en='Test', fee_syp=10, is_active=True)
    return variant, batch, zone


def checkout(client, key, stock):
    return client.post('/api/v1/checkout/submit/', {'customer_name':'عميل', 'customer_phone':'+963900000000', 'delivery_zone_code':stock[2].code, 'address_landmark_ar':'عنوان اختبار', 'items':[{'sku':stock[0].sku,'quantity':1}], 'expected_total_syp':110}, format='json', HTTP_IDEMPOTENCY_KEY=key)


def test_customer_registration_creates_unprivileged_account_and_session():
    client = APIClient()
    data = successful(post(client, 'auth/register/', {'username':'new-customer','password':PASSWORD,'account_type':'customer'}))
    user = User.objects.get(username='new-customer')
    assert not user.is_staff and not user.is_superuser and not user.groups.exists()
    assert user.check_password(PASSWORD)
    assert AccountProfile.objects.get(user=user).account_type == 'customer'
    assert data['authenticated'] and data['account_type'] == 'customer' and data['dashboard'] == '/account'
    assert client.get('/api/v1/auth/session/').json()['username'] == user.username
    assert 'password' not in data


def test_store_registration_is_pending_and_needs_manager_approval():
    client = APIClient()
    data = successful(post(client, 'auth/register/', {'username':'new-pharmacy','password':PASSWORD,'account_type':'store','store_name':'صيدلية جديدة'}))
    user = User.objects.get(username='new-pharmacy')
    store = Store.objects.get(owner=user)
    assert store.status == 'PENDING' and store.name == 'صيدلية جديدة'
    assert not user.is_staff and not user.is_superuser
    assert data['account_type'] == 'store' and data['dashboard'] == '/store'
    assert post(client, 'store/products/', {'sku':'NOT-READY','name':'منتج','selling_price_syp':100,'low_stock_threshold':1}).status_code == 403


@pytest.mark.parametrize('extra', [
    {'account_type':'manager'}, {'is_staff':True}, {'is_superuser':True},
    {'groups':['Owner']}, {'role':'Owner'},
])
def test_public_registration_cannot_create_or_promote_manager(extra):
    payload = {'username':'attempted-admin','password':PASSWORD,'account_type':'customer',**extra}
    response = post(APIClient(), 'auth/register/', payload)
    assert response.status_code == 400
    assert not User.objects.filter(username='attempted-admin').exists()


def test_registration_duplicate_username_and_missing_store_name_are_atomic():
    account('existing')
    client = APIClient()
    assert post(client, 'auth/register/', {'username':'existing','password':PASSWORD,'account_type':'customer'}).status_code == 400
    assert post(client, 'auth/register/', {'username':'incomplete-store','password':PASSWORD,'account_type':'store'}).status_code == 400
    assert not User.objects.filter(username='incomplete-store').exists()
    assert not Store.objects.exists()


def test_short_password_and_invalid_username_rejected():
    client = APIClient()
    assert post(client, 'auth/register/', {'username':'weak-pass','password':'123','account_type':'customer'}).status_code == 400
    assert post(client, 'auth/register/', {'username':'invalid user name!','password':PASSWORD,'account_type':'customer'}).status_code == 400
    assert not User.objects.exists()


def test_sign_in_checks_real_account_type_and_all_accounts_can_logout(manager, pharmacies):
    customer = account('sign-in-customer')
    for user, kind in ((customer,'customer'), (pharmacies[0].owner,'store'), (manager,'manager')):
        client = APIClient()
        wrong = 'manager' if kind != 'manager' else 'customer'
        assert post(client,'auth/login/',{'username':user.username,'password':PASSWORD,'account_type':wrong}).status_code == 403
        data = successful(post(client,'auth/login/',{'username':user.username,'password':PASSWORD,'account_type':kind}))
        assert data['account_type'] == kind
        successful(post(client, 'auth/logout/', {}))
        assert client.get('/api/v1/auth/session/').json()['authenticated'] is False


def test_registration_and_authenticated_mutations_require_csrf(manager):
    client = Client(enforce_csrf_checks=True)
    payload = {'username':'csrf-customer','password':PASSWORD,'account_type':'customer'}
    assert client.post('/api/v1/auth/register/',payload,content_type='application/json').status_code == 403
    client.get('/api/v1/auth/session/')
    token = client.cookies['csrftoken'].value
    response = client.post('/api/v1/auth/register/',payload,content_type='application/json',HTTP_X_CSRFTOKEN=token)
    assert response.status_code in (200,201), response.content
    assert client.post('/api/v1/auth/logout/',{},content_type='application/json').status_code == 403
    assert client.post('/api/v1/auth/logout/',{},content_type='application/json',HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value).status_code == 200


def test_only_manager_can_approve_or_suspend_stores(manager, pharmacies):
    first, second = pharmacies
    first.status = 'PENDING'; first.save()
    endpoint = f'manager/stores/{first.pk}/status/'
    for client in (APIClient(),client_for(account('ordinary')),client_for(second.owner)):
        assert post(client,endpoint,{'status':'APPROVED'}).status_code == 403
        assert client.get('/api/v1/manager/stores/').status_code == 403
    mc = client_for(manager)
    listing = successful(mc.get('/api/v1/manager/stores/'))
    assert {s['id'] for s in listing['stores']} == {first.pk,second.pk}
    assert listing['summary']['pending_stores'] == 1
    successful(post(mc,endpoint,{'status':'APPROVED'}))
    successful(post(client_for(first.owner),'store/products/',{'sku':'FIRST','name':'منتج','selling_price_syp':100,'low_stock_threshold':1}))
    successful(post(mc,endpoint,{'status':'SUSPENDED'}))
    first.refresh_from_db(); assert first.status == 'SUSPENDED'
    assert post(client_for(first.owner),'store/products/',{'sku':'BLOCKED','name':'منتج','selling_price_syp':100,'low_stock_threshold':1}).status_code == 403


def test_customer_and_store_cannot_read_central_staff_records(pharmacies, central_stock):
    for user in (account('ordinary-customer'), pharmacies[0].owner):
        client = client_for(user)
        for path in ('staff/orders/','staff/inventory/','staff/remittances/','staff/zones/'):
            assert client.get('/api/v1/'+path).status_code == 403


def test_customer_history_excludes_other_accounts_and_guest_same_phone(central_stock):
    first = account('customer-first'); second = account('customer-second')
    a = successful(checkout(client_for(first),'customer-first-order',central_stock))
    successful(checkout(client_for(second),'customer-second-order',central_stock))
    guest = successful(checkout(APIClient(),'same-phone-guest',central_stock))
    response = client_for(first).get('/api/v1/customer/orders/')
    assert response.status_code == 200
    data = response.json()
    assert [o['order_number'] for o in data['orders']] == [a['order_number']]
    assert guest['tracking_token'] not in response.content.decode()
    assert 'unit_cost_syp' not in response.content.decode()
    assert Order.objects.get(order_number=a['order_number']).customer_user_id == first.pk
    assert Order.objects.get(order_number=guest['order_number']).customer_user_id is None
    assert 'no-store' in response['Cache-Control']
    assert APIClient().get('/api/v1/customer/orders/').status_code == 403


def test_authenticated_customer_checkout_requires_csrf(central_stock):
    user = account('csrf-checkout')
    client = Client(enforce_csrf_checks=True); client.force_login(user)
    payload = {'customer_name':'عميل','customer_phone':'+963900000000','delivery_zone_code':central_stock[2].code,'address_landmark_ar':'عنوان اختبار','items':[{'sku':central_stock[0].sku,'quantity':1}],'expected_total_syp':110}
    assert client.post('/api/v1/checkout/submit/',payload,content_type='application/json',HTTP_IDEMPOTENCY_KEY='csrf-no-token').status_code == 403
    assert not Order.objects.exists()
    client.get('/api/v1/auth/session/')
    response = client.post('/api/v1/checkout/submit/',payload,content_type='application/json',HTTP_IDEMPOTENCY_KEY='csrf-with-token',HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
    assert response.status_code == 201, response.content
    assert Order.objects.get().customer_user_id == user.pk


def test_checkout_retry_cannot_claim_guest_or_other_customer_order(central_stock):
    guest = successful(checkout(APIClient(),'unclaimable-guest-key',central_stock))
    first = account('order-owner'); second = account('not-order-owner')
    other = client_for(second)
    response = checkout(other,'unclaimable-guest-key',central_stock)
    assert response.status_code == 409 and guest['tracking_token'] not in response.content.decode()
    owned = successful(checkout(client_for(first),'private-owner-key',central_stock))
    repeated = successful(checkout(client_for(first),'private-owner-key',central_stock))
    assert repeated['order_number'] == owned['order_number']
    response = checkout(other,'private-owner-key',central_stock)
    assert response.status_code == 409 and owned['tracking_token'] not in response.content.decode()
    assert not other.get('/api/v1/customer/orders/').json()['orders']
    assert Order.objects.count() == 2


def test_store_dashboard_and_mutations_are_tenant_scoped(pharmacies):
    first, second = pharmacies
    p1 = add_product(first,sku='SHARED-SKU'); p2 = add_product(second,sku='SHARED-SKU',price=900)
    c1 = client_for(first.owner); c2 = client_for(second.owner)
    successful(post(c1,'store/receipts/',receipt(sku=p1.sku)))
    successful(post(c2,'store/receipts/',receipt(sku=p2.sku,quantity=12)))
    d1 = successful(c1.get('/api/v1/store/dashboard/'))
    assert [p['id'] for p in d1['products']] == [p1.pk]
    assert d1['products'][0]['available_quantity'] == 5
    assert len(d1['batches']) == 1 and d1['batches'][0]['quantity'] == 5
    successful(post(c1,'store/sales/',{'reference':'SALE-ONE','items':[{'sku':p1.sku,'quantity':2}]}))
    assert c2.get('/api/v1/store/dashboard/').json()['products'][0]['available_quantity'] == 12
    sale = StoreSale.objects.get(store=first)
    assert post(c2,f'store/sales/{sale.pk}/return/',{'reason':'ليس طلب هذا المتجر'}).status_code == 404
    assert not sale.is_returned


def test_store_receipt_retry_is_atomic_and_changed_payload_conflicts(pharmacies):
    first, _ = pharmacies; product = add_product(first); client = client_for(first.owner)
    payload = receipt()
    successful(post(client,'store/receipts/',payload)); successful(post(client,'store/receipts/',payload))
    assert StoreReceipt.objects.filter(store=first).count() == 1
    assert StoreBatch.objects.get(product=product).quantity == 5
    changed = receipt(quantity=6)
    assert post(client,'store/receipts/',changed).status_code == 409
    assert StoreBatch.objects.get(product=product).quantity == 5
    mixed = receipt(reference='MIXED')
    mixed['items'].append({**mixed['items'][0],'sku':'UNKNOWN','lot_code':'OTHER'})
    assert post(client,'store/receipts/',mixed).status_code in (400,404,409)
    assert StoreReceipt.objects.count() == 1 and StoreBatch.objects.count() == 1


@pytest.mark.parametrize('days', [None,-1,0])
def test_missing_or_expired_expiry_stock_is_quarantined(pharmacies,days):
    first,_ = pharmacies; product = add_product(first); client = client_for(first.owner)
    successful(post(client,'store/receipts/',receipt(days=days)))
    batch = StoreBatch.objects.get(product=product)
    assert batch.condition == 'QUARANTINE'
    assert client.get('/api/v1/store/dashboard/').json()['products'][0]['available_quantity'] == 0
    assert post(client,'store/sales/',{'reference':'NO-EXPIRED-SALE','items':[{'sku':product.sku,'quantity':1}]}).status_code == 409
    assert not StoreSale.objects.exists()


def test_sale_fefo_frozen_cost_profit_and_retry(pharmacies):
    first,_ = pharmacies; product = add_product(first); client = client_for(first.owner)
    # Receive later-expiring stock first; allocation must use expiry, not receipt order.
    successful(post(client,'store/receipts/',receipt(reference='LATE',quantity=3,cost=60,days=200,lot='LATE-LOT')))
    successful(post(client,'store/receipts/',receipt(reference='EARLY',quantity=2,cost=40,days=90,lot='EARLY-LOT')))
    payload = {'reference':'FEFO-SALE','items':[{'sku':product.sku,'quantity':3}]}
    successful(post(client,'store/sales/',payload)); successful(post(client,'store/sales/',payload))
    sale = StoreSale.objects.get(store=first)
    assert sale.total_syp == 300 and sale.cost_syp == 140
    assert {a.batch.lot_code:a.quantity for a in StoreSaleAllocation.objects.filter(line__sale=sale)} == {'EARLY-LOT':2,'LATE-LOT':1}
    assert StoreSaleLine.objects.get(sale=sale).unit_price_syp == 100
    product.selling_price_syp = 999; product.save()
    sale.refresh_from_db(); assert sale.total_syp == 300
    data = successful(client.get('/api/v1/store/dashboard/'))
    assert data['summary']['sales_today_syp'] == 300 and data['summary']['cost_today_syp'] == 140
    assert data['summary']['gross_profit_today_syp'] == 160 and data['summary']['low_stock_count'] == 1
    assert data['products'][0]['available_quantity'] == 2
    assert post(client,'store/sales/',{'reference':'FEFO-SALE','items':[{'sku':product.sku,'quantity':1}]}).status_code == 409
    assert StoreSale.objects.count() == 1


def test_sale_oversell_rejected_without_partial_deduction(pharmacies):
    first,_=pharmacies; p1=add_product(first); p2=add_product(first,sku='NO-STOCK'); client=client_for(first.owner)
    successful(post(client,'store/receipts/',receipt()))
    for items in ([{'sku':p1.sku,'quantity':6}],[{'sku':p1.sku,'quantity':1},{'sku':p2.sku,'quantity':1}]):
        assert post(client,'store/sales/',{'reference':'FAILED-SALE','items':items}).status_code == 409
    assert not StoreSale.objects.exists() and not StoreSaleLine.objects.exists()
    assert StoreBatch.objects.get(product=p1).quantity == 5


def test_full_return_reverses_profit_once_and_quarantines_stock(pharmacies):
    first,_=pharmacies; product=add_product(first); client=client_for(first.owner)
    successful(post(client,'store/receipts/',receipt()))
    successful(post(client,'store/sales/',{'reference':'RETURNED-SALE','items':[{'sku':product.sku,'quantity':2}]}))
    sale=StoreSale.objects.get()
    endpoint=f'store/sales/{sale.pk}/return/'
    successful(post(client,endpoint,{'reason':'مرتجع اختبار'})); successful(post(client,endpoint,{'reason':'مرتجع اختبار'}))
    sale.refresh_from_db(); assert sale.is_returned
    data=successful(client.get('/api/v1/store/dashboard/'))
    assert data['products'][0]['available_quantity'] == 3
    assert sum(b['quantity'] for b in data['batches'] if b['condition']=='QUARANTINE') == 2
    assert data['summary']['sales_today_syp'] == data['summary']['cost_today_syp'] == data['summary']['gross_profit_today_syp'] == 0


def test_pharmacy_operations_never_mutate_central_stock_or_customer_cash(pharmacies,central_stock):
    first,_=pharmacies; product=add_product(first,sku=central_stock[0].sku); client=client_for(first.owner)
    before=StockMovement.objects.count()
    successful(post(client,'store/receipts/',receipt(sku=product.sku)))
    successful(post(client,'store/sales/',{'reference':'COUNTER-SALE','items':[{'sku':product.sku,'quantity':2}]}))
    sale=StoreSale.objects.get()
    successful(post(client,f'store/sales/{sale.pk}/return/',{'reason':'مرتجع محلي'}))
    central_stock[1].refresh_from_db()
    assert central_stock[1].qty_on_hand == 20 and central_stock[1].qty_reserved == 0
    assert StockMovement.objects.count() == before
    assert not Order.objects.exists() and not CustomerCashCollection.objects.exists() and not CourierRemittance.objects.exists()


def test_pending_and_suspended_store_cannot_read_operational_reports(pharmacies):
    first,_=pharmacies; product=add_product(first); client=client_for(first.owner)
    successful(post(client,'store/receipts/',receipt()))
    for status in ('PENDING','SUSPENDED'):
        first.status=status; first.save()
        data=successful(client.get('/api/v1/store/dashboard/'))
        assert data['store']['status'] == status
        assert data['products'] == data['sales'] == data['batches'] == []
        assert post(client,'store/receipts/',receipt(reference='BLOCKED')).status_code == 403
        assert post(client,'store/sales/',{'reference':'BLOCKED','items':[{'sku':product.sku,'quantity':1}]}).status_code == 403


def test_postgresql_store_last_unit_race(pharmacies):
    if connection.vendor != 'postgresql': pytest.skip('PostgreSQL row-lock acceptance')
    from apps.accounts.services import receive_stock, make_sale
    first,_=pharmacies; product=add_product(first)
    receive_stock(store_id=first.pk,**receipt(quantity=1))
    barrier=Barrier(2)
    def buyer(index):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                result=make_sale(store_id=first.pk,reference=f'RACE-{index}',items=[{'sku':product.sku,'quantity':1}])
                return ('ok',result.pk)
            except CommerceDomainError:
                return ('blocked',None)
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(buyer,[1,2]))
    assert sorted(r[0] for r in results)==['blocked','ok']
    assert StoreSale.objects.count()==1 and StoreBatch.objects.get(product=product).quantity==0


def test_postgresql_store_same_reference_race(pharmacies):
    if connection.vendor != 'postgresql': pytest.skip('PostgreSQL same-reference serialization')
    from apps.accounts.services import receive_stock, make_sale
    first,_=pharmacies; product=add_product(first)
    receive_stock(store_id=first.pk,**receipt(quantity=5))
    barrier=Barrier(2)
    def retry(_):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return make_sale(store_id=first.pk,reference='SAME-REF-RACE',items=[{'sku':product.sku,'quantity':2}]).pk
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids=list(pool.map(retry,[1,2]))
    assert ids[0]==ids[1]
    assert StoreSale.objects.count()==1 and StoreBatch.objects.get(product=product).quantity==3


def test_pos_expected_total_guard_and_retry_preserve_frozen_receipt(pharmacies):
    first,_ = pharmacies; product = add_product(first); client = client_for(first.owner)
    successful(post(client,'store/receipts/',receipt()))
    payload = {'reference':'REVIEWED-PRICE-SALE','items':[{'sku':product.sku,'quantity':2}],'expected_total_syp':150}
    response = post(client,'store/sales/',payload)
    assert response.status_code == 409
    assert not StoreSale.objects.exists() and not StoreSaleAllocation.objects.exists()
    assert StoreBatch.objects.get(product=product).quantity == 5
    payload['expected_total_syp'] = 200
    successful(post(client,'store/sales/',payload))
    sale = StoreSale.objects.get()
    assert sale.total_syp == 200 and sale.cost_syp == 80
    product.selling_price_syp = 999; product.save()
    successful(post(client,'store/sales/',payload))
    sale.refresh_from_db()
    assert StoreSale.objects.count() == 1 and sale.total_syp == 200 and sale.cost_syp == 80
    assert StoreBatch.objects.get(product=product).quantity == 3
    assert post(client,'store/sales/',{**payload,'reference':'NEW-SALE-OLD-PRICE'}).status_code == 409
    assert StoreSale.objects.count() == 1 and StoreBatch.objects.get(product=product).quantity == 3
    assert post(client,'store/sales/',{**payload,'expected_total_syp':1998}).status_code == 409


@pytest.mark.parametrize('body', [['not-an-object'], 'not-an-object', 7, True])
@pytest.mark.parametrize('endpoint', ['manager-status','store/products/','store/receipts/','store/sales/','store-return'])
def test_mutation_endpoints_reject_non_object_json_without_server_error(manager,pharmacies,body,endpoint):
    first,_ = pharmacies
    if endpoint == 'manager-status':
        client = client_for(manager)
        path = f'manager/stores/{first.pk}/status/'
    else:
        client = client_for(first.owner)
        path = 'store/sales/999999/return/' if endpoint == 'store-return' else endpoint
    response = post(client,path,body)
    assert response.status_code == 400, getattr(response,'data',response.content)
    first.refresh_from_db()
    assert first.status == 'APPROVED'
    assert not StoreProduct.objects.exists() and not StoreReceipt.objects.exists() and not StoreSale.objects.exists()
