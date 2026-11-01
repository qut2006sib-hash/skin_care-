"use client";
import Link from "next/link";
import {useState} from "react";
import Brand from "@/components/Brand";
import {Session,accountNames,signOut} from "@/lib/accounts";
export default function AccountHeader({session}:{session:Session}){
 const [busy,setBusy]=useState(false),[error,setError]=useState("");
 return <><header className="subpage-header account-header"><Brand/><div className="account-header-links"><span>{session.username} · {session.account_type&&accountNames[session.account_type]}</span><Link href="/shop">المتجر الإلكتروني</Link><button className="button outline" disabled={busy} onClick={async()=>{setBusy(true);setError("");try{await signOut();}catch(e){setError(e instanceof Error?e.message:"تعذر تسجيل الخروج.");setBusy(false);}}}>{busy?"جارٍ الخروج…":"تسجيل الخروج"}</button></div></header>{error&&<div className="message error" role="alert">{error}</div>}</>;
}
