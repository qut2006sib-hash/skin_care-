"use client";
import Link from "next/link";
import {useEffect,useState} from "react";
import Brand from "@/components/Brand";
import {localDate,ORDERS_KEY,readStorage,removeStorage,SavedOrder} from "@/lib/shop";
export default function Orders(){
 const [orders,setOrders]=useState<SavedOrder[]>([]),[ready,setReady]=useState(false);
 useEffect(()=>{const raw=readStorage<unknown>(ORDERS_KEY,[]);setOrders(Array.isArray(raw)?raw.filter(o=>typeof o?.tracking_token==="string"&&typeof o?.order_number==="string"&&typeof o?.createdAt==="string"):[]);setReady(true);},[]);
 return <main className="page-wrap"><header className="subpage-header"><Brand/><Link href="/">متابعة التسوق ←</Link></header><div className="content-page"><h1>طلباتك على هذا الجهاز</h1><p className="small-note">نحفظ روابط الطلبات التي أرسلتها من هذا المتصفح. إذا استخدمت جهازًا آخر، افتح رابط التتبع الخاص بطلبك.</p>{!ready?<div className="empty-state">جارٍ التحميل…</div>:orders.length?<><div className="content-card">{orders.map(o=><div className="recent-order" key={o.tracking_token}><div><strong dir="ltr">{o.order_number}</strong><small>{localDate(o.createdAt)}</small></div><Link className="button outline" href={`/orders/track/${encodeURIComponent(o.tracking_token)}`}>تتبّع الطلب</Link></div>)}</div><button className="remove-button" style={{marginTop:20}} onClick={()=>{if(confirm("إزالة الروابط المحفوظة من هذا الجهاز؟ لن تُلغى الطلبات.")){removeStorage(ORDERS_KEY);setOrders([]);}}}>إزالة روابط الطلبات من هذا الجهاز</button></>:<div className="empty-state"><h2>لا توجد روابط محفوظة بعد</h2><p>بعد إرسال طلبك ستجد رابط تتبعه هنا.</p><Link href="/" className="button primary">تصفّح المنتجات</Link></div>}</div></main>;
}
