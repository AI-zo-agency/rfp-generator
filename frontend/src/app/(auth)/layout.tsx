import { preload } from "react-dom";
import { AuthGate } from "@/components/AuthGate";

const AUTH_GRID = "/auth/zo_Grid.webp";
const AUTH_SKATE = "/auth/skateboard-bg.webp";

export default function AuthLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  preload(AUTH_GRID, { as: "image", fetchPriority: "high" });
  preload(AUTH_SKATE, { as: "image", fetchPriority: "high" });

  return <AuthGate>{children}</AuthGate>;
}
