from django.core.management.base import BaseCommand
from apps.commerce.roles import configure_roles


class Command(BaseCommand):
    help = "Create/update the explicit least-privilege staff role permissions."

    def handle(self, *args, **options):
        for name, group in configure_roles().items():
            self.stdout.write(f"{name}: {group.permissions.count()} permissions")
