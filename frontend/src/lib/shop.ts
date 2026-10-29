export interface Variant {
  sku: string; pack_size_label_ar: string; price_syp: number | null; currency: string;
  orderable?: boolean; reference_price?: { amount: string; currency: string } | null;
  pao_months: number | null; available_quantity: number; image_url?: string;
}
export interface Product {
  slug: string; name_ar: string; name_en: string; brand_name_en: string; brand_name_ar: string;
  category_slug: string; category_name_ar: string; description_ar: string; ingredients_text: string;
  directions_ar: string; warnings_ar: string; concern_tags: string[]; variants: Variant[]; catalog_origin?: string;
}
export interface DeliveryZone { code: string; city: string; district_name_ar: string; fee_syp: number; estimated_hours: number }
export interface Cart { [sku: string]: number }
export interface OrderReceipt { order_number: string; tracking_token: string; total_syp: number; hold_expires_at: string; status: string; currency: string }
export interface CheckoutBody {
  customer_name: string; customer_phone: string; delivery_zone_code: string;
  address_landmark_ar: string; items: { sku: string; quantity: number }[]; expected_total_syp: number;
}
export interface CheckoutAttempt { key: string; body: CheckoutBody; createdAt: string }
export interface SavedOrder { order_number: string; tracking_token: string; createdAt: string }
export const CART_KEY = "homs_skincare_cart_v2";
export const ATTEMPT_KEY = "homs_skincare_attempt_v1";
export const ORDERS_KEY = "homs_skincare_orders_v1";
export function money(value: number) { return new Intl.NumberFormat("ar-SY", {maximumFractionDigits:0}).format(value) + " ل.س"; }
export function hasLocalSellingPrice(variant: Variant): boolean { return typeof variant.price_syp === "number" && Number.isSafeInteger(variant.price_syp) && variant.price_syp > 0 && variant.currency === "SYP"; }
export function isOrderableVariant(variant: Variant): boolean { return variant.orderable !== false && hasLocalSellingPrice(variant); }
export function isReferenceProduct(product: Product): boolean { return product.catalog_origin === "pharmacy-distribution"; }
export function referenceMoney(price: { amount: string; currency: string }): string | null {
  if(typeof price.amount!=="string"||!price.amount.trim())return null;
  const amount=Number(price.amount);
  if(price.currency !== "USD" || !Number.isFinite(amount) || amount < 0) return null;
  return `${amount.toLocaleString("en-US", {minimumFractionDigits:2,maximumFractionDigits:2})} USD`;
}
export function localDate(value: string) { return new Intl.DateTimeFormat("ar-SY", {dateStyle:"medium",timeStyle:"short",timeZone:"Asia/Damascus"}).format(new Date(value)); }
export function readStorage<T>(key: string, fallback: T): T {
  try { const value=localStorage.getItem(key); return value ? JSON.parse(value) as T : fallback; } catch { return fallback; }
}
export function writeStorage(key: string, value: unknown) { try { localStorage.setItem(key,JSON.stringify(value)); return true; } catch { return false; } }
export function removeStorage(key: string) { try { localStorage.removeItem(key); } catch {} }
export function validateCart(raw: unknown): Cart {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  return Object.fromEntries(Object.entries(raw).filter(([sku,qty])=>sku.length<=64 && typeof qty==="number" && Number.isInteger(qty) && qty>0 && qty<=999));
}
export async function api<T>(path: string): Promise<T> {
  const response=await fetch(`/api/v1/${path}`,{cache:"no-store"});
  if (!response.ok) throw new Error("تعذر تحميل البيانات. تحقق من الاتصال ثم حاول مجددًا.");
  return response.json();
}
export function errorText(payload: unknown): string {
  if (!payload || typeof payload!=="object") return "تعذر إتمام الطلب. راجع البيانات وحاول مجددًا.";
  const p=payload as Record<string,unknown>;
  if (typeof p.detail==="string") return p.detail;
  const labels:Record<string,string>={customer_name:"الاسم",customer_phone:"رقم الهاتف",delivery_zone_code:"الحي",address_landmark_ar:"العنوان",items:"المنتجات",expected_total_syp:"الإجمالي"};
  const errors=Object.entries(p).filter(([k])=>k!=="code").map(([k,v])=>`${labels[k]||k}: ${Array.isArray(v)?v.join(" "):typeof v==="string"?v: "قيمة غير صالحة"}`);
  return errors.join(" • ")||"تعذر إتمام الطلب. راجع البيانات وحاول مجددًا.";
}
export const statusLabels:Record<string,string>={PENDING_CONFIRMATION:"بانتظار التأكيد",CONFIRMED:"تم تأكيد الطلب",PREPARING:"قيد التجهيز",DISPATCHED:"مع مندوب التوصيل",DELIVERED:"تم التسليم",FAILED_DELIVERY:"تعذر التسليم",EXPIRED:"انتهت مهلة الحجز",CANCELLED:"ملغى"};
