import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Emits .next/standalone with a minimal server bundle — the Docker image
  // then needs no node_modules tree. Harmless for `next dev` / `next start`.
  output: "standalone",
  // The FastAPI backend is a separate service; the browser calls it directly
  // using NEXT_PUBLIC_API_URL. No rewrites needed.
};

export default nextConfig;
