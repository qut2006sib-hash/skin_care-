import json
from django.contrib.auth import get_user_model, login
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from apps.commerce.models import Order
from apps.commerce.permissions import IsOwnerStaff
from apps.commerce.serializers import ProtectedOrderTrackingSerializer
from apps.commerce.views import command_errors, private, user_data
from apps.commerce.services import _audit
from .access import IsCustomer, IsStore, IsApprovedStore
from .models import AccountProfile, Store, StoreProduct, StoreBatch, StoreSale
from .services import save_product, receive_stock, make_sale, return_sale


class RegistrationSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, validators=[UnicodeUsernameValidator()])
    password = serializers.CharField(max_length=128, trim_whitespace=False, write_only=True)
    email = serializers.EmailField(required=False, allow_blank=True, default='')
    account_type = serializers.ChoiceField(choices=['customer', 'store'])
    store_name = serializers.CharField(max_length=160, required=False)

    def validate(self, data):
        if set(self.initial_data) - set(self.fields):
            raise serializers.ValidationError('حقول الحساب غير صالحة.')
        if data['account_type'] == 'store' and not data.get('store_name'):
            raise serializers.ValidationError('اسم الصيدلية مطلوب.')
        user = get_user_model()(username=data['username'], email=data['email'])
        try:
            validate_password(data['password'], user=user)
        except ValidationError as exc:
            raise serializers.ValidationError({'password': exc.messages})
        return data


@require_POST
@csrf_protect
@never_cache
def register_view(request):
    if request.user.is_authenticated:
        return JsonResponse({'detail': 'سجّل الخروج قبل إنشاء حساب آخر.'}, status=409)
    try:
        raw = json.loads(request.body)
    except (ValueError, UnicodeError):
        return JsonResponse({'detail': 'بيانات غير صالحة.'}, status=400)
    serializer = RegistrationSerializer(data=raw)
    if not serializer.is_valid():
        return JsonResponse({'detail': 'راجع بيانات التسجيل.', 'errors': serializer.errors}, status=400)
    data = serializer.validated_data
    try:
        with transaction.atomic():
            user = get_user_model().objects.create_user(username=data['username'], password=data['password'], email=data['email'])
            AccountProfile.objects.create(user=user, account_type=data['account_type'])
            if data['account_type'] == 'store':
                Store.objects.create(owner=user, name=data['store_name'])
    except IntegrityError:
        return JsonResponse({'detail': 'اسم المستخدم غير متاح؛ اختر اسمًا آخر.'}, status=400)
    login(request, user)
    return private(JsonResponse(user_data(user), status=201))


def store_data(store):
    return {'id': store.pk, 'name': store.name, 'status': store.status, 'username': store.owner.username, 'created_at': store.created_at}


@api_view(['GET'])
@permission_classes([IsOwnerStaff])
def manager_stores_view(request):
    stores = Store.objects.select_related('owner').order_by('-id')
    return private(Response({'stores': [store_data(s) for s in stores], 'summary': {'store_count': stores.count(), 'pending_stores': stores.filter(status='PENDING').count(), 'customer_count': AccountProfile.objects.filter(account_type='customer').count()}}))


@api_view(['POST'])
@permission_classes([IsOwnerStaff])
def manager_store_status_view(request, store_id):
    status = request.data.get('status')
    if status not in ('PENDING', 'APPROVED', 'SUSPENDED'):
        return Response({'detail': 'حالة الصيدلية غير صالحة.'}, status=400)
    with transaction.atomic():
        from django.shortcuts import get_object_or_404
        store = get_object_or_404(Store.objects.select_for_update(), pk=store_id)
        store.status, store.reviewed_by, store.reviewed_at = status, request.user, timezone.now()
        store.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
        _audit(request.user.username[:120], 'STORE_STATUS', store, {'status': status})
    return private(Response({'ok': True, 'store': store_data(store)}))


@api_view(['GET'])
@permission_classes([IsCustomer])
def customer_orders_view(request):
    orders = Order.objects.filter(customer_user=request.user).prefetch_related('lines').order_by('-id')
    class CustomerOrderSerializer(ProtectedOrderTrackingSerializer):
        class Meta(ProtectedOrderTrackingSerializer.Meta):
            fields = ProtectedOrderTrackingSerializer.Meta.fields + ['tracking_token']
    return private(Response({'orders': CustomerOrderSerializer(orders, many=True).data}))


def sale_data(sale):
    return {'id': sale.pk, 'receipt_number': sale.receipt_number, 'reference': sale.reference, 'total_syp': sale.total_syp, 'cost_syp': sale.cost_syp, 'created_at': sale.created_at, 'is_returned': sale.is_returned, 'lines': list(sale.lines.values('sku', 'name', 'quantity', 'unit_price_syp', 'cost_syp'))}


@api_view(['GET'])
@permission_classes([IsStore])
def store_dashboard_view(request):
    store = Store.objects.select_related('owner').get(owner=request.user)
    base = {'store': store_data(store), 'products': [], 'batches': [], 'sales': [], 'receipts': [], 'summary': {'sales_today_syp': 0, 'cost_today_syp': 0, 'gross_profit_today_syp': 0, 'low_stock_count': 0, 'refunds_today_syp': 0}}
    if store.status != 'APPROVED':
        return private(Response(base))
    today = timezone.localdate()
    products = StoreProduct.objects.filter(store=store).annotate(available=Sum('batches__quantity', filter=Q(batches__condition='SALEABLE', batches__expiry_date__gt=today))).order_by('sku')
    base['products'] = [{'id': p.pk, 'sku': p.sku, 'name': p.name, 'selling_price_syp': p.selling_price_syp, 'low_stock_threshold': p.low_stock_threshold, 'available_quantity': p.available or 0} for p in products]
    base['batches'] = list(StoreBatch.objects.filter(product__store=store).order_by('-id').values('id', 'product__sku', 'lot_code', 'expiry_date', 'quantity', 'condition'))
    for batch in base['batches']:
        batch['sku'] = batch.pop('product__sku')
    base['sales'] = [sale_data(s) for s in store.sales.prefetch_related('lines').order_by('-id')[:100]]
    base['receipts'] = list(store.receipts.order_by('-id').values('id', 'reference', 'total_syp', 'created_at', 'items')[:100])
    totals = store.sales.filter(created_at__date=today).aggregate(revenue=Sum('total_syp'), cost=Sum('cost_syp'))
    returns = store.sales.filter(is_returned=True, returned_at__date=today).aggregate(revenue=Sum('total_syp'), cost=Sum('cost_syp'))
    net = (totals['revenue'] or 0)-(returns['revenue'] or 0)
    cost = (totals['cost'] or 0)-(returns['cost'] or 0)
    base['summary'] = {'sales_today_syp': net, 'cost_today_syp': cost, 'gross_profit_today_syp': net-cost, 'refunds_today_syp': returns['revenue'] or 0, 'low_stock_count': sum(p['available_quantity'] <= p['low_stock_threshold'] for p in base['products'])}
    return private(Response(base))


@api_view(['POST'])
@permission_classes([IsApprovedStore])
@command_errors
def store_products_view(request):
    product = save_product(store_id=Store.objects.get(owner=request.user).pk, sku=request.data.get('sku'), name=request.data.get('name'), selling_price_syp=request.data.get('selling_price_syp'), low_stock_threshold=request.data.get('low_stock_threshold', 5), actor=request.user.username)
    return private(Response({'ok': True, 'id': product.pk}))


@api_view(['POST'])
@permission_classes([IsApprovedStore])
@command_errors
def store_receipts_view(request):
    receipt = receive_stock(store_id=Store.objects.get(owner=request.user).pk, reference=request.data.get('reference'), items=request.data.get('items'), actor=request.user.username)
    return private(Response({'ok': True, 'id': receipt.pk, 'total_syp': receipt.total_syp}))


@api_view(['POST'])
@permission_classes([IsApprovedStore])
@command_errors
def store_sales_view(request):
    sale = make_sale(store_id=Store.objects.get(owner=request.user).pk, reference=request.data.get('reference'), items=request.data.get('items'), actor=request.user.username, expected_total_syp=request.data.get('expected_total_syp'))
    return private(Response({'ok': True, 'sale': sale_data(sale)}))


@api_view(['POST'])
@permission_classes([IsApprovedStore])
@command_errors
def store_return_view(request, sale_id):
    sale = return_sale(store_id=Store.objects.get(owner=request.user).pk, sale_id=sale_id, reason=request.data.get('reason'), actor=request.user.username)
    return private(Response({'ok': True, 'sale': sale_data(sale)}))
