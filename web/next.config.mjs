/** @type {import('next').NextConfig} */

// The backend (leadcentre/api.py) sends no CORS headers — ИЗМЕРЕНО 2026-09-09:
//   curl -D- -H "Origin: http://localhost:3100" http://127.0.0.1:8000/leads → no
//   access-control-* headers, so a browser on another origin gets "Failed to fetch".
// Rather than ask another module to change, the dashboard proxies: the browser always
// calls its own origin at /api/backend/*, and Next forwards to NEXT_PUBLIC_API_URL.
// One variable still switches mock ↔ live, exactly as specified.
const target = (process.env.NEXT_PUBLIC_API_URL ?? "").trim().replace(/\/$/, "");

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    if (!target) return [];
    return [{ source: "/api/backend/:path*", destination: `${target}/:path*` }];
  },
};

export default nextConfig;
