import type { NextConfig } from "next";

// Proxy /api/* to the FastAPI backend so the browser talks to the SBR API same-origin (no CORS).
// Locally this targets the dev API; in deployment set API_PROXY_TARGET to the backend URL.
const API_PROXY_TARGET = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // three ships ESM that some bundler paths choke on; transpiling it keeps R3F builds reliable.
  transpilePackages: ["three"],
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_PROXY_TARGET}/:path*` }];
  },
  async headers() {
    // The credentialed C2PA sample is a static, immutable asset (read by c2pa-web and next/image),
    // so cache it hard instead of revalidating on every visit.
    return [
      {
        source: "/credentialed-sample.jpg",
        headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }],
      },
    ];
  },
};

export default nextConfig;
