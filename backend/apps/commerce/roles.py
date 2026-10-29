"""Explicit model permissions for staff roles.

Operational writes are service-backed actions, never generic model edits.
Group membership enables scoped API actions in commerce.permissions.
"""
from django.contrib.auth.models import Group, Permission
from django.db import transaction

ROLE_NAMES = ("Owner", "Catalog_Inventory_Manager", "Fulfillment_Support_Staff")
CATALOG_MODELS = {"brand", "category", "product", "productvariant", "suppliercatalogitem"}
OPERATIONAL_MODELS = {"inventorybatch", "order", "orderline", "orderbatchallocation", "customercashcollection", "courierremittance", "returnrecord", "auditlog", "stockmovement"}


@transaction.atomic
def configure_roles():
    all_perms = list(Permission.objects.filter(content_type__app_label="commerce"))
    groups = {}
    for name in ROLE_NAMES:
        group, _ = Group.objects.get_or_create(name=name)
        chosen = []
        for permission in all_perms:
            model = permission.content_type.model
            action = permission.codename.split("_", 1)[0]
            if name == "Owner":
                allowed = action == "view" or (model in CATALOG_MODELS | {"deliveryzone"} and action in {"add", "change"})
            elif name == "Catalog_Inventory_Manager":
                allowed = (model in CATALOG_MODELS and action in {"view", "add", "change"}) or (model in OPERATIONAL_MODELS and action == "view")
            else:
                allowed = model in OPERATIONAL_MODELS | CATALOG_MODELS | {"deliveryzone"} and action == "view"
            if allowed:
                chosen.append(permission)
        group.permissions.set(chosen)
        groups[name] = group
    return groups
