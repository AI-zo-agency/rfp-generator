"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { ZoAmuletLoader } from "@/components/ZoAmuletLoader";

export default function RootPage() {
  const router = useRouter();

  useEffect(() => {
    const token = localStorage.getItem("auth_token");
    router.replace(token ? "/choose" : "/login");
  }, [router]);

  return <ZoAmuletLoader fullScreen label="Loading ZO Agency" />;
}
