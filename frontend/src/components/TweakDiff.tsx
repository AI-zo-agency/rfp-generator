"use client";

import { useMemo } from "react";
import { wordDiff } from "@/lib/rfp-ledger";

/** Word-level before/after view: deletions struck through, additions highlighted. */
export function TweakDiff({ before, after }: { before: string; after: string }) {
  const segments = useMemo(() => wordDiff(before, after), [before, after]);
  if (segments.length === 0) {
    return <p className="text-xs text-zo-text-muted">No text on either side.</p>;
  }
  return (
    <div className="max-h-80 overflow-y-auto whitespace-pre-wrap rounded-lg border border-black/10 bg-white p-3 text-[13px] leading-relaxed text-foreground">
      {segments.map((segment, index) =>
        segment.type === "same" ? (
          <span key={index}>{segment.text}</span>
        ) : segment.type === "del" ? (
          <del key={index} className="bg-red-100 text-red-800 decoration-red-400">
            {segment.text}
          </del>
        ) : (
          <ins key={index} className="bg-green-100 text-green-900 no-underline">
            {segment.text}
          </ins>
        ),
      )}
    </div>
  );
}
