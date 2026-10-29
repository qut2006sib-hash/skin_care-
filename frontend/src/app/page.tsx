"use client";
import Link from "next/link";
import {useCallback,useEffect,useMemo,useRef,useState} from "react";
import Brand,{BagIcon} from "@/components/Brand";
import ProductArt from "@/components/ProductArt";
import {api,ATTEMPT_KEY,CART_KEY,ORDERS_KEY,CheckoutAttempt,CheckoutBody,Cart,DeliveryZone,Product,Variant,OrderReceipt,SavedOrder,errorText,localDate,money,readStorage,removeStorage,validateCart,writeStorage,isOrderableVariant,isReferenceProduct,referenceMoney,hasLocalSellingPrice} from "@/lib/shop";

function VariantPrice({variant}:{variant:Variant}) {
 const sourcePrice=variant.reference_price?referenceMoney(variant.reference_price):null;
 if(hasLocalSellingPrice(variant)) return <strong className="price">{money(variant.price_syp as number)}</strong>;
 if(sourcePrice) return <span className="reference-price"><strong dir="ltr">{sourcePrice}</strong><small>سعر المصدر · مرجعي</small></span>;
 return <span className="reference-price"><small>سعر البيع غير محدد</small></span>;
}
export default function Storefront(){
 const [products,setProducts]=useState<Product[]>([]),[zones,setZones]=useState<DeliveryZone[]>([]);
 const [loading,setLoading]=useState(true),[loadError,setLoadError]=useState("");
 const [demo,setDemo]=useState(false),[query,setQuery]=useState(""),[category,setCategory]=useState(""),[brand,setBrand]=useState(""),[availableOnly,setAvailableOnly]=useState(false);
 const [cart,setCart]=useState<Cart>({}),[cartOpen,setCartOpen]=useState(false),[detail,setDetail]=useState<Product|null>(null);
 const [name,setName]=useState(""),[phone,setPhone]=useState(""),[zoneCode,setZoneCode]=useState(""),[address,setAddress]=useState("");
 const [online,setOnline]=useState(true),[sending,setSending]=useState(false),[attempt,setAttempt]=useState<CheckoutAttempt|null>(null);
 const [error,setError]=useState(""),[notice,setNotice]=useState(""),[receipt,setReceipt]=useState<OrderReceipt|null>(null);
 const [ready,setReady]=useState(false);
 const inFlight=useRef(false), cartClose=useRef<HTMLButtonElement>(null);
 const loadCatalog=useCallback(async()=>{
  setLoading(true);setLoadError("");
  try{
   const [catalog,areas]=await Promise.all([api<{products:Product[];demo_mode?:boolean}>("catalog/products/"),api<DeliveryZone[]>("delivery-zones/")]);
   setProducts(catalog.products||[]);setDemo(Boolean(catalog.demo_mode));setZones(areas);
  }catch(e){setLoadError(e instanceof Error?e.message:"تعذر تحميل المنتجات.");}
  finally{setLoading(false);}
 },[]);
 useEffect(()=>{
  const saved=readStorage<unknown>(CART_KEY,null)??readStorage<unknown>("homs_skincare_cart_v1",{});
  setCart(validateCart(saved));
  const pending=readStorage<CheckoutAttempt|null>(ATTEMPT_KEY,null);
  if(pending?.key&&pending?.body?.items?.length){
   setAttempt(pending);setName(pending.body.customer_name);setPhone(pending.body.customer_phone);setZoneCode(pending.body.delivery_zone_code);setAddress(pending.body.address_landmark_ar);setCartOpen(true);
  }
  setOnline(navigator.onLine);setReady(true);
  const update=()=>setOnline(navigator.onLine);
  window.addEventListener("online",update);window.addEventListener("offline",update);
  void loadCatalog();
  return()=>{window.removeEventListener("online",update);window.removeEventListener("offline",update);};
 },[loadCatalog]);
 useEffect(()=>{if(ready)writeStorage(CART_KEY,cart);},[cart,ready]);
 useEffect(()=>{
  if(!cartOpen&&!detail)return;
  const previous=document.activeElement as HTMLElement|null;
  document.body.style.overflow="hidden";
  const onKey=(e:KeyboardEvent)=>{if(e.key==="Escape"&&!sending){setCartOpen(false);setDetail(null);}};
  window.addEventListener("keydown",onKey);
  requestAnimationFrame(()=>document.querySelector<HTMLElement>("[role=dialog] button")?.focus());
  return()=>{document.body.style.overflow="";window.removeEventListener("keydown",onKey);previous?.focus();};
 },[cartOpen,detail,sending]);
 const variants=useMemo(()=>Object.fromEntries(products.flatMap(p=>p.variants.map(v=>[v.sku,{product:p,variant:v}]))),[products]);
 const count=Object.values(cart).reduce((a,b)=>a+b,0);
 const subtotal=Object.entries(cart).reduce((sum,[sku,qty])=>{const entry=variants[sku];return sum+(entry&&!isReferenceProduct(entry.product)&&isOrderableVariant(entry.variant)?(entry.variant.price_syp as number)*qty:0);},0);
 const invalidCart=Object.entries(cart).some(([sku,qty])=>!variants[sku]||isReferenceProduct(variants[sku].product)||!isOrderableVariant(variants[sku].variant)||variants[sku].variant.available_quantity<qty);
 const zone=zones.find(z=>z.code===zoneCode),total=zone&&!invalidCart?subtotal+zone.fee_syp:null;
 const categories=Array.from(new Map(products.map(p=>[p.category_slug,p.category_name_ar])).entries());
 const brands=Array.from(new Set(products.map(p=>p.brand_name_en)));
 const filtered=products.filter(p=>(!category||p.category_slug===category)&&(!brand||p.brand_name_en===brand)&&(!availableOnly||p.variants.some(v=>isOrderableVariant(v)&&v.available_quantity>0))&&(!query||`${p.name_ar} ${p.name_en} ${p.brand_name_en} ${p.ingredients_text} ${p.variants.map(v=>v.sku).join(" ")}`.toLowerCase().includes(query.trim().toLowerCase())));
 function updateCart(sku:string,qty:number){
  const entry=variants[sku];
  if(qty>0&&(!entry||isReferenceProduct(entry.product)||!isOrderableVariant(entry.variant)))return;
  if(attempt){setError("تحقق من نتيجة المحاولة السابقة أولًا قبل تعديل السلة.");setCartOpen(true);return;}
  setReceipt(null);setNotice("");
  setCart(current=>{const next={...current};if(qty<=0)delete next[sku];else next[sku]=Math.min(999,qty);return next;});
 }
 async function sendAttempt(current:CheckoutAttempt){
  if(inFlight.current)return;
  if(!navigator.onLine){setError("لا يوجد اتصال. سلتك محفوظة، ولن يُرسل الطلب حتى عودة الاتصال.");return;}
  inFlight.current=true;setSending(true);setError("");setNotice("");
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),25000);
  try{
   const response=await fetch("/api/v1/checkout/submit/",{method:"POST",headers:{"Content-Type":"application/json","Idempotency-Key":current.key},body:JSON.stringify(current.body),signal:controller.signal,cache:"no-store"});
   const payload=await response.json();
   if(!response.ok){
    if(response.status>=500){throw new Error("unknown");}
    if(payload.code==="idempotency_conflict"){
     setError("توجد محاولة سابقة بهذه البيانات. احتفظ بهذه الصفحة وتواصل مع مسؤول المتجر للتحقق قبل إنشاء طلب جديد.");return;
    }
    setAttempt(null);removeStorage(ATTEMPT_KEY);
    if(payload.code==="price_changed"){
     setError("تغيّر السعر أو رسم التوصيل. حدّثنا البيانات؛ راجع الإجمالي الجديد ثم أكد الطلب إذا وافقت.");
     await loadCatalog();
    }else{setError(errorText(payload));if(response.status===409)await loadCatalog();}
    return;
   }
   const result=payload as OrderReceipt;
   if(!result.order_number||!result.tracking_token)throw new Error("unknown");
   const rawRecent=readStorage<unknown>(ORDERS_KEY,[]);
   const recent:SavedOrder[]=Array.isArray(rawRecent)?rawRecent.filter(o=>typeof o?.tracking_token==="string"&&typeof o?.order_number==="string"&&typeof o?.createdAt==="string"):[];
   writeStorage(ORDERS_KEY,[{order_number:result.order_number,tracking_token:result.tracking_token,createdAt:new Date().toISOString()},...recent.filter(o=>o.tracking_token!==result.tracking_token)].slice(0,20));
   setReceipt(result);setCart({});removeStorage(CART_KEY);removeStorage("homs_skincare_cart_v1");setAttempt(null);removeStorage(ATTEMPT_KEY);
   void loadCatalog();
  }catch{
   setError("لم تصلنا نتيجة مؤكدة للطلب. قد يكون الخادم قد استلمه. اضغط «التحقق من الطلب السابق» لإعادة نفس المحاولة دون إنشاء طلب مكرر.");
  }finally{clearTimeout(timeout);setSending(false);inFlight.current=false;}
 }
 async function submit(e:React.FormEvent){
  e.preventDefault();
  if(inFlight.current)return;
  if(attempt){await sendAttempt(attempt);return;}
  if(total===null||!count||invalidCart||loading)return;
  if(!window.crypto?.getRandomValues){setError("يتعذر إنشاء محاولة آمنة في هذا المتصفح. استخدم متصفحًا محدثًا.");return;}
  const bytes=new Uint8Array(24);window.crypto.getRandomValues(bytes);
  const key="web-"+Array.from(bytes,b=>b.toString(16).padStart(2,"0")).join("");
  const body:CheckoutBody={customer_name:name.trim(),customer_phone:phone.trim(),delivery_zone_code:zoneCode,address_landmark_ar:address.trim(),items:Object.entries(cart).sort(([a],[b])=>a.localeCompare(b)).map(([sku,quantity])=>({sku,quantity})),expected_total_syp:total};
  const next={key,body,createdAt:new Date().toISOString()};
  if(!writeStorage(ATTEMPT_KEY,next)){setError("اسمح بحفظ بيانات الموقع على جهازك لإرسال الطلب بأمان وإعادة المحاولة دون تكراره.");return;}
  setAttempt(next);await sendAttempt(next);
 }
 function openCart(){setDetail(null);setCartOpen(true);setNotice("");}
 const trapFocus=(e:React.KeyboardEvent<HTMLDivElement>)=>{
  if(e.key!=="Tab")return;
  const nodes=Array.from(e.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex="0"]')).filter(el=>el.getClientRects().length);
  const first=nodes[0],last=nodes[nodes.length-1];
  if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
 };
 return <>
  <a href="#catalog" className="skip-link">انتقل إلى المنتجات</a>
  {demo&&<div className="demo-bar">نسخة تجريبية — المنتجات والأسعار والمعلومات للعرض والاختبار، وليست عرض بيع فعليًا.</div>}
  <header className="site-header"><div className="header-inner"><Brand/><nav aria-label="التنقل الرئيسي"><a href="#catalog" className="nav-active">المنتجات</a><Link href="/orders">طلباتي</Link><Link href="/help">المساعدة</Link></nav><button className="bag-button" onClick={openCart}><BagIcon/><span>السلة</span><b>{count}</b></button></div></header>
  <main className="page-wrap">
   {!online&&<div className="message warning" role="status">أنت غير متصل الآن. سلتك محفوظة على هذا الجهاز؛ إرسال الطلب وتتبعه يحتاجان اتصالًا.</div>}
   <section className="hero">
    <div className="hero-copy"><span className="location-pill"><span/>التوصيل ضمن أحياء حمص المفعّلة</span><h1>مساحة صغيرة<br/>لعنايتك اليومية.</h1><p>تصفّح العناية بالبشرة والشعر، اختر العبوة المناسبة، وراجع السعر والتوصيل قبل الطلب.</p><div className="hero-actions"><a href="#catalog" className="button primary">اكتشف المنتجات <span aria-hidden="true">↙</span></a><span className="payment-note">الدفع نقدًا<br/><strong>عند الاستلام</strong></span></div></div>
    <div className="hero-still" aria-label="رسوم توضيحية لعبوات العناية"><div className="still-orbit"/><div className="still-bottle one"><ProductArt brand="CARE" category="serums" large/></div><div className="still-bottle two"><ProductArt brand="DAILY" category="moisturizers" large/></div><span className="still-caption">العناية تبدأ بالاختيار الواضح</span><span className="still-note">رسوم توضيحية للعبوات</span></div>
   </section>
   <div className="service-strip"><span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 8h14v9H3zM6 8V5a4 4 0 0 1 8 0v3"/></svg>طلب دون إنشاء حساب</span><span><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7"/><path d="m6 10 3 3 5-6"/></svg>إجمالي واضح قبل التأكيد</span><span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M2 5h10v10H2zM12 9h4l2 3v3h-6M4 17h2M14 17h2"/></svg>تتبّع حالة الطلب</span></div>
   <section id="catalog" className="catalog-section">
    <div className="section-heading"><div><span className="section-kicker">ما يناسب روتينك</span><h2>تصفّح العناية</h2></div><span className="product-count">{loading?"جارٍ التحديث…":`${filtered.length} منتجات`}</span></div>
    {products.some(isReferenceProduct)&&<div className="source-catalog-note"><strong>من كتالوج منتجات التوزيع</strong><p>المنتجات المستوردة متاحة للتصفح بأسعار المصدر بالدولار. السعر مرجعي؛ يلزم اعتماد بيانات العبوة وسعر البيع بالليرة والمخزون قبل إتاحة الطلب.</p></div>}
    <div className="filter-bar"><label className="search-field"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg><input type="search" placeholder="اسم المنتج، الماركة، أو المكوّن" aria-label="البحث عن منتج" value={query} onChange={e=>setQuery(e.target.value)}/></label><select aria-label="الماركة" value={brand} onChange={e=>setBrand(e.target.value)}><option value="">جميع الماركات</option>{brands.map(b=><option key={b} value={b}>{b}</option>)}</select><label className="availability-toggle"><input type="checkbox" checked={availableOnly} onChange={e=>setAvailableOnly(e.target.checked)}/>المتوفر فقط</label></div>
    <div className="category-tabs" role="group" aria-label="فئات المنتجات"><button className={!category?"selected":""} onClick={()=>setCategory("")}>كل العناية</button>{categories.map(([slug,label])=><button key={slug} className={category===slug?"selected":""} onClick={()=>setCategory(slug)}>{label}</button>)}</div>
    {loadError?<div className="empty-state"><h3>تعذر تحميل المتجر</h3><p>{loadError}</p><button className="button primary" onClick={()=>void loadCatalog()}>إعادة المحاولة</button></div>:loading&&products.length===0?<div className="product-grid" aria-label="جارٍ تحميل المنتجات" aria-busy="true">{[1,2,3,4].map(n=><div className="skeleton-card" key={n}><div/><span/><span/></div>)}</div>:filtered.length===0?<div className="empty-state"><h3>{products.length?"لم نجد منتجات بهذه الخيارات":"الكتالوج قيد التجهيز"}</h3><p>{products.length?"جرّب اسمًا آخر أو اعرض جميع الفئات.":"ستظهر المنتجات بعد إضافتها ونشرها من إدارة المتجر."}</p>{products.length>0&&<button className="button outline" onClick={()=>{setQuery("");setBrand("");setCategory("");setAvailableOnly(false);}}>عرض كل المنتجات</button>}</div>:<div className="product-grid">{filtered.map((p,index)=>{
     const v=p.variants[0];if(!v)return null;
     const reference=isReferenceProduct(p);
     const stock=!reference&&p.variants.some(v=>isOrderableVariant(v)&&v.available_quantity>0);
     return <article className="product-card" key={p.slug}>
      <button className="product-visual" onClick={()=>setDetail(p)} aria-label={`عرض تفاصيل ${p.name_ar}`}>
       {v.image_url?<img src={v.image_url} alt={p.name_ar} loading="lazy"/>:<ProductArt category={p.category_slug} brand={p.brand_name_en}/>}
       <span className={`stock-pill ${stock?"":"out"}`}>{reference?"للتصفح":stock?"متوفر":"غير متوفر"}</span>
      </button>
      <div className="product-info"><span className="brand-label" dir="ltr">{p.brand_name_en}</span><button className="product-title" onClick={()=>setDetail(p)}><h3>{p.name_ar}</h3></button><p className="pack-label">{v.pack_size_label_ar||"حجم العبوة غير موثق"}{p.variants.length>1?` · ${p.variants.length} أحجام`:""}</p><div className="product-bottom"><VariantPrice variant={v}/>{reference?<button className="add-button browse-button" onClick={()=>setDetail(p)} aria-label={`عرض تفاصيل ${p.name_ar} من المصدر`}>عرض التفاصيل</button>:<button className="add-button" disabled={!stock||Boolean(attempt)||(p.variants.length===1&&(cart[v.sku]||0)>=v.available_quantity)} onClick={()=>{if(p.variants.length>1)setDetail(p);else{updateCart(v.sku,(cart[v.sku]||0)+1);setNotice(`أُضيف ${p.name_ar} إلى السلة`);}}} aria-label={`أضف ${p.name_ar} إلى السلة`}><span aria-hidden="true">+</span><span>أضف للسلة</span></button>}</div></div>
     </article>;
    })}</div>}
   </section>
   <section className="quiet-note"><div><h2>تفاصيل تساعدك على الاختيار.</h2><p>راجع الحجم والمكوّنات وطريقة الاستخدام قبل الطلب. إن لم تكن المعلومات واضحة، انتظر توضيح المتجر قبل الشراء.</p></div><Link href="/help" className="text-link">التوصيل والطلبات <span aria-hidden="true">←</span></Link></section>
  </main>
  <footer className="site-footer"><div className="page-wrap footer-inner"><Brand/><p>العناية بالبشرة والشعر في حمص</p><div><Link href="/help">المساعدة والخصوصية</Link><a href="/staff/">دخول الموظفين</a></div></div></footer>
  {notice&&<div className="toast" role="status"><span>✓ {notice}</span><button onClick={openCart}>عرض السلة</button><button aria-label="إغلاق التنبيه" onClick={()=>setNotice("")}>×</button></div>}
  {detail&&<div className="modal-backdrop" onClick={e=>{if(e.target===e.currentTarget)setDetail(null);}}><div className="product-dialog" role="dialog" aria-modal="true" aria-labelledby="detail-title" onKeyDown={trapFocus}><button className="close-button" aria-label="إغلاق التفاصيل" onClick={()=>setDetail(null)}>×</button><div className="detail-visual">{detail.variants[0]?.image_url?<img src={detail.variants[0].image_url} alt={detail.name_ar}/>:<ProductArt category={detail.category_slug} brand={detail.brand_name_en} large/>}</div><div className="detail-content"><span className="brand-label" dir="ltr">{detail.brand_name_en}</span><h2 id="detail-title">{detail.name_ar}</h2><p>{detail.description_ar}</p>{demo&&<p className="small-note">بيانات تجريبية؛ يجب التحقق من ملصق المنتج الفعلي قبل البيع.</p>}{isReferenceProduct(detail)&&<div className="message source-detail-note"><strong>للتصفح، بانتظار تجهيز البيع</strong><p>لا يتضمن المصدر حجم عبوة موثقًا أو مكوّنات أو صورًا معتمدة لهذا المنتج. الرسم توضيحي، وسعر المصدر بالدولار ليس سعر بيع نهائيًا بالليرة.</p></div>}<div className="variant-list">{detail.variants.map(v=><div className="variant-row" key={v.sku}><div><strong>{v.pack_size_label_ar||"حجم العبوة غير موثق"}</strong><VariantPrice variant={v}/><small>{isReferenceProduct(detail)?"الطلب غير مفعّل":v.available_quantity>0?`المتاح ${v.available_quantity} عبوة`:"غير متوفر حاليًا"}</small></div>{!isReferenceProduct(detail)&&<button className="button primary" disabled={!isOrderableVariant(v)||v.available_quantity<=0||Boolean(attempt)||(cart[v.sku]||0)>=v.available_quantity} onClick={()=>{updateCart(v.sku,(cart[v.sku]||0)+1);setDetail(null);setNotice("أُضيف المنتج إلى السلة");}}>أضف للسلة</button>}</div>)}</div><details open><summary>المكوّنات</summary><p dir="auto" className="ingredients">{detail.ingredients_text||(isReferenceProduct(detail)?"لم يورد المصدر قائمة المكوّنات.":"لم تُضف المكوّنات بعد.")}</p></details><details><summary>طريقة الاستخدام والتحذيرات</summary><p>{detail.directions_ar||"طريقة الاستخدام غير موثقة بعد."}</p><p>{detail.warnings_ar||"تحذيرات العبوة غير موثقة بعد."}</p></details></div></div></div>}
  {cartOpen&&<div className="modal-backdrop cart-backdrop" onClick={e=>{if(e.target===e.currentTarget&&!sending)setCartOpen(false);}}><div className="cart-panel" role="dialog" aria-modal="true" aria-labelledby="cart-title" onKeyDown={trapFocus}><div className="panel-heading"><div><span className="section-kicker">كل شيء قبل التأكيد</span><h2 id="cart-title">سلة العناية <small>({count})</small></h2></div><button className="close-button" ref={cartClose} aria-label="إغلاق السلة" onClick={()=>setCartOpen(false)} disabled={sending}>×</button></div><div className="cart-scroll">
   {error&&<div className="message error" role="alert">{error}</div>}
   {receipt?<div className="receipt"><div className="receipt-check">✓</div><h3>وصل طلبك إلى المتجر</h3><p>رقم الطلب <strong dir="ltr">{receipt.order_number}</strong></p><p>الإجمالي المسجل: <strong>{money(receipt.total_syp)}</strong></p><p>الحالة: بانتظار التأكيد. تنتهي مهلة الحجز في {localDate(receipt.hold_expires_at)}.</p><Link className="button primary" href={`/orders/track/${encodeURIComponent(receipt.tracking_token)}`}>تتبّع الطلب</Link><p className="small-note">الرابط خاص بطلبك. احفظه ولا تشاركه علنًا.</p></div>:<>
   {attempt&&<div className="message warning"><strong>محاولة طلب محفوظة</strong><p>نحافظ على نفس المحاولة حتى نعرف نتيجتها. لا تعدّل السلة أو ترسل طلبًا جديدًا الآن.</p><button className="button outline" disabled={sending||!online} onClick={()=>void sendAttempt(attempt)}>{sending?"جارٍ التحقق…":"التحقق من الطلب السابق"}</button></div>}
   {count===0&&!attempt?<div className="empty-state"><BagIcon/><h3>سلتك جاهزة لاختيارك الأول</h3><p>أضف منتجًا، ثم راجع الكمية والتوصيل هنا.</p><button className="button primary" onClick={()=>setCartOpen(false)}>متابعة التصفح</button></div>:<>
    <div className="cart-items">{Object.entries(cart).map(([sku,qty])=>{const entry=variants[sku];return <div className="cart-item" key={sku}><div className="cart-item-art"><ProductArt category={entry?.product.category_slug} brand={entry?.product.brand_name_en}/></div><div className="cart-item-main"><h3>{entry?.product.name_ar||sku}</h3><span>{entry?.variant.pack_size_label_ar||"هذا المنتج لم يعد متاحًا"}</span><strong>{entry&&!isReferenceProduct(entry.product)&&isOrderableVariant(entry.variant)?money((entry.variant.price_syp as number)*qty):"غير متاح للطلب؛ أزله من السلة"}</strong><div className="quantity-row"><div className="quantity-control"><button type="button" aria-label={`إنقاص كمية ${entry?.product.name_ar||sku}`} disabled={Boolean(attempt)||sending} onClick={()=>updateCart(sku,qty-1)}>−</button><span aria-label="الكمية">{qty}</span><button type="button" aria-label={`زيادة كمية ${entry?.product.name_ar||sku}`} disabled={Boolean(attempt)||sending||!entry||isReferenceProduct(entry.product)||!isOrderableVariant(entry.variant)||qty>=entry.variant.available_quantity} onClick={()=>updateCart(sku,qty+1)}>+</button></div><button className="remove-button" type="button" disabled={Boolean(attempt)||sending} onClick={()=>updateCart(sku,0)}>إزالة</button></div>{entry&&qty>entry.variant.available_quantity&&<small className="field-error">المتاح الآن {entry.variant.available_quantity} فقط؛ عدّل الكمية.</small>}</div></div>;})}</div>
    <form onSubmit={submit} className="checkout-form"><h3>إلى أين نرسل طلبك؟</h3><p className="small-note">لا تحتاج إلى حساب. يستخدم المتجر هذه البيانات لتأكيد الطلب وتوصيله.</p><fieldset disabled={sending||Boolean(attempt)}><label>الاسم الكامل<input required autoComplete="name" maxLength={160} value={name} onChange={e=>setName(e.target.value)}/></label><label>رقم الهاتف<input required type="tel" dir="ltr" autoComplete="tel" maxLength={40} placeholder="09xxxxxxxx" value={phone} onChange={e=>setPhone(e.target.value)}/></label><label>حي التوصيل<select aria-label="حي التوصيل" required value={zoneCode} onChange={e=>setZoneCode(e.target.value)}><option value="">اختر الحي داخل حمص</option>{zones.map(z=><option key={z.code} value={z.code}>{z.district_name_ar} — {money(z.fee_syp)}</option>)}</select></label>{zones.length===0&&!loading&&<p className="field-error">لا توجد أحياء مفعّلة للتوصيل بعد. يمكن متابعة التصفح؛ الطلب غير متاح الآن.</p>}<label>العنوان وأقرب نقطة علام<textarea aria-label="العنوان وأقرب نقطة علام" required rows={3} maxLength={1000} autoComplete="street-address" placeholder="الشارع، البناء، الطابق، وأقرب نقطة معروفة" value={address} onChange={e=>setAddress(e.target.value)}/></label></fieldset>
     <div className="order-totals"><div><span>مجموع المنتجات</span><strong>{invalidCart?"يحتاج إلى مراجعة":money(subtotal)}</strong></div><div><span>التوصيل</span><strong>{zone?money(zone.fee_syp):"اختر الحي أولًا"}</strong></div><div className="grand-total"><span>المستحق عند الاستلام</span><strong>{total!==null?money(total):"—"}</strong></div></div>
     {invalidCart&&!loading&&<p className="field-error">راجع السلة؛ أحد المنتجات غير متاح للطلب أو تغيّر توفره.</p>}
     {!attempt&&<button className="button primary checkout-submit" type="submit" disabled={sending||loading||!count||total===null||invalidCart||!online}>{sending?"جارٍ إرسال الطلب…":"تأكيد الطلب والدفع عند الاستلام"}</button>}
     <p className="small-note">بتأكيد الطلب توافق على <Link href="/help">معلومات الطلب والتوصيل والخصوصية</Link>. لا يتم تحصيل أي دفع إلكتروني.</p>
    </form>
   </>}
   </>}
  </div></div></div>}
 </>;
}
