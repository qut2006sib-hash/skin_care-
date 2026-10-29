from django.contrib import admin
from django.urls import path
from apps.commerce import views

urlpatterns = [
    path('admin/',admin.site.urls),
    path('staff/',views.staff_home_view,name='staff-home'),
    path('staff/orders/<int:order_id>/picklist/',views.staff_picklist_view,name='staff-picklist'),
    path('api/v1/health/',views.health_view),
    path('api/v1/config/',views.config_view),
    path('api/v1/auth/session/',views.session_view),
    path('api/v1/auth/login/',views.login_view),
    path('api/v1/auth/logout/',views.logout_view),
    path('api/v1/catalog/products/',views.catalog_products_view,name='catalog-products'),
    path('api/v1/delivery-zones/',views.active_delivery_zones_view,name='delivery-zones'),
    path('api/v1/checkout/submit/',views.guest_checkout_submit_view,name='checkout-submit'),
    path('api/v1/orders/track/<str:tracking_token>/',views.protected_order_tracking_view,name='order-track'),
    path('api/v1/staff/orders/',views.staff_orders_view),
    path('api/v1/staff/orders/<int:order_id>/',views.staff_order_detail_view),
    path('api/v1/staff/orders/<int:order_id>/<str:action>/',views.staff_order_action_view),
    path('api/v1/staff/inventory/',views.staff_inventory_view),
    path('api/v1/staff/batches/receive/',views.staff_receive_batch_view,name='staff-receive-batch'),
    path('api/v1/staff/batches/<int:batch_id>/block/',views.staff_block_batch_view),
    path('api/v1/staff/batches/<int:batch_id>/review/',views.staff_review_batch_view),
    path('api/v1/staff/remittances/',views.staff_remittances_view),
    path('api/v1/staff/returns/',views.staff_returns_view),
    path('api/v1/staff/returns/<int:return_id>/inspect/',views.staff_inspect_return_view),
    path('api/v1/staff/returns/<int:return_id>/refund-payments/',views.staff_refund_payment_view),
    path('api/v1/staff/zones/',views.staff_zones_view),
    path('api/v1/staff/zones/<slug:zone_code>/',views.staff_zone_update_view),
]

# Local pilot media only; public deployment must serve media through its web server.
from django.conf import settings
from django.conf.urls.static import static
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
