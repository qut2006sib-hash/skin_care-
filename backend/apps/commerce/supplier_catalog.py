"""Public source-listing representation, separate from approved commerce data."""
from .models import ProductVariant, SupplierCatalogItem


def source_listing(item):
    return {
        'slug': 'source-' + item.sku.lower(),
        'name_ar': item.name_ar, 'name_en': item.source_name,
        'brand_name_en': item.brand_name, 'brand_name_ar': item.brand_name_ar,
        'brand_slug': item.brand_slug,
        'category_slug': item.category_slug, 'category_name_ar': item.category_name_ar,
        'description_ar': 'بيانات مرجعية من كتالوج موزع الصيدليات. سعر الدولار من المصدر مرجعي وليس سعر البيع النهائي. يلزم توثيق الحجم والمكونات والسعر المحلي والمخزون قبل الطلب.',
        'ingredients_text': '', 'directions_ar': '', 'warnings_ar': '', 'concern_tags': [],
        'catalog_origin': 'pharmacy-distribution',
        'source': {
            'repository': item.source_repository, 'commit': item.source_commit, 'path': item.source_path,
            'url': f'https://github.com/{item.source_repository}/blob/{item.source_commit}/{item.source_path}',
        },
        'variants': [{
            'sku': item.sku, 'pack_size_label_ar': 'الحجم غير موثق',
            'volume_ml': None, 'weight_g': None, 'price_syp': None, 'currency': 'SYP',
            'pao_months': None, 'image_url': '', 'available_quantity': 0, 'orderable': False,
            'reference_price': {'amount': format(item.reference_price_usd, '.2f'), 'currency': 'USD'},
        }],
    }


def public_supplier_listings(params):
    # Suppression is global, before filters: once local staff publish an
    # approved SKU, the old source listing must not reappear under old filters.
    approved_skus = ProductVariant.objects.filter(product__is_published=True).values_list('sku', flat=True)
    items = SupplierCatalogItem.objects.filter(is_visible=True).exclude(sku__in=approved_skus)
    if params.get('brand'):
        items = items.filter(brand_slug=params['brand'])
    if params.get('category'):
        items = items.filter(category_slug=params['category'])
    if params.get('concern'):
        # The upstream snapshot carries no reviewed concern tags.
        return []
    query = (params.get('q') or '').strip()[:200].casefold()
    result = []
    for item in items:
        if query and not any(query in value.casefold() for value in (item.sku, item.source_name, item.name_ar, item.brand_name, item.brand_name_ar)):
            continue
        result.append(source_listing(item))
    return result
