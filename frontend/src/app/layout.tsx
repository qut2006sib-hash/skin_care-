import React from "react";

export const metadata = {
  title: "متجر العناية بالبشرة — حمص | تسوق الأصلي مع الدفع عند الاستلام",
  description: "متجر متخصص بمنتجات MEDEE و JUVELAB و FEME Hair في مدينة حمص",
  manifest: "/manifest.json",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ar" dir="rtl">
      <body style={{ margin: 0, fontFamily: "'Noto Sans Arabic', system-ui, sans-serif", backgroundColor: "#FAF7F5", color: "#1C2526" }}>
        {children}
      </body>
    </html>
  );
}
