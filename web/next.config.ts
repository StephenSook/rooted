import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // three ships ESM that some bundler paths choke on; transpiling it keeps R3F builds reliable.
  transpilePackages: ["three"],
};

export default nextConfig;
