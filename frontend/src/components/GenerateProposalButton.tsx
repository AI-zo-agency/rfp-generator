"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { trackClick } from "@/lib/zo-analytics";

interface GenerateProposalButtonProps {
  rfpId: string;
  /** Already marked Go — nothing to record, just open the proposal. */
  isGo: boolean;
  /** Server-seeded: at least one drafted section exists for this RFP. */
  hasProposal: boolean;
  /** Blocks the button; `disabledReason` is shown as its tooltip. */
  disabled?: boolean;
  disabledReason?: string;
}

/** Marks the RFP as Go when it isn't yet, then opens its proposal workspace,
 * where the draft is generated (bid scope and cost checks live there). */
export function GenerateProposalButton({
  rfpId,
  isGo,
  hasProposal,
  disabled = false,
  disabledReason,
}: GenerateProposalButtonProps) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleGenerate() {
    setLoading(true);
    setError(null);
    trackClick(hasProposal ? "rfp.view_proposal" : "rfp.generate_proposal", {
      path: `/rfps/${rfpId}`,
      entity_type: "rfp",
      entity_id: rfpId,
    });
    try {
      if (!isGo) {
        const res = await fetch(`/api/rfps/${rfpId}/go`, { method: "POST" });
        if (!res.ok) {
          setError("Could not mark this RFP as Go. Try again.");
          return;
        }
      }
      router.push(`/proposals?rfp=${encodeURIComponent(rfpId)}`);
    } catch {
      setError("Could not reach the server. Try again.");
    } finally {
      setLoading(false);
    }
  }

  const label = hasProposal ? "View proposal" : "Generate proposal";

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={handleGenerate}
        disabled={loading || disabled}
        className="zo-btn disabled:cursor-not-allowed disabled:opacity-60"
        title={
          disabled && disabledReason
            ? disabledReason
            : hasProposal
              ? "Open this RFP's proposal"
              : isGo
                ? "Open this RFP's proposal"
                : "Mark this RFP as Go and open its proposal"
        }
      >
        {loading ? "Opening…" : label}
      </button>
      {error && <p className="text-xs text-zo-error">{error}</p>}
    </div>
  );
}
