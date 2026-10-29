"""Catalog editing and read-only operational records; transitions live in /staff/."""
from django.contrib import admin
from django import forms
from django.conf import settings
from .models import Brand, Category, Product, ProductVariant, InventoryBatch, DeliveryZone, Order, OrderLine, OrderBatchAllocation, CustomerCashCollection, CourierRemittance, ReturnRecord, AuditLog, StockReceipt, StockMovement, RefundPayment
from .permissions import has_role

admin.site.site_header='إدارة متجر العناية'
admin.site.site_title='إدارة المتجر'
admin.site.index_title='الكتالوج وسجلات العمليات — التجهيز والتحصيل في لوحة الموظفين /staff/'

class CatalogAdmin(admin.ModelAdmin):
    def has_view_permission(self,request,obj=None): return has_role(request.user) and super().has_view_permission(request,obj)
    def has_add_permission(self,request): return has_role(request.user,('Owner','Catalog_Inventory_Manager')) and super().has_add_permission(request)
    def has_change_permission(self,request,obj=None): return has_role(request.user,('Owner','Catalog_Inventory_Manager')) and super().has_change_permission(request,obj)
    def has_delete_permission(self,request,obj=None): return False

@admin.register(DeliveryZone)
class DeliveryZoneAdmin(CatalogAdmin):
    list_display=('district_name_ar','code','city','fee_syp','is_active','estimated_hours')
    search_fields=('district_name_ar','code')
    def has_add_permission(self,request): return has_role(request.user,('Owner',)) and super().has_add_permission(request)
    def has_change_permission(self,request,obj=None): return has_role(request.user,('Owner',)) and super().has_change_permission(request,obj)

@admin.register(ProductVariant)
class ProductVariantAdmin(CatalogAdmin):
    list_display=('sku','product','pack_size_label_ar','price_syp','currency','is_active')
    search_fields=('sku','barcode','product__name_ar')

class ProductForm(forms.ModelForm):
    class Meta:
        model=Product
        fields='__all__'
    def clean(self):
        data=super().clean()
        if data.get('is_published'):
            for name in ('ingredients_text','directions_ar','warnings_ar','claim_evidence_source','claim_checked_date','claim_reviewer'):
                if not data.get(name): self.add_error(name,'هذا البيان مطلوب قبل نشر المنتج.')
            if not getattr(settings,'DEMO_MODE',True) and 'DEMO' in data.get('claim_evidence_source','').upper():
                self.add_error('claim_evidence_source','استبدل الدليل التجريبي بمرجع موثق قبل النشر الفعلي.')
        return data

@admin.register(Product)
class ProductAdmin(CatalogAdmin):
    form=ProductForm
    list_display=('name_ar','brand','is_published','claim_checked_date')
    list_filter=('brand','is_published')
    search_fields=('name_ar','name_en')

admin.site.register(Brand,CatalogAdmin)
admin.site.register(Category,CatalogAdmin)

class OperationalAdmin(admin.ModelAdmin):
    def has_view_permission(self,request,obj=None): return has_role(request.user) and super().has_view_permission(request,obj)
    actions=None
    def has_add_permission(self,request): return False
    def has_change_permission(self,request,obj=None): return False
    def has_delete_permission(self,request,obj=None): return False
    def get_readonly_fields(self,request,obj=None): return [f.name for f in self.model._meta.fields]

@admin.register(InventoryBatch)
class InventoryBatchAdmin(OperationalAdmin):
    list_display=('variant','lot_code','condition','expiry_date','qty_on_hand','qty_reserved')
    list_filter=('condition','variant__product__brand')
    search_fields=('lot_code','variant__sku')

class OrderLineInline(admin.TabularInline):
    model=OrderLine
    extra=0
    can_delete=False
    def has_add_permission(self,request,obj=None): return False
    def has_change_permission(self,request,obj=None): return False
    def get_readonly_fields(self,request,obj=None): return [f.name for f in self.model._meta.fields]

@admin.register(Order)
class OrderAdmin(OperationalAdmin):
    list_display=('order_number','status','customer_name','district_snapshot_ar','total_syp','created_at')
    list_filter=('status','delivery_zone')
    search_fields=('order_number','customer_name','customer_phone')
    inlines=[OrderLineInline]

for model in (OrderBatchAllocation,CustomerCashCollection,CourierRemittance,ReturnRecord,AuditLog,StockReceipt,StockMovement,RefundPayment):
    admin.site.register(model,OperationalAdmin)
