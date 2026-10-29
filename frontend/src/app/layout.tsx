import type {Metadata,Viewport} from "next";
import Shell from "@/components/Shell";
import "./globals.css";
export const metadata: Metadata = {
 title: {default:"العناية | متجر حمص",template:"%s | العناية"},
 description:"منتجات العناية بالبشرة والشعر، مع سلة واضحة وتتبع الطلب والتوصيل ضمن الأحياء المفعلة في حمص.",
 manifest:"/manifest.json",
 appleWebApp:{capable:true,statusBarStyle:"default",title:"العناية"},
};
export const viewport:Viewport={width:"device-width",initialScale:1,themeColor:"#654b7a"};
export default function RootLayout({children}:{children:React.ReactNode}) { return <html lang="ar" dir="rtl"><body><Shell/>{children}</body></html>; }
