import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The FastAPI backend is a separate service; the browser calls it directly
  // using NEXT_PUBLIC_API_URL. No rewrites needed.
};

export default nextConfig;
