"""Inspect tenant records; mutations go through scoped service-backed actions."""
from django.contrib import admin
from apps.commerce.admin import OperationalAdmin
from .models import AccountProfile, Store, StoreProduct, StoreBatch, StoreReceipt, StoreSale, StoreSaleLine, StoreSaleAllocation

for model in (AccountProfile, Store, StoreProduct, StoreBatch, StoreReceipt, StoreSale, StoreSaleLine, StoreSaleAllocation):
    admin.site.register(model, OperationalAdmin)
