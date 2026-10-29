from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.shortcuts import get_object_or_404

from .models import Product, DeliveryZone, Order
from .serializers import (
    PublicProductSerializer,
    PublicDeliveryZoneSerializer,
    ProtectedOrderTrackingSerializer,
)
from .services import InventoryOrderService
from .permissions import IsCatalogOrOwnerStaff, IsFulfillmentOrOwnerStaff
from .exceptions import CommerceDomainError


@api_view(["GET"])
def catalog_products_view(request):
    qs = Product.objects.filter(is_published=True).select_related("brand", "category").prefetch_related("variants")
    category = request.query_params.get("category")
    brand = request.query_params.get("brand")
    concern = request.query_params.get("concern")
    q = request.query_params.get("q")
    if category:
        qs = qs.filter(category__slug=category)
    if brand:
        qs = qs.filter(brand__slug=brand)
    if q:
        qs = qs.filter(name_ar__icontains=q) | qs.filter(name_en__icontains=q)
    data = PublicProductSerializer(qs, many=True).data
    if concern:
        data = [p for p in data if concern in p.get("concern_tags", [])]
    return Response({"currency": "SYP", "city": "Homs", "products": data})


@api_view(["GET"])
def active_delivery_zones_view(request):
    zones = DeliveryZone.objects.filter(city="Homs", is_active=True, fee_syp__isnull=False)
    return Response(PublicDeliveryZoneSerializer(zones, many=True).data)


@api_view(["POST"])
def guest_checkout_submit_view(request):
    idempotency_key = request.headers.get("Idempotency-Key") or request.data.get("idempotency_key")
    if not idempotency_key:
        return Response({"detail": "Idempotency-Key is required."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        order = InventoryOrderService.submit_guest_cod_order(
            idempotency_key=idempotency_key,
            customer_name=request.data["customer_name"],
            customer_phone=request.data["customer_phone"],
            delivery_zone_code=request.data["delivery_zone_code"],
            address_landmark_ar=request.data["address_landmark_ar"],
            items=request.data["items"],
        )
    except CommerceDomainError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

    return Response(
        {
            "order_number": order.order_number,
            "tracking_token": order.tracking_token,
            "status": order.status,
            "total_syp": order.total_syp,
            "currency": order.currency,
            "hold_expires_at": order.hold_expires_at,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
def protected_order_tracking_view(request, tracking_token: str):
    order = get_object_or_404(Order, tracking_token=tracking_token)
    response = Response(ProtectedOrderTrackingSerializer(order).data)
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@api_view(["POST"])
@permission_classes([IsCatalogOrOwnerStaff])
def staff_receive_batch_view(request):
    batch = InventoryOrderService.receive_batch(
        sku=request.data["sku"],
        lot_code=request.data["lot_code"],
        supplier_invoice_ref=request.data["supplier_invoice_ref"],
        qty_received=int(request.data["qty_received"]),
        unit_cost_syp=int(request.data["unit_cost_syp"]),
        expiry_date=request.data.get("expiry_date"),
        validity_evidence_ref=request.data.get("validity_evidence_ref", ""),
        actor=request.user.username,
    )
    return Response({"lot_code": batch.lot_code, "condition": batch.condition, "qty_on_hand": batch.qty_on_hand})


@api_view(["POST"])
@permission_classes([IsFulfillmentOrOwnerStaff])
def staff_confirm_order_view(request, order_id: int):
    try:
        order = InventoryOrderService.confirm_order(order_id=order_id, actor=request.user.username)
        return Response({"order_number": order.order_number, "status": order.status})
    except CommerceDomainError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
