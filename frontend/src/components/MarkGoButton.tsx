"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { GoNoGoRecommendation } from "@/types/rfp";

interface MarkGoButtonProps {
  rfpId: string;
  current: GoNoGoRecommendation;
}

export function MarkGoButton({ rfpId, current }: MarkGoButtonProps) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const isGo = current === "go";

  async function handleMarkGo() {
    setLoading(true);
    try {
      const res = await fetch(`/api/rfps/${rfpId}/go`, { method: "POST" });
      if (res.ok) {
        router.refresh();
      }
    } finally {
      setLoading(false);
    }
  }

  if (isGo) return null;

  return (
    <button
      type="button"
      onClick={handleMarkGo}
      disabled={loading}
      className="zo-btn disabled:opacity-60"
    >
      {loading ? "Saving…" : "Mark as Go"}
    </button>
  );
}
