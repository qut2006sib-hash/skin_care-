from rest_framework.permissions import BasePermission


class IsCatalogOrOwnerStaff(BasePermission):
    """Server-enforced permission for catalog and batch receiving actions."""
    def has_permission(self, request, view) -> bool:
        user = request.user
        if not (user and user.is_authenticated and user.is_staff):
            return False
        if user.is_superuser:
            return True
        return user.groups.filter(name__in=["Owner", "Catalog_Inventory_Manager"]).exists()


class IsFulfillmentOrOwnerStaff(BasePermission):
    """Server-enforced permission for order confirmation, dispatch, and cash reconciliation."""
    def has_permission(self, request, view) -> bool:
        user = request.user
        if not (user and user.is_authenticated and user.is_staff):
            return False
        if user.is_superuser:
            return True
        return user.groups.filter(name__in=["Owner", "Fulfillment_Support_Staff"]).exists()
