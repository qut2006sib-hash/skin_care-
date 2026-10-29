import type {Metadata} from "next";
import Tracking from "./tracking";
export const dynamic="force-dynamic";
export const metadata:Metadata={title:"تتبّع الطلب",robots:{index:false,follow:false},referrer:"no-referrer"};
export default async function TrackingPage({params}:{params:Promise<{token:string}>}){const {token}=await params;return <Tracking token={token}/>;}
