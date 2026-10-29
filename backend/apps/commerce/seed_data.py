"""Synthetic fixtures for disposable demonstrations, never supplier/label evidence.

SKUs retain the original acceptance identifiers. Names, prices, ingredients,
stock, invoices and validity dates are illustrative and must not be published
as verified brand data. Date offsets are applied only on initial insertion.
"""
from datetime import timedelta
from django.utils import timezone

BRANDS = [
    {"slug": "medee", "name_en": "MEDEE", "name_ar": "ميدي"},
    {"slug": "juvelab", "name_en": "JUVELAB", "name_ar": "جوفيلاب"},
    {"slug": "feme-hair", "name_en": "FEME Hair", "name_ar": "فيمي هير"},
]
CATEGORIES = [
    {"slug": "serums", "name_ar": "سيرومات", "name_en": "Serums"},
    {"slug": "cleansers", "name_ar": "غسولات", "name_en": "Cleansers"},
    {"slug": "moisturizers", "name_ar": "كريمات", "name_en": "Creams"},
    {"slug": "hair-care", "name_ar": "العناية بالشعر", "name_en": "Hair care"},
]
# sku, brand, category, neutral Arabic name, size, price, cost, physical qty, lot
_DEMO_ROWS = [
    ("MEDEE-HYDRA-B5-30ML", "medee", "serums", "سيروم تجريبي", "30 مل", 145000, 88000, 6, "MED-B5-2601"),
    ("MEDEE-HYDRA-B5-50ML", "medee", "serums", "سيروم تجريبي — عبوة كبيرة", "50 مل", 215000, 135000, 8, "MED-B550-2605"),
    ("MEDEE-GENTLE-GEL-200ML", "medee", "cleansers", "غسول تجريبي", "200 مل", 110000, 68000, 12, "MED-CLN-2603"),
    ("MEDEE-NIACINAMIDE-10-30ML", "medee", "serums", "سيروم عناية تجريبي", "30 مل", 135000, 82000, 9, "MED-NIA-2604"),
    ("JUVELAB-CICA-BARRIER-50ML", "juvelab", "moisturizers", "كريم تجريبي", "50 مل", 160000, 98000, 7, "JUV-CICA-2602"),
    ("JUVELAB-RETINOL-NIGHT-30ML", "juvelab", "moisturizers", "كريم تجريبي غير متوفر", "30 مل", 185000, 110000, 0, None),
    ("JUVELAB-UV-FLUID-SPF50-50ML", "juvelab", "moisturizers", "كريم نهاري تجريبي", "50 مل", 155000, 94000, 15, "JUV-SPF-2606"),
    ("FEME-SULFATEFREE-SHAMPOO-400ML", "feme-hair", "hair-care", "شامبو تجريبي", "400 مل", 125000, 75000, 14, "FEM-SHM-2602"),
    ("FEME-ROSEMARY-TONIC-100ML", "feme-hair", "hair-care", "مستحضر شعر تجريبي", "100 مل", 140000, 84000, 5, "FEM-TON-2607"),
    ("FEME-BIOTIN-MASK-250ML", "feme-hair", "hair-care", "ماسك تجريبي قيد فحص الصلاحية", "250 غ", 150000, 90000, 10, "FEM-MSK-2609-HOLD"),
]


def demo_skus(today=None):
    today = today or timezone.localdate()
    result = []
    for index, (sku, brand, category, name, size, price, cost, qty, lot) in enumerate(_DEMO_ROWS):
        held = sku == "FEME-BIOTIN-MASK-250ML"
        batches = []
        if qty:
            batches.append({
                "lot_code": lot,
                "supplier_invoice_ref": f"DEMO-INVOICE-{index + 1:02}",
                "unit_cost_syp": cost,
                "expiry_date": None if held else today + timedelta(days=180 + index * 20),
                "condition": "QUARANTINE" if held else "SALEABLE",
                "qty_on_hand": qty,
                "validity_evidence_ref": "DEMO: synthetic validity data; no supplier evidence",
            })
        if index == 0:
            batches.append({
                "lot_code": "MED-B5-2608", "supplier_invoice_ref": "DEMO-INVOICE-SECOND-LOT",
                "unit_cost_syp": 92000, "expiry_date": today + timedelta(days=400),
                "condition": "SALEABLE", "qty_on_hand": 10,
                "validity_evidence_ref": "DEMO: synthetic second lot",
            })
        result.append({
            "sku": sku, "brand": brand, "category": category,
            "name_ar": name, "name_en": f"Demo product {index + 1}",
            "pack_size_label_ar": size, "price_syp": price,
            "volume_ml": None if held else int(size.split()[0]),
            "weight_g": 250 if held else None, "pao_months": 12,
            "min_remaining_validity_days": 60,
            "concern_tags": ["عناية بالشعر"] if category == "hair-care" else ["عناية بالبشرة"],
            "ingredients_text": "Demo only — ingredients have not been verified.",
            "directions_ar": "بيانات تجريبية؛ تُضاف تعليمات الملصق المعتمدة قبل البيع الفعلي.",
            "warnings_ar": "هذا المنتج والبيانات المعروضة للتجربة فقط.",
            "claim_evidence_source": "DEMO-NOT-VERIFIED", "batches": batches,
        })
    return result


# Compatibility for tools that inspect the fixture module. Seeder calls the
# function at execution time so its dates do not depend on process startup.
SKUS = demo_skus()
HOMS_DELIVERY_ZONES = [
    {"city": "Homs", "code": code, "district_name_ar": ar, "district_name_en": en, "fee_syp": None, "is_active": False}
    for code, ar, en in [
        ("homs-al-waer", "الوعر", "Al-Waer"),
        ("homs-akrama", "عكرمة", "Akrama"),
        ("homs-al-inshaat", "الإنشاءات", "Al-Inshaat"),
        ("homs-al-hamidiyah", "الحميدية", "Al-Hamidiyah"),
        ("homs-al-ghouta", "الغوطة", "Al-Ghouta"),
    ]
]
