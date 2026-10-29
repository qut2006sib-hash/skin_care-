"""HTTP contracts, permissions, browser sessions and complete fulfillment."""
from datetime import timedelta
import pytest
from django.contrib import admin
from django.contrib.auth.models import User,Group
from django.test import Client,RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient
from apps.commerce.models import Brand,Category,Product,ProductVariant,DeliveryZone,InventoryBatch,Order,ReturnRecord
from apps.commerce.services import InventoryOrderService as Service
from apps.commerce.admin import ProductForm
pytestmark=pytest.mark.django_db(transaction=True)

@pytest.fixture
def store():
    b=Brand.objects.create(slug='api-brand',name_en='Demo',name_ar='تجريبي')
    c=Category.objects.create(slug='care',name_en='Care',name_ar='عناية')
    p=Product.objects.create(brand=b,category=c,slug='api-product',name_ar='منتج تجريبي',name_en='Test',is_published=True)
    v=ProductVariant.objects.create(product=p,sku='API-30',pack_size_label_ar='30 مل',price_syp=100)
    batch=Service.receive_batch(sku=v.sku,lot_code='API-LOT',supplier_invoice_ref='TEST',qty_received=10,unit_cost_syp=40,expiry_date=timezone.localdate()+timedelta(days=180))
    z=DeliveryZone.objects.create(code='api-zone',district_name_ar='حي تجريبي',district_name_en='Test',fee_syp=10,is_active=True)
    return v,batch,z

@pytest.fixture
def owner():
    u=User.objects.create_user('owner',password='example-long-test-password',is_staff=True)
    u.groups.add(Group.objects.get_or_create(name='Owner')[0]);return u

@pytest.fixture
def fulfillment():
    u=User.objects.create_user('fulfill',password='example-long-test-password',is_staff=True)
    u.groups.add(Group.objects.get_or_create(name='Fulfillment_Support_Staff')[0]);return u

def payload():
    return {'customer_name':'زبون تجريبي','customer_phone':'+963900000000','delivery_zone_code':'api-zone','address_landmark_ar':'عنوان تجريبي','items':[{'sku':'API-30','quantity':2}],'expected_total_syp':210}

def auth(u):
    c=APIClient();c.force_authenticate(u);return c

def checkout(c=None,key='api-checkout'):
    return (c or APIClient()).post('/api/v1/checkout/submit/',payload(),format='json',HTTP_IDEMPOTENCY_KEY=key)

def test_catalog_no_cost_or_inactive_variant(store):
    c=APIClient();r=c.get('/api/v1/catalog/products/');assert r.status_code==200
    assert 'unit_cost_syp' not in str(r.data)
    assert r.data['products'][0]['variants'][0]['available_quantity']==10
    v=store[0];v.is_active=False;v.save()
    assert c.get('/api/v1/catalog/products/').data['products'][0]['variants']==[]

def test_checkout_validation_never_allocates(store):
    c=APIClient();assert c.post('/api/v1/checkout/submit/',{},format='json').status_code==400
    bad=payload();bad['items'][0]['quantity']=-1
    assert c.post('/api/v1/checkout/submit/',bad,format='json',HTTP_IDEMPOTENCY_KEY='invalid').status_code==400
    assert Order.objects.count()==0
    store[1].refresh_from_db();assert store[1].qty_reserved==0

def test_checkout_retry_and_price_conflict(store):
    c=APIClient();r=checkout(c);assert r.status_code==201,r.data
    assert checkout(c).data['order_number']==r.data['order_number']
    wrong=payload();wrong['expected_total_syp']=200
    r=c.post('/api/v1/checkout/submit/',wrong,format='json',HTTP_IDEMPOTENCY_KEY='changed')
    assert r.status_code==409 and r.data['code']=='price_changed' and r.data['current_total_syp']==210
    changed=payload();changed['items'][0]['quantity']=1
    assert c.post('/api/v1/checkout/submit/',changed,format='json',HTTP_IDEMPOTENCY_KEY='api-checkout').data['code']=='idempotency_conflict'

def test_private_tracking(store):
    r=checkout();c=APIClient();t=c.get('/api/v1/orders/track/'+r.data['tracking_token']+'/')
    assert t.status_code==200 and 'no-store' in t['Cache-Control']
    assert not {'customer_name','customer_phone','address_landmark_ar','unit_cost_syp'}&set(t.data)
    assert c.get('/api/v1/orders/track/not-a-real-token/').status_code==404

def test_staff_role_matrix(store,owner,fulfillment):
    assert APIClient().get('/api/v1/staff/orders/').status_code==403
    helper=User.objects.create_user('helper',is_staff=True)
    assert auth(helper).get('/api/v1/staff/orders/').status_code==403
    catalog=User.objects.create_user('catalog',is_staff=True);catalog.groups.add(Group.objects.get_or_create(name='Catalog_Inventory_Manager')[0])
    assert auth(catalog).get('/api/v1/staff/orders/').status_code==403
    assert 'unit_cost_syp' in auth(catalog).get('/api/v1/staff/inventory/').data['batches'][0]
    assert 'unit_cost_syp' not in auth(fulfillment).get('/api/v1/staff/inventory/').data['batches'][0]
    assert auth(fulfillment).patch('/api/v1/staff/zones/api-zone/',{'fee_syp':99},format='json').status_code==403
    assert auth(owner).patch('/api/v1/staff/zones/api-zone/',{'fee_syp':20},format='json').status_code==200

def test_receiving_iso_date_and_idempotent_receipt(store,owner):
    c=auth(owner);d={'sku':'API-30','lot_code':'NEW-LOT','supplier_invoice_ref':'INV','qty_received':3,'unit_cost_syp':20,'expiry_date':(timezone.localdate()+timedelta(days=200)).isoformat(),'receipt_ref':'one'}
    assert c.post('/api/v1/staff/batches/receive/',d,format='json').status_code==201
    assert c.post('/api/v1/staff/batches/receive/',d,format='json').status_code==201
    assert InventoryBatch.objects.get(lot_code='NEW-LOT').qty_on_hand==3

def test_http_full_cod_and_return_flow(store,owner):
    c=auth(owner);r=checkout();assert r.status_code==201
    o=Order.objects.get(order_number=r.data['order_number']);base=f'/api/v1/staff/orders/{o.id}/'
    assert c.post(base+'confirm/',{},format='json').status_code==200
    assert c.post(base+'dispatch/',{'courier_name':'مندوب تجريبي'},format='json').status_code==200
    assert c.post(base+'delivered/',{'collected_amount_syp':210,'evidence_ref':'CASH'},format='json').status_code==200
    summary=c.get('/api/v1/staff/remittances/').data['summary']
    assert summary['collected_held_by_courier_syp']==210 and summary['remitted_net_syp']==0
    rem={'remittance_ref':'API-REM','courier_name':'مندوب تجريبي','order_ids':[o.id],'courier_deductions_syp':10,'explanation':'أجرة اختبار'}
    assert c.post('/api/v1/staff/remittances/',rem,format='json').status_code==201
    assert c.get('/api/v1/staff/remittances/').data['summary']['remitted_net_syp']==200
    detail=c.get(base).data
    ret={'order_line_id':detail['lines'][0]['id'],'batch_id':detail['lines'][0]['allocations'][0]['batch_id'],'qty_returned':1,'seal_intact':True,'storage_condition_ok':True,'refund_authorized_syp':100,'refund_paid_syp':100,'refund_method':'نقداً','return_ref':'API-RETURN'}
    rr=c.post('/api/v1/staff/returns/',ret,format='json');assert rr.status_code==201,rr.data
    assert rr.data['inspection_status']=='QUARANTINED'
    assert c.post(f"/api/v1/staff/returns/{rr.data['id']}/inspect/",{'approve_restock':True},format='json').status_code==200
    store[1].refresh_from_db();assert store[1].qty_on_hand==9

def test_csrf_login_and_logout(owner):
    c=Client(enforce_csrf_checks=True);data={'username':'owner','password':'example-long-test-password'}
    assert c.post('/api/v1/auth/login/',data=data,content_type='application/json').status_code==403
    c.get('/api/v1/auth/session/');csrf=c.cookies['csrftoken'].value
    r=c.post('/api/v1/auth/login/',data=data,content_type='application/json',HTTP_X_CSRFTOKEN=csrf)
    assert r.status_code==200,r.content
    assert c.get('/api/v1/staff/orders/').status_code==200
    assert c.post('/api/v1/auth/logout/',data={},content_type='application/json').status_code==403
    assert c.post('/api/v1/auth/logout/',data={},content_type='application/json',HTTP_X_CSRFTOKEN=c.cookies['csrftoken'].value).status_code==200

def test_admin_operational_fields_not_mutable(owner,store):
    req=RequestFactory().get('/admin/');req.user=owner
    for model in (InventoryBatch,Order,ReturnRecord):
        a=admin.site._registry[model]
        assert not a.has_add_permission(req) and not a.has_change_permission(req) and not a.has_delete_permission(req)

def test_publication_needs_evidence(store):
    v=store[0];form=ProductForm(instance=v.product,data={'brand':v.product.brand_id,'category':v.product.category_id,'slug':'api-product','name_ar':'منتج','name_en':'Test','is_published':True})
    assert not form.is_valid() and 'claim_evidence_source' in form.errors

def test_staff_page_and_picklist(store,owner):
    c=Client();assert c.get('/staff/').status_code==200
    o=Order.objects.get(order_number=checkout().data['order_number'])
    assert c.get(f'/staff/orders/{o.id}/picklist/').status_code==403
    c.force_login(owner);r=c.get(f'/staff/orders/{o.id}/picklist/')
    assert r.status_code==200 and 'API-LOT' in r.content.decode() and 'no-store' in r['Cache-Control']

def test_refund_payment_retry_and_outstanding_bound(store,owner):
    v,b,z=store
    order=Service.submit_guest_cod_order(idempotency_key='refund-order',customer_name='Test',customer_phone='+963900000000',delivery_zone_code=z.code,address_landmark_ar='Test',items=[{'sku':v.sku,'quantity':1}])
    Service.confirm_order(order_id=order.pk,actor='owner')
    Service.dispatch_order(order_id=order.pk,courier_name='Courier',actor='owner')
    Service.record_delivery_and_customer_collection(order_id=order.pk,collected_amount_syp=order.total_syp,evidence_ref='CASH',actor='owner')
    ret=Service.process_physical_return(order_line_id=order.lines.get().id,batch_id=b.id,qty_returned=1,seal_intact=True,storage_condition_ok=True,refund_authorized_syp=100,refund_paid_syp=0,inspected_by='owner',return_ref='refund-return')
    c=auth(owner);url=f'/api/v1/staff/returns/{ret.pk}/refund-payments/'
    pay={'amount_syp':60,'payment_ref':'PAY-ONE','refund_method':'نقداً'}
    assert c.post(url,pay,format='json').status_code==201
    assert c.post(url,pay,format='json').status_code==201
    ret.refresh_from_db();assert ret.refund_paid_syp==60
    assert c.post(url,{'amount_syp':50,'payment_ref':'PAY-TWO','refund_method':'نقداً'},format='json').status_code==409
    assert c.get('/api/v1/staff/remittances/').data['summary']['refunds_outstanding_syp']==40

def test_incoming_quarantine_evidence_review(store,owner,fulfillment):
    v,b,z=store
    incoming=Service.receive_batch(sku=v.sku,lot_code='REVIEW-LOT',supplier_invoice_ref='INV',qty_received=2,unit_cost_syp=40,expiry_date=None)
    url=f'/api/v1/staff/batches/{incoming.pk}/review/'
    data={'expiry_date':(timezone.localdate()+timedelta(days=200)).isoformat(),'validity_evidence_ref':'TEST-LABEL'}
    assert auth(fulfillment).post(url,data,format='json').status_code==403
    r=auth(owner).post(url,data,format='json')
    assert r.status_code==200,r.data
    incoming.refresh_from_db();assert incoming.condition=='SALEABLE' and incoming.qty_on_hand==2

def test_authenticated_staff_guest_checkout_does_not_need_staff_csrf(store,owner):
    # Public guest checkout has no ambient user authority; a staff session must
    # not turn an ordinary customer checkout into a staff-authenticated command.
    c=Client(enforce_csrf_checks=True);c.force_login(owner)
    r=c.post('/api/v1/checkout/submit/',data=payload(),content_type='application/json',HTTP_IDEMPOTENCY_KEY='staff-guest')
    assert r.status_code==201,r.content
