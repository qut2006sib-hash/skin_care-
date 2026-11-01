from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import BasePermission
from apps.commerce.permissions import has_role
from .models import AccountProfile, Store


def account_type(user):
    if not user or not user.is_authenticated or not user.is_active:
        return None
    if has_role(user, ('Owner',)):
        return 'manager'
    if has_role(user):
        return 'staff'
    return AccountProfile.objects.filter(user=user).values_list('account_type', flat=True).first()


def session_details(user):
    kind = account_type(user)
    data = {'account_type': kind, 'dashboard': {'manager': '/manager', 'staff': '/staff/', 'customer': '/account', 'store': '/store'}.get(kind, '/sign-in')}
    if kind == 'store':
        data['store'] = Store.objects.filter(owner=user).values('id', 'name', 'status').first()
    return data


class IsCustomer(BasePermission):
    def has_permission(self, request, view):
        return account_type(request.user) == 'customer'


class IsStore(BasePermission):
    def has_permission(self, request, view):
        return account_type(request.user) == 'store'


class IsApprovedStore(IsStore):
    message = 'حساب الصيدلية بانتظار التفعيل أو موقوف من المدير.'

    def has_permission(self, request, view):
        return super().has_permission(request, view) and Store.objects.filter(owner=request.user, status='APPROVED').exists()


class CustomerCheckoutAuthentication(SessionAuthentication):
    """Link only CSRF-checked customer sessions; preserve legacy guest/staff checkout."""
    def authenticate(self, request):
        if account_type(getattr(request._request, 'user', None)) != 'customer':
            return None
        return super().authenticate(request)
