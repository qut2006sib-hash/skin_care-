"use client";
import React, { useState, useEffect, useMemo } from "react";

interface Variant {
  sku: string;
  pack_size_label_ar: string;
  price_syp: number;
  currency: string;
  pao_months: number | null;
  available_quantity: number;
}

interface Product {
  slug: string;
  name_ar: string;
  name_en: string;
  brand_name_en: string;
  brand_name_ar: string;
  category_slug: string;
  category_name_ar: string;
  description_ar: string;
  ingredients_text: string;
  directions_ar: string;
  warnings_ar: string;
  concern_tags: string[];
  variants: Variant[];
}

interface DeliveryZone {
  code: string;
  city: string;
  district_name_ar: string;
  fee_syp: number;
  estimated_hours: number;
}

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000/api/v1";

export default function HomsSkincareStorefront() {
  const [products, setProducts] = useState<Product[]>([]);
  const [zones, setZones] = useState<DeliveryZone[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("");
  const [selectedBrand, setSelectedBrand] = useState("");
  const [selectedConcern, setSelectedConcern] = useState("");
  const [cart, setCart] = useState<Record<string, number>>({});
  const [customerName, setCustomerName] = useState("");
  const [customerPhone, setCustomerPhone] = useState("");
  const [selectedZoneCode, setSelectedZoneCode] = useState("");
  const [addressLandmark, setAddressLandmark] = useState("");
  const [isOnline, setIsOnline] = useState(true);
  const [orderConfirmation, setOrderConfirmation] = useState<any>(null);
  const [errorMsg, setErrorMsg] = useState("");

  useEffect(() => {
    const saved = window.localStorage.getItem("homs_skincare_cart_v1");
    if (saved) {
      try {
        setCart(JSON.parse(saved));
      } catch {}
    }
    const handleOnline = () => setIsOnline(true);
    const handleOffline = () => setIsOnline(false);
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, []);

  useEffect(() => {
    fetch(`${API_BASE}/catalog/products/`)
      .then((r) => r.json())
      .then((d) => setProducts(d.products || []))
      .catch(() => {});
    fetch(`${API_BASE}/delivery-zones/`)
      .then((r) => r.json())
      .then((z) => setZones(z || []))
      .catch(() => {});
  }, []);

  const updateCart = (sku: string, nextQty: number) => {
    const updated = { ...cart };
    if (nextQty <= 0) {
      delete updated[sku];
    } else {
      updated[sku] = nextQty;
    }
    setCart(updated);
    window.localStorage.setItem("homs_skincare_cart_v1", JSON.stringify(updated));
  };

  const variantsBySku = useMemo(() => {
    const map: Record<string, { variant: Variant; product: Product }> = {};
    for (const p of products) {
      for (const v of p.variants) {
        map[v.sku] = { variant: v, product: p };
      }
    }
    return map;
  }, [products]);

  const subtotalSyp = useMemo(() => {
    return Object.entries(cart).reduce((sum, [sku, qty]) => {
      const entry = variantsBySku[sku];
      return sum + (entry ? entry.variant.price_syp * qty : 0);
    }, 0);
  }, [cart, variantsBySku]);

  const selectedZone = zones.find((z) => z.code === selectedZoneCode);
  const deliveryFeeSyp = selectedZone ? selectedZone.fee_syp : null;
  const finalTotalSyp = deliveryFeeSyp !== null ? subtotalSyp + deliveryFeeSyp : null;

  const submitGuestOrder = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg("");
    if (!isOnline) {
      setErrorMsg("لا يوجد اتصال بالإنترنت. سلتك محفوظة محلياً؛ يرجى المحاولة عند عودة الاتصال لتأكيد المخزون والسعر.");
      return;
    }
    if (!selectedZone || deliveryFeeSyp === null) {
      setErrorMsg("يرجى اختيار حي التوصيل المعتمد داخل مدينة حمص لمعرفة أجور التوصيل النهائية قبل إرسال الطلب.");
      return;
    }
    const idempotencyKey = `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    const items = Object.entries(cart).map(([sku, quantity]) => ({ sku, quantity }));
    const res = await fetch(`${API_BASE}/checkout/submit/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify({
        customer_name: customerName,
        customer_phone: customerPhone,
        delivery_zone_code: selectedZoneCode,
        address_landmark_ar: addressLandmark,
        items,
      }),
    });
    const payload = await res.json();
    if (!res.ok) {
      setErrorMsg(payload.detail || "تعذر إتمام الطلب؛ يرجى مراجعة الكميات المتوفرة.");
      return;
    }
    setOrderConfirmation(payload);
    setCart({});
    window.localStorage.removeItem("homs_skincare_cart_v1");
  };

  return (
    <div style={{ maxWidth: 1080, margin: "0 auto", padding: "16px 20px" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #DFD9D0", paddingBottom: 14 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: "1.4rem", color: "#1E4D40" }}>متجر العناية بالبشرة — حمص</h1>
          <p style={{ margin: "4px 0 0", fontSize: "0.9rem", color: "#5A6562" }}>
            منتجات أصلية من MEDEE و JUVELAB و FEME Hair • التوصيل داخل أحياء حمص • الدفع نقداً عند الاستلام (ل.س)
          </p>
        </div>
        <nav aria-label="التنقل الرئيسي" style={{ display: "flex", gap: 16, fontWeight: 600 }}>
          <a href="#catalog" style={{ color: "#1E4D40", textDecoration: "none" }}>الرئيسية</a>
          <a href="#filters" style={{ color: "#1E4D40", textDecoration: "none" }}>الفئات</a>
          <a href="#search" style={{ color: "#1E4D40", textDecoration: "none" }}>البحث</a>
          <a href="#cart" style={{ color: "#1E4D40", textDecoration: "none" }}>السلة ({Object.values(cart).reduce((a, b) => a + b, 0)})</a>
          <a href="#orders" style={{ color: "#1E4D40", textDecoration: "none" }}>طلباتي</a>
        </nav>
      </header>

      {!isOnline && (
        <div role="alert" style={{ background: "#FFF3CD", color: "#664D03", padding: 12, borderRadius: 8, marginTop: 12 }}>
          أنت غير متصل حالياً. سلتك محفوظة على جهازك ولن يتم تسجيل أي طلب وهمي دون تحقق مباشر من الخادم.
        </div>
      )}

      {errorMsg && (
        <div role="alert" style={{ background: "#F8D7DA", color: "#842029", padding: 12, borderRadius: 8, marginTop: 12 }}>
          {errorMsg}
        </div>
      )}

      <section id="filters" style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 16 }}>
        <input
          id="search"
          type="search"
          aria-label="ابحث عن منتج أو مكون"
          placeholder="ابحث بالاسم العربي أو الإنجليزي..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{ flex: 1, minWidth: 220, padding: "10px 12px", borderRadius: 8, border: "1px solid #C8C2B8" }}
        />
        <select aria-label="تصفية حسب الماركة" value={selectedBrand} onChange={(e) => setSelectedBrand(e.target.value)} style={{ padding: "10px 12px", borderRadius: 8 }}>
          <option value="">جميع الماركات (MEDEE / JUVELAB / FEME Hair)</option>
          <option value="MEDEE">MEDEE</option>
          <option value="JUVELAB">JUVELAB</option>
          <option value="FEME Hair">FEME Hair</option>
        </select>
        <select aria-label="تصفية حسب الفئة" value={selectedCategory} onChange={(e) => setSelectedCategory(e.target.value)} style={{ padding: "10px 12px", borderRadius: 8 }}>
          <option value="">جميع الفئات</option>
          <option value="serums">سيرومات العناية بالبشرة</option>
          <option value="cleansers">غسولات ومنظفات الوجه</option>
          <option value="moisturizers">كريمات الترطيب والوقاية</option>
          <option value="hair-care">العناية بالشعر وفروة الرأس</option>
        </select>
        <select aria-label="تصفية حسب حاجة البشرة أو الشعر" value={selectedConcern} onChange={(e) => setSelectedConcern(e.target.value)} style={{ padding: "10px 12px", borderRadius: 8 }}>
          <option value="">التسوق حسب الحاجة (وسوم معتمدة)</option>
          <option value="ترطيب">ترطيب</option>
          <option value="بشرة حساسة">بشرة حساسة</option>
          <option value="خالٍ من العطور">خالٍ من العطور</option>
          <option value="حماية من الشمس">حماية من الشمس</option>
          <option value="عناية بالشعر">عناية بالشعر</option>
        </select>
      </section>

      <section id="catalog" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 16, marginTop: 20 }}>
        {products
          .filter((p) => (!selectedBrand || p.brand_name_en === selectedBrand))
          .filter((p) => (!selectedCategory || p.category_slug === selectedCategory))
          .filter((p) => (!selectedConcern || p.concern_tags.includes(selectedConcern)))
          .filter((p) => (!searchQuery || p.name_ar.includes(searchQuery) || p.name_en.toLowerCase().includes(searchQuery.toLowerCase())))
          .map((p) => (
            <article key={p.slug} style={{ background: "#FFFFFF", border: "1px solid #E5E0D8", borderRadius: 12, padding: 16 }}>
              <div style={{ fontSize: "0.8rem", color: "#1E4D40", fontWeight: 700 }}>{p.brand_name_ar} • {p.category_name_ar}</div>
              <h2 style={{ fontSize: "1.05rem", margin: "6px 0" }}>{p.name_ar}</h2>
              <div style={{ fontSize: "0.82rem", color: "#666", direction: "ltr", textAlign: "right" }}>{p.name_en}</div>
              <p style={{ fontSize: "0.85rem", color: "#333", marginTop: 8 }}><strong>طريقة الاستخدام:</strong> {p.directions_ar}</p>
              <p style={{ fontSize: "0.82rem", color: "#7A2E2E", marginTop: 4 }}><strong>تحذيرات الملصق:</strong> {p.warnings_ar}</p>
              <p style={{ fontSize: "0.78rem", color: "#555", direction: "ltr", textAlign: "left", background: "#F6F4F0", padding: 8, borderRadius: 6 }}>
                <strong>INCI:</strong> {p.ingredients_text}
              </p>
              {p.variants.map((v) => (
                <div key={v.sku} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 12, paddingTop: 10, borderTop: "1px dashed #E5E0D8" }}>
                  <div>
                    <div style={{ fontWeight: 700 }}>الحجم: {v.pack_size_label_ar} <span style={{ fontSize: "0.75rem", direction: "ltr" }}>({v.sku})</span></div>
                    <div style={{ color: "#1E4D40", fontWeight: 700 }}>{v.price_syp.toLocaleString("ar-SY")} ل.س</div>
                    <div style={{ fontSize: "0.78rem", color: v.available_quantity > 0 ? "#2B6E46" : "#A63A3A" }}>
                      {v.available_quantity > 0 ? `متوفر للبيع الفوري (${v.available_quantity} عبوة)` : "غير متوفر حالياً"}
                    </div>
                  </div>
                  <button
                    disabled={v.available_quantity <= 0}
                    onClick={() => updateCart(v.sku, (cart[v.sku] || 0) + 1)}
                    style={{
                      padding: "10px 14px",
                      borderRadius: 8,
                      border: "none",
                      background: v.available_quantity > 0 ? "#1E4D40" : "#CCC",
                      color: "#FFF",
                      cursor: v.available_quantity > 0 ? "pointer" : "not-allowed",
                    }}
                  >
                    أضف للسلة
                  </button>
                </div>
              ))}
            </article>
          ))}
      </section>

      <section id="cart" style={{ background: "#FFFFFF", border: "1px solid #E5E0D8", borderRadius: 12, padding: 20, marginTop: 28 }}>
        <h2 style={{ marginTop: 0 }}>سلة التسوق وإتمام الطلب دون حساب (الدفع عند الاستلام — حمص)</h2>
        <form onSubmit={submitGuestOrder} style={{ display: "grid", gap: 12, maxWidth: 540 }}>
          <label>
            الاسم الكامل
            <input required value={customerName} onChange={(e) => setCustomerName(e.target.value)} style={{ width: "100%", padding: 8, marginTop: 4 }} />
          </label>
          <label>
            رقم الهاتف في سوريا (للتأكيد الهاتفي خلال 12 ساعة)
            <input required dir="ltr" value={customerPhone} onChange={(e) => setCustomerPhone(e.target.value)} placeholder="+963 9..." style={{ width: "100%", padding: 8, marginTop: 4 }} />
          </label>
          <label>
            حي التوصيل داخل مدينة حمص
            <select required value={selectedZoneCode} onChange={(e) => setSelectedZoneCode(e.target.value)} style={{ width: "100%", padding: 8, marginTop: 4 }}>
              <option value="">اختر الحي...</option>
              {zones.map((z) => (
                <option key={z.code} value={z.code}>
                  {z.district_name_ar} — أجور التوصيل: {z.fee_syp.toLocaleString("ar-SY")} ل.س
                </option>
              ))}
            </select>
          </label>
          <label>
            العنوان التفصيلي وأقرب نقطة علام
            <textarea required value={addressLandmark} onChange={(e) => setAddressLandmark(e.target.value)} style={{ width: "100%", padding: 8, marginTop: 4 }} />
          </label>
          <div style={{ background: "#F6F4F0", padding: 12, borderRadius: 8 }}>
            <div>مجموع المنتجات: <strong>{subtotalSyp.toLocaleString("ar-SY")} ل.س</strong></div>
            <div>أجور التوصيل: <strong>{deliveryFeeSyp !== null ? `${deliveryFeeSyp.toLocaleString("ar-SY")} ل.س` : "يحدد بعد اختيار الحي"}</strong></div>
            <div>الإجمالي النهائي المستحق عند الاستلام: <strong>{finalTotalSyp !== null ? `${finalTotalSyp.toLocaleString("ar-SY")} ل.س` : "—"}</strong></div>
          </div>
          <button type="submit" disabled={Object.keys(cart).length === 0 || finalTotalSyp === null} style={{ padding: "12px 16px", background: "#1E4D40", color: "#FFF", border: "none", borderRadius: 8, fontWeight: 700 }}>
            تأكيد وإرسال الطلب (الدفع نقداً عند الاستلام)
          </button>
        </form>

        {orderConfirmation && (
          <div id="orders" style={{ marginTop: 16, padding: 14, background: "#E8F5E9", borderRadius: 8 }}>
            <h3 style={{ margin: "0 0 6px" }}>تم استلام طلبك بنجاح: {orderConfirmation.order_number}</h3>
            <p style={{ margin: "4px 0" }}>المبلغ النهائي المسجل: {orderConfirmation.total_syp.toLocaleString("ar-SY")} ل.س</p>
            <p style={{ margin: "4px 0" }}>الحالة الحالية: بانتظار التأكيد الهاتفي (حجز المخزون صالح حتى {orderConfirmation.hold_expires_at})</p>
            <p style={{ margin: "4px 0" }}>رابط التتبع الخاص غير القابل للتخمين: <code>/orders/track/{orderConfirmation.tracking_token}</code></p>
          </div>
        )}
      </section>
    </div>
  );
}
