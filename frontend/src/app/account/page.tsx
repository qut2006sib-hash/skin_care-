"use client";
import Link from "next/link";
import {useCallback,useEffect,useState} from "react";
import AccountHeader from "@/components/AccountHeader";
import {accountRequest,useAccount} from "@/lib/accounts";
import {localDate,money,statusLabels} from "@/lib/shop";
interface CustomerOrder{order_number:string;tracking_token:string;total_syp:number;status:string;status_label_ar?:string;created_at:string}
export default function CustomerAccount(){
 const {session,error:sessionError,reload:reloadSession}=useAccount("customer");
 const [orders,setOrders]=useState<CustomerOrder[]>([]),[error,setError]=useState(""),[loading,setLoading]=useState(true);
 const load=useCallback(async()=>{setError("");setLoading(true);try{setOrders((await accountRequest<{orders:CustomerOrder[]}>("customer/orders/")).orders);}catch(e){setError(e instanceof Error?e.message:"تعذر تحميل الطلبات.");}finally{setLoading(false);}},[]);
 useEffect(()=>{if(session)void load();},[session,load]);
 if(!session)return <main className="page-wrap content-page"><h1>حساب الزبون</h1><p role="status">{sessionError||"جارٍ التحقق من الحساب…"}</p>{sessionError&&<button className="button outline" onClick={()=>void reloadSession()}>إعادة المحاولة</button>}</main>;
 return <main className="page-wrap"><AccountHeader session={session}/><section className="workspace-heading"><div><h1>طلباتك في العناية</h1><p>الطلبات التي ترسلها أثناء تسجيل الدخول تظهر هنا على أجهزتك. تجهيزها وتوصيلها من المستودع المركزي.</p></div><Link className="button primary" href="/shop">تصفّح المنتجات</Link></section>{error&&<div role="alert" className="message error">{error}</div>}<div className="workspace-toolbar"><h2>سجل الطلبات</h2><button className="button outline" disabled={loading} onClick={()=>void load()}>{loading?"جارٍ التحميل…":"تحديث الطلبات"}</button></div>{loading?<p role="status">جارٍ تحميل طلباتك…</p>:orders.length?<div className="content-card">{orders.map(o=><div className="recent-order" key={o.order_number}><div><strong dir="ltr">{o.order_number}</strong><small>{localDate(o.created_at)}</small><span>{o.status_label_ar||statusLabels[o.status]}</span></div><strong>{money(o.total_syp)}</strong><Link className="button outline" href={`/orders/track/${encodeURIComponent(o.tracking_token)}`}>تتبّع الطلب</Link></div>)}</div>:!error&&<div className="empty-state"><h2>ابدأ طلبك الأول</h2><p>عند إتمام الطلب وأنت مسجّل الدخول، سيظهر في هذا الحساب.</p><Link href="/shop" className="button primary">اذهب إلى المنتجات</Link></div>}<p className="small-note">للطلبات السابقة دون حساب: <Link href="/orders">روابط الطلبات المحفوظة على هذا الجهاز</Link>. لا ننسب طلبًا قديمًا لحسابك اعتمادًا على الاسم أو الهاتف وحدهما.</p></main>;
}
