import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone output keeps the production container minimal: the runtime
  // stage copies .next/standalone instead of node_modules + the full .next.
  output: "standalone",
};

export default nextConfig;
