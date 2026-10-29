"""Import only product names, source SKU and reference USD prices."""
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from apps.commerce.models import SupplierCatalogItem

SNAPSHOT = Path(__file__).resolve().parents[2] / 'data' / 'pharmacy_catalog.json'
BRANDS = {
    'MEDEE': ('medee', 'ميدي'),
    'JUVE LAB': ('juve-lab', 'جوفيلاب'),
    'FEME HAIR': ('feme-hair', 'فيمي هير'),
}


def labels(sku, name, brand):
    """Neutral labels; no ingredients, pack size or efficacy inferred."""
    display = {
        'MEDEE-VIT-C': 'سيروم ميدي Vit C',
        'MEDEE-EXTRA-C': 'سيروم ميدي EXTRA C',
        'MEDEE-HYDRA': 'ميدي HYDRA',
        'MEDEE-HA': 'ميدي HA',
        'MEDEE-RETINOL': 'ميدي RETINOL',
        'MEDEE-ROLLER-025': 'أداة ميدي DERMA ROLLER 0.25',
        'MEDEE-ROLLER-05': 'أداة ميدي DERMA ROLLER 0.5',
        'JUVE-AQUAFLUID-SPF50': 'جوفيلاب AQUAFLUID +SPF50',
        'JUVE-SPF50-WHITENIZER': 'جوفيلاب SPF50 + WHITENIZER',
        'JUVE-TKN-CREAM': 'كريم جوفيلاب TKN',
        'JUVE-CLEANSER-OILY': 'غسول جوفيلاب OILY COMBINED SKIN',
        'JUVE-CLEANSER-DRY': 'غسول جوفيلاب DRY COMBINED SKIN',
        'JUVE-CLEANSER-SOFT': 'غسول جوفيلاب SOFT COMBINED SKIN',
        'JUVE-DEODORANT': 'جوفيلاب DEODARANT',
        'JUVE-DEODORANT-ANTI': 'جوفيلاب DEODARANT ANTIPRESPIRANT',
        'FEME-CAPSULES-60': 'فيمي هير CAPSULES 60',
        'FEME-SHAMPOO': 'شامبو فيمي هير',
        'FEME-ROSEMARY-OIL': 'زيت فيمي هير ROSEMARY',
    }.get(sku, name)
    upper = name.upper()
    if 'ROLLER' in upper:
        category = ('accessories', 'أدوات العناية')
    elif 'CAPSULES' in upper:
        category = ('capsules', 'كبسولات')
    elif brand == 'FEME HAIR':
        category = ('hair-care', 'العناية بالشعر')
    elif 'CLEANSER' in upper:
        category = ('cleansers', 'غسولات')
    elif 'DEODARANT' in upper or 'DEODORANT' in upper:
        category = ('body-care', 'العناية بالجسم')
    elif 'SERUM' in upper:
        category = ('serums', 'سيرومات')
    elif 'CREAM' in upper:
        category = ('moisturizers', 'كريمات')
    else:
        category = ('skin-care', 'العناية بالبشرة')
    return display, category


class Command(BaseCommand):
    help = 'Import the bundled pharmacy-distribution product snapshot as non-orderable source listings. No prices converted and no stock created.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            snapshot = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
            source = snapshot['source']
            if set(source) != {'repository', 'commit', 'path'} or source['repository'] != 'qut2006sib-hash/pharmacy-distribution' or not re.fullmatch(r'[0-9a-f]{40}', source['commit']):
                raise ValueError('invalid source provenance')
            if source['path'] != 'pharmacy_distribution/field_data.py':
                raise ValueError('unexpected source path')
            rows = snapshot['products']
            if not isinstance(rows, list) or len(rows) != 18:
                raise ValueError('the bundled source snapshot must contain exactly 18 products')
            seen = set()
            created = updated = 0
            for i, row in enumerate(rows, 1):
                if set(row) != {'sku', 'name', 'brand', 'price_usd'}:
                    raise ValueError(f'row {i}: only product identity and reference price are allowed')
                if not isinstance(row['sku'], str) or not re.fullmatch(r'[A-Z0-9][A-Z0-9-]{0,63}', row['sku']) or row['sku'] in seen:
                    raise ValueError(f'row {i}: invalid or duplicate SKU')
                if row['brand'] not in BRANDS or not isinstance(row['name'], str) or not 1 <= len(row['name']) <= 240:
                    raise ValueError(f'row {i}: invalid product identity')
                if not isinstance(row['price_usd'], str) or not re.fullmatch(r'[0-9]+\.[0-9]{2}', row['price_usd']):
                    raise ValueError(f'row {i}: price must retain two USD decimals')
                amount = Decimal(row['price_usd'])
                if not amount.is_finite() or amount < 0 or amount >= Decimal('10000000000'):
                    raise ValueError(f'row {i}: reference price out of range')
                seen.add(row['sku'])
                brand_slug, brand_ar = BRANDS[row['brand']]
                name_ar, (category_slug, category_ar) = labels(row['sku'], row['name'], row['brand'])
                _, was_created = SupplierCatalogItem.objects.update_or_create(sku=row['sku'], defaults={
                    'source_name': row['name'], 'name_ar': name_ar,
                    'brand_name': row['brand'], 'brand_name_ar': brand_ar, 'brand_slug': brand_slug,
                    'category_slug': category_slug, 'category_name_ar': category_ar,
                    'reference_price_usd': amount, 'source_repository': source['repository'],
                    'source_commit': source['commit'], 'source_path': source['path'],
                })
                created += int(was_created)
                updated += int(not was_created)
            if options['dry_run']:
                transaction.set_rollback(True)
            self.stdout.write(self.style.SUCCESS(f'{"Validated (no changes)" if options["dry_run"] else "Imported"}: {created} new, {updated} existing supplier listings. Operational prices, stock and orders unchanged.'))
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, InvalidOperation) as exc:
            raise CommandError(f'Cannot import pharmacy catalog: {exc}') from exc
