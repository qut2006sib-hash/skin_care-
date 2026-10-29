from django.contrib import admin
from django.urls import path
from apps.commerce import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/catalog/products/", views.catalog_products_view, name="catalog-products"),
    path("api/v1/delivery-zones/", views.active_delivery_zones_view, name="delivery-zones"),
    path("api/v1/checkout/submit/", views.guest_checkout_submit_view, name="checkout-submit"),
    path("api/v1/orders/track/<str:tracking_token>/", views.protected_order_tracking_view, name="order-track"),
    path("api/v1/staff/batches/receive/", views.staff_receive_batch_view, name="staff-receive-batch"),
    path("api/v1/staff/orders/<int:order_id>/confirm/", views.staff_confirm_order_view, name="staff-confirm-order"),
]
