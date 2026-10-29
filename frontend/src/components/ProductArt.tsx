export default function ProductArt({category="serums",brand="العناية",large=false}:{category?:string;brand?:string;large?:boolean}) {
 const tube=category==="cleansers"||category==="moisturizers";
 const hair=category==="hair-care";
 return <div className={`product-art ${large?"large-art":""} art-${category}`} role="img" aria-label="رسم توضيحي للعبوة، ليس صورة المنتج الفعلي">
  <svg viewBox="0 0 240 230" aria-hidden="true">
   <ellipse cx="121" cy="204" rx="65" ry="9" fill="#423051" opacity=".06"/>
   {tube?<><path d="M76 35H164L154 185H86Z" fill="var(--bottle)"/><path d="M76 35H164V44H76Z" fill="#fff" opacity=".5"/><rect x="86" y="185" width="68" height="18" rx="4" fill="#5c4c66"/><path d="M81 52H159" stroke="#fff" opacity=".5"/></>:
   hair?<><rect x="84" y="19" width="72" height="20" rx="5" fill="#59495f"/><rect x="78" y="38" width="84" height="164" rx="21" fill="var(--bottle)"/><path d="M91 49C84 60 86 170 86 177" stroke="#fff" strokeWidth="4" opacity=".5"/></>:
   <><rect x="101" y="16" width="38" height="35" rx="7" fill="#5c4c66"/><path d="M107 16V9Q120 0 133 9V16" fill="#5c4c66"/><rect x="100" y="51" width="40" height="15" rx="2" fill="#c4b3ca"/><rect x="78" y="65" width="84" height="137" rx="16" fill="var(--bottle)"/><path d="M88 77V181" stroke="#fff" strokeWidth="4" opacity=".45"/></>}
   <rect x="85" y="91" width="70" height="71" rx="2" fill="#fff" opacity=".86"/>
   <text x="120" y="113" textAnchor="middle" fill="#584765" fontSize="10" fontFamily="Arial">{brand.length>14?"CARE":brand}</text>
   <path d="M111 133Q120 121 129 130Q127 144 116 141" fill="none" stroke="#887198" strokeWidth="1.3"/>
   <path d="M104 151H136" stroke="#c7baca"/><path d="M110 155H130" stroke="#d8cfda"/>
  </svg>
  <span className="art-label">رسم توضيحي</span>
 </div>;
}
