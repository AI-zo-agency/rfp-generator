import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Dev often opens as 127.0.0.1 while `next dev` binds as localhost.
  allowedDevOrigins: ["127.0.0.1"],
};

export default nextConfig;
