"use client";
import {useCallback,useEffect,useState} from "react";
export type AccountType="manager"|"staff"|"customer"|"store";
export interface Session { authenticated:boolean;username?:string;account_type?:AccountType;dashboard?:string;store?:{id:number;name:string;status:string} }
export const accountNames:Record<AccountType,string>={manager:"المدير",staff:"الموظف",customer:"الزبون",store:"المتجر / الصيدلية"};
export const storeStatuses:Record<string,string>={PENDING:"بانتظار موافقة المدير",APPROVED:"مفعّل",SUSPENDED:"موقوف"};
export async function csrfHeaders():Promise<Record<string,string>> {
 const read=()=>document.cookie.split(";").map(s=>s.trim()).find(s=>s.startsWith("csrftoken="))?.slice(10)||"";
 let token=read();
 if(!token){await fetch("/api/v1/auth/session/",{credentials:"same-origin",cache:"no-store",signal:AbortSignal.timeout(15000)});token=read();}
 return {"Content-Type":"application/json","X-CSRFToken":decodeURIComponent(token)};
}
export class AccountRequestError extends Error {
 constructor(message:string,public status:number,public code?:string,public currentTotal?:number){super(message);this.name="AccountRequestError";}
}
export async function accountRequest<T>(path:string,body?:unknown):Promise<T>{
 const response=await fetch(`/api/v1/${path}`,{method:body===undefined?"GET":"POST",credentials:"same-origin",cache:"no-store",signal:AbortSignal.timeout(25000),headers:body===undefined?undefined:await csrfHeaders(),body:body===undefined?undefined:JSON.stringify(body)});
 let data:Record<string,unknown>;
 try{data=await response.json();}catch{throw new Error("تعذر قراءة رد الخادم. تحقق من الاتصال ثم حاول مجددًا.");}
 if(!response.ok){
  if(typeof data.detail==="string")throw new AccountRequestError(data.detail,response.status,typeof data.code==="string"?data.code:undefined,typeof data.current_total_syp==="number"?data.current_total_syp:undefined);
  const messages=Object.values(data).flatMap(value=>Array.isArray(value)?value:typeof value==="string"?[value]:[]).join(" · ");
  throw new AccountRequestError(messages||"تعذر تنفيذ العملية. راجع البيانات والصلاحيات.",response.status,typeof data.code==="string"?data.code:undefined);
 }
 return data as T;
}
export function useAccount(expected:AccountType){
 const [session,setSession]=useState<Session|null>(null),[error,setError]=useState("");
 const reload=useCallback(async()=>{setError("");try{
  const s=await accountRequest<Session>("auth/session/");
  if(!s.authenticated){window.location.replace(`/sign-in?type=${expected}`);return;}
  if(s.account_type!==expected){window.location.replace(s.dashboard||"/sign-in");return;}
  setSession(s);
 }catch(e){setError(e instanceof Error?e.message:"تعذر تحميل الحساب.");}},[expected]);
 useEffect(()=>{void reload();},[reload]);
 return {session,error,reload};
}
export async function signOut(){await accountRequest("auth/logout/",{});window.location.assign("/");}
