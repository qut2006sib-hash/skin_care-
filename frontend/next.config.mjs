const backend = (process.env.API_PROXY_TARGET || "http://backend:8000").replace(/\/$/, "");
/** @type {import('next').NextConfig} */
const config = {
  poweredByHeader: false,
  allowedDevOrigins: ["127.0.0.1", ...(process.env.NEXT_DEV_ALLOWED_HOSTS || "").split(",").map(host => host.trim()).filter(Boolean)],
  skipTrailingSlashRedirect: true,
  async rewrites() {
    return [
      { source: "/api/v1/:path*", destination: `${backend}/api/v1/:path*/` },
      { source: "/staff/:path*", destination: `${backend}/staff/:path*/` },
      { source: "/admin/:path*", destination: `${backend}/admin/:path*/` },
      { source: "/static/:path*", destination: `${backend}/static/:path*` },
      { source: "/media/:path*", destination: `${backend}/media/:path*` },
    ];
  },
  async headers() {
    return [
      {source:"/orders/:path*",headers:[{key:"Cache-Control",value:"no-store, private"},{key:"Referrer-Policy",value:"no-referrer"},{key:"X-Robots-Tag",value:"noindex, nofollow"}]},
      {source:"/api/v1/:path*",headers:[{key:"Cache-Control",value:"no-store"}]},
      {source:"/sw.js",headers:[{key:"Cache-Control",value:"no-cache"}]},
    ];
  },
};
export default config;
