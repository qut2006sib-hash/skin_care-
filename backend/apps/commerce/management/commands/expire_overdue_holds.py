"""Bounded, retry-safe pending reservation expiry worker."""
import time
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from apps.commerce.models import Order, OrderStatus
from apps.commerce.services import InventoryOrderService
from apps.commerce.exceptions import CommerceDomainError


class Command(BaseCommand):
    help = "Release overdue unconfirmed holds once, or poll using --loop. Confirmed orders are excluded."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Poll until stopped; each pass is bounded by --limit.")
        parser.add_argument("--interval", type=int, default=60, help="Seconds between passes (1–3600).")
        parser.add_argument("--limit", type=int, default=500, help="Maximum pending orders examined per pass (1–10000).")

    def handle(self, *args, **options):
        if not 1 <= options["interval"] <= 3600 or not 1 <= options["limit"] <= 10000:
            raise CommandError("interval must be 1–3600 and limit must be 1–10000")
        try:
            while True:
                now = timezone.now()
                ids = list(Order.objects.filter(status=OrderStatus.PENDING_CONFIRMATION, hold_expires_at__lte=now).order_by("hold_expires_at", "pk").values_list("pk", flat=True)[:options["limit"]])
                expired = skipped = failed = 0
                for order_id in ids:
                    try:
                        InventoryOrderService.expire_pending_hold(order_id=order_id, now=now)
                        expired += 1
                    except (CommerceDomainError, Order.DoesNotExist):
                        # Confirmation/cancellation can win between this read
                        # and the service's row-locked transition.
                        skipped += 1
                    except Exception as exc:
                        failed += 1
                        self.stderr.write(f"Hold expiry failed for order {order_id}: {type(exc).__name__}")
                self.stdout.write(f"expired={expired} raced_or_skipped={skipped} failed={failed}")
                if not options["loop"]:
                    if failed:
                        raise CommandError(f"{failed} holds could not be expired; inspect logs and retry.")
                    return
                time.sleep(options["interval"])
        except KeyboardInterrupt:
            self.stdout.write("Hold worker stopped.")
