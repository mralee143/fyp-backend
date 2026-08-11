import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit a self-contained server bundle at .next/standalone so the Docker image
  // ships only the code it actually reaches instead of all of node_modules.
  // No effect on `next dev`.
  output: "standalone",
};

export default nextConfig;
