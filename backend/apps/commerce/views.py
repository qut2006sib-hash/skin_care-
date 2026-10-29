import json
from functools import wraps
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from django.db import connection, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_POST
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from .models import Product, ProductVariant, DeliveryZone, Order, InventoryBatch, CourierRemittance, ReturnRecord, AuditLog
from .serializers import (PublicProductSerializer, PublicDeliveryZoneSerializer, ProtectedOrderTrackingSerializer, CheckoutSerializer, ReceiveBatchSerializer, ReasonSerializer, DispatchSerializer, CollectionSerializer, RemittanceSerializer, ReturnReceiptSerializer, ReturnInspectionSerializer, ZoneUpdateSerializer, LoginSerializer, StaffBatchSerializer, StaffReturnSerializer, StaffRemittanceSerializer, RefundPaymentCommandSerializer, StaffRefundPaymentSerializer, ReviewBatchSerializer)
from .services import InventoryOrderService
from .permissions import IsCommerceStaff, IsOwnerStaff, IsCatalogOrOwnerStaff, IsFulfillmentOrOwnerStaff, has_role
from .exceptions import CommerceDomainError


def command_errors(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except CommerceDomainError as exc:
            code = {'PriceChangedError': 'price_changed', 'IdempotencyConflictError': 'idempotency_conflict'}.get(type(exc).__name__, 'operation_conflict')
            data = {'detail': str(exc), 'code': code}
            if hasattr(exc, 'current_total_syp'):
                data['current_total_syp'] = exc.current_total_syp
            return Response(data, status=409)
        except ObjectDoesNotExist:
            return Response({'detail': 'السجل المطلوب غير موجود أو غير متاح.', 'code': 'not_found'}, status=404)
        except (ValueError, DjangoValidationError) as exc:
            return Response({'detail': 'تعذر تنفيذ العملية؛ راجع القيم المدخلة.', 'errors': str(exc)}, status=400)
    return wrapped


def validated(cls, request, **kwargs):
    data = cls(data=request.data, **kwargs)
    data.is_valid(raise_exception=True)
    return data.validated_data


def private(response):
    response['Cache-Control'] = 'no-store, private'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


def user_data(user):
    if not user.is_authenticated:
        return {'authenticated': False}
    return {'authenticated': True, 'username': user.username, 'roles': list(user.groups.values_list('name', flat=True)), 'is_owner': has_role(user, ('Owner',)), 'can_catalog': has_role(user, ('Owner','Catalog_Inventory_Manager')), 'can_fulfill': has_role(user, ('Owner','Fulfillment_Support_Staff')), 'is_staff': has_role(user)}


@api_view(['GET'])
def config_view(request):
    return Response({'demo_mode':getattr(settings,'DEMO_MODE',True),'store_name':getattr(settings,'STORE_NAME','متجر العناية'),'support_message':'للاستفسار عن طلبك احتفظ برقم الطلب ورابط التتبع الخاص.'})


@api_view(['GET'])
def health_view(request):
    with connection.cursor() as cur:
        cur.execute('SELECT 1')
    return Response({'status': 'ok'})


@ensure_csrf_cookie
@never_cache
@api_view(['GET'])
def session_view(request):
    return private(Response(user_data(request.user)))


@require_POST
@csrf_protect
@never_cache
def login_view(request):
    try:
        raw = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'detail':'صيغة الطلب غير صحيحة.'}, status=400)
    serializer = LoginSerializer(data=raw)
    if not serializer.is_valid():
        return JsonResponse({'detail':'أدخل اسم المستخدم وكلمة المرور.', 'errors':serializer.errors}, status=400)
    user = authenticate(request, **serializer.validated_data)
    if user is None or not has_role(user):
        return JsonResponse({'detail':'بيانات الدخول غير صحيحة أو الحساب غير مصرح له.'}, status=403)
    login(request, user)
    return JsonResponse(user_data(user))


@api_view(['POST'])
@permission_classes([IsCommerceStaff])
def logout_view(request):
    logout(request)
    return private(Response({'authenticated':False}))


@api_view(['GET'])
def catalog_products_view(request):
    qs = Product.objects.filter(is_published=True).select_related('brand','category').prefetch_related('variants')
    for param, lookup in [('category','category__slug'), ('brand','brand__slug')]:
        if request.query_params.get(param):
            qs = qs.filter(**{lookup:request.query_params[param]})
    if request.query_params.get('q'):
        q = request.query_params['q'][:200]
        qs = qs.filter(Q(name_ar__icontains=q)|Q(name_en__icontains=q))
    data = PublicProductSerializer(qs.order_by('id'), many=True).data
    if request.query_params.get('concern'):
        data = [p for p in data if request.query_params['concern'] in p.get('concern_tags',[])]
    return Response({'currency':'SYP','city':'Homs','demo_mode':getattr(settings,'DEMO_MODE',True),'products':data})


@api_view(['GET'])
def active_delivery_zones_view(request):
    return Response(PublicDeliveryZoneSerializer(DeliveryZone.objects.filter(city='Homs',is_active=True,fee_syp__isnull=False),many=True).data)


@api_view(['POST'])
@authentication_classes([])
@command_errors
def guest_checkout_submit_view(request):
    key = request.headers.get('Idempotency-Key') or request.data.get('idempotency_key')
    if not isinstance(key,str) or not key.strip() or len(key)>128:
        return Response({'detail':'معرّف محاولة الطلب مطلوب ويجب ألا يتجاوز 128 حرفاً.'},status=400)
    data = validated(CheckoutSerializer,request)
    order = InventoryOrderService.submit_guest_cod_order(idempotency_key=key,**data)
    return private(Response({'order_number':order.order_number,'tracking_token':order.tracking_token,'status':order.status,'total_syp':order.total_syp,'currency':order.currency,'hold_expires_at':order.hold_expires_at},status=201))


@api_view(['GET'])
def protected_order_tracking_view(request,tracking_token):
    order = get_object_or_404(Order.objects.prefetch_related('lines'),tracking_token=tracking_token)
    return private(Response(ProtectedOrderTrackingSerializer(order).data))


def staff_order(order):
    data = dict(ProtectedOrderTrackingSerializer(order).data)
    data.update(id=order.id,customer_name=order.customer_name,customer_phone=order.customer_phone,address_landmark_ar=order.address_landmark_ar)
    lines=[]
    for line in order.lines.all():
        lines.append({'id':line.id,'sku':line.sku_snapshot,'product_name_ar':line.product_name_ar_snapshot,'quantity':line.quantity,'unit_price_syp':line.unit_price_syp_snapshot,'allocations':[{'batch_id':a.batch_id,'lot_code':a.batch.lot_code,'expiry_date':a.batch.effective_validity_date,'quantity':a.qty_allocated,'status':a.status} for a in line.allocations.select_related('batch').all()]})
    data['lines']=lines
    collection=getattr(order,'cash_collection',None)
    data['collection']=None if collection is None else {'status':collection.status,'expected_amount_syp':collection.expected_amount_syp,'collected_amount_syp':collection.collected_amount_syp,'courier_name':collection.courier_name,'remittance_id':collection.remittance_id}
    return data


@api_view(['GET'])
@permission_classes([IsFulfillmentOrOwnerStaff])
def staff_orders_view(request):
    qs=Order.objects.select_related('cash_collection').prefetch_related('lines__allocations__batch').order_by('-created_at')
    if request.query_params.get('status'): qs=qs.filter(status=request.query_params['status'])
    if request.query_params.get('q'):
        q=request.query_params['q'][:160]
        qs=qs.filter(Q(order_number__icontains=q)|Q(customer_name__icontains=q)|Q(customer_phone__icontains=q))
    return private(Response({'orders':[staff_order(o) for o in qs[:200]]}))


@api_view(['GET'])
@permission_classes([IsFulfillmentOrOwnerStaff])
def staff_order_detail_view(request,order_id):
    return private(Response(staff_order(get_object_or_404(Order,pk=order_id))))


@api_view(['GET'])
@permission_classes([IsCommerceStaff])
def staff_inventory_view(request):
    qs=InventoryBatch.objects.select_related('variant__product').prefetch_related('quarantine_returns').order_by('variant__sku','lot_code')
    return private(Response({'batches':StaffBatchSerializer(qs,many=True,context={'include_cost':has_role(request.user,('Owner','Catalog_Inventory_Manager'))}).data,'variants':list(ProductVariant.objects.order_by('sku').values('sku','pack_size_label_ar','product__name_ar'))}))


@api_view(['POST'])
@permission_classes([IsCatalogOrOwnerStaff])
@command_errors
def staff_receive_batch_view(request):
    data=validated(ReceiveBatchSerializer,request)
    if not data.get('receipt_ref'): data.pop('receipt_ref',None)
    batch=InventoryOrderService.receive_batch(actor=request.user.username,**data)
    return private(Response(StaffBatchSerializer(batch,context={'include_cost':True}).data,status=201))


@api_view(['POST'])
@permission_classes([IsCatalogOrOwnerStaff])
@command_errors
def staff_block_batch_view(request,batch_id):
    data=validated(ReasonSerializer,request)
    get_object_or_404(InventoryBatch,pk=batch_id)
    affected=InventoryOrderService.block_batch_and_trace_orders(batch_id=batch_id,actor=request.user.username,**data)
    return private(Response({'affected_orders':affected}))


@api_view(['POST'])
@permission_classes([IsFulfillmentOrOwnerStaff])
@command_errors
def staff_order_action_view(request,order_id,action):
    get_object_or_404(Order,pk=order_id)
    common={'order_id':order_id,'actor':request.user.username}
    if action=='confirm': result=InventoryOrderService.confirm_order(**common)
    elif action=='cancel': result=InventoryOrderService.cancel_order_before_dispatch(**common,**validated(ReasonSerializer,request))
    elif action=='dispatch': result=InventoryOrderService.dispatch_order(**common,**validated(DispatchSerializer,request))
    elif action=='delivered':
        InventoryOrderService.record_delivery_and_customer_collection(**common,**validated(CollectionSerializer,request))
        result=Order.objects.get(pk=order_id)
    elif action=='failed': result=InventoryOrderService.record_failed_delivery(**common,**validated(ReasonSerializer,request))
    else: return Response({'detail':'العملية غير موجودة.'},status=404)
    return private(Response(staff_order(result)))


# Preserve the original function entry point for callers.
def staff_confirm_order_view(request,order_id):
    return staff_order_action_view(request,order_id,'confirm')


@api_view(['GET','POST'])
@permission_classes([IsFulfillmentOrOwnerStaff])
@command_errors
def staff_remittances_view(request):
    if request.method=='GET':
        return private(Response({'remittances':StaffRemittanceSerializer(CourierRemittance.objects.order_by('-received_at')[:200],many=True).data,'summary':InventoryOrderService.get_cash_reconciliation_summary()}))
    result=InventoryOrderService.record_courier_remittance(recorded_by=request.user.username,**validated(RemittanceSerializer,request))
    return private(Response(StaffRemittanceSerializer(result).data,status=201))


@api_view(['GET','POST'])
@permission_classes([IsFulfillmentOrOwnerStaff])
@command_errors
def staff_returns_view(request):
    if request.method=='GET':
        return private(Response({'returns':StaffReturnSerializer(ReturnRecord.objects.select_related('order_line__order').order_by('-created_at')[:200],many=True).data}))
    data=validated(ReturnReceiptSerializer,request)
    if not data.get('return_ref'): data.pop('return_ref',None)
    result=InventoryOrderService.process_physical_return(approve_restock=False,inspected_by=request.user.username,**data)
    return private(Response(StaffReturnSerializer(result).data,status=201))


@api_view(['POST'])
@permission_classes([IsFulfillmentOrOwnerStaff])
@command_errors
def staff_inspect_return_view(request,return_id):
    get_object_or_404(ReturnRecord,pk=return_id)
    result=InventoryOrderService.inspect_return(return_id=return_id,inspected_by=request.user.username,**validated(ReturnInspectionSerializer,request))
    return private(Response(StaffReturnSerializer(result).data))


@api_view(['GET'])
@permission_classes([IsCommerceStaff])
def staff_zones_view(request):
    data=PublicDeliveryZoneSerializer(DeliveryZone.objects.order_by('code'),many=True).data
    for row,zone in zip(data,DeliveryZone.objects.order_by('code')): row['is_active']=zone.is_active
    return private(Response({'zones':data}))


@api_view(['PATCH'])
@permission_classes([IsOwnerStaff])
@command_errors
def staff_zone_update_view(request,zone_code):
    with transaction.atomic():
        zone=get_object_or_404(DeliveryZone.objects.select_for_update(),code=zone_code)
        serializer=ZoneUpdateSerializer(zone,data=request.data,partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        AuditLog.objects.create(actor=request.user.username,action='CONFIGURE_DELIVERY_ZONE',entity_type='DeliveryZone',entity_id=zone.code,payload_json=serializer.validated_data)
    return private(Response({**PublicDeliveryZoneSerializer(zone).data,'is_active':zone.is_active}))


@ensure_csrf_cookie
@never_cache
def staff_home_view(request):
    return render(request,'commerce/staff.html',{'demo_mode':getattr(settings,'DEMO_MODE',True)})


@never_cache
def staff_picklist_view(request,order_id):
    if not has_role(request.user,('Owner','Fulfillment_Support_Staff')):
        return JsonResponse({'detail':'سجّل الدخول بحساب موظف التجهيز.'},status=403)
    order=get_object_or_404(Order,pk=order_id)
    return render(request,'commerce/picklist.html',{'order':order,'details':staff_order(order),'printed_at':timezone.now()})


@api_view(['POST'])
@permission_classes([IsFulfillmentOrOwnerStaff])
@command_errors
def staff_refund_payment_view(request,return_id):
    get_object_or_404(ReturnRecord,pk=return_id)
    result=InventoryOrderService.record_refund_payment(return_id=return_id,actor=request.user.username,**validated(RefundPaymentCommandSerializer,request))
    return private(Response(StaffRefundPaymentSerializer(result).data,status=201))


@api_view(['POST'])
@permission_classes([IsCatalogOrOwnerStaff])
@command_errors
def staff_review_batch_view(request,batch_id):
    get_object_or_404(InventoryBatch,pk=batch_id)
    result=InventoryOrderService.review_batch(batch_id=batch_id,actor=request.user.username,**validated(ReviewBatchSerializer,request))
    return private(Response(StaffBatchSerializer(result,context={'include_cost':True}).data))
