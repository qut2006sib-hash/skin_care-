"use client";
import {useEffect} from "react";
export default function Shell() {
 useEffect(()=>{if("serviceWorker" in navigator && (location.protocol==="https:"||location.hostname==="localhost"||location.hostname==="127.0.0.1")){navigator.serviceWorker.register("/sw.js").catch(()=>{});}},[]);
 return null;
}
