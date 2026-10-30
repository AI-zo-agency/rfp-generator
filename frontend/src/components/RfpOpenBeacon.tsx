"use client";

import { useEffect } from "react";
import { shouldSendOpen } from "@/lib/rfp-open";
import { recordRfpOpen } from "@/lib/user-activity-api";

const lastSent: Record<string, number> = {};

/** Reports that the signed-in person opened this RFP. Renders nothing. */
export function RfpOpenBeacon({ rfpId }: { rfpId: string }) {
  useEffect(() => {
    const now = Date.now();
    if (!rfpId || !shouldSendOpen(rfpId, lastSent, now)) return;
    lastSent[rfpId] = now;
    void recordRfpOpen(rfpId);
  }, [rfpId]);

  return null;
}
