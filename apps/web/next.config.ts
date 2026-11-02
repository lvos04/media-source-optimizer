// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/next.config.ts — internally versioned copy, see PROVENANCE.md
import type { NextConfig } from "next";

// Production traffic is proxied by server.mjs (streaming-safe, WebSocket-aware). These rewrites make
// `next dev` behave the same way. No secrets ever reach the browser bundle.
const core = process.env.MSO_CORE_URL ?? "http://localhost:8200";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "same-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  // Docker image: .next/standalone (traced node_modules only) + server.mjs as the entrypoint
  output: "standalone",
  outputFileTracingRoot: __dirname,
  poweredByHeader: false,
  compress: false, // never buffer SSE through compression
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  async rewrites() {
    return [
      { source: "/api/v1/:path*", destination: `${core}/api/v1/:path*` },
      { source: "/newznab/:path*", destination: `${core}/newznab/:path*` },
      { source: "/torznab/:path*", destination: `${core}/torznab/:path*` },
    ];
  },
};

export default nextConfig;
