from rest_framework.permissions import BasePermission

ROLES = ('Owner', 'Catalog_Inventory_Manager', 'Fulfillment_Support_Staff')

def has_role(user, roles=ROLES):
    return bool(user and user.is_authenticated and user.is_active and user.is_staff and (user.is_superuser or user.groups.filter(name__in=roles).exists()))

class IsCommerceStaff(BasePermission):
    message = 'هذه العملية متاحة لموظفي المتجر المصرّح لهم فقط.'
    def has_permission(self, request, view):
        return has_role(request.user)

class IsOwnerStaff(IsCommerceStaff):
    def has_permission(self, request, view):
        return has_role(request.user, ('Owner',))

class IsCatalogOrOwnerStaff(IsCommerceStaff):
    def has_permission(self, request, view):
        return has_role(request.user, ('Owner', 'Catalog_Inventory_Manager'))

class IsFulfillmentOrOwnerStaff(IsCommerceStaff):
    def has_permission(self, request, view):
        return has_role(request.user, ('Owner', 'Fulfillment_Support_Staff'))
