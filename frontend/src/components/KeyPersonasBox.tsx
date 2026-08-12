"use client";

import { useEffect, useState } from "react";
import { KeyPersonasModal } from "./KeyPersonasModal";
import type { ProposalOutline } from "@/types/proposal";

interface KeyPersonasBoxProps {
  rfpId?: string;
  initialSelectedIds?: string[];
  onSelectionChange?: (selectedPersonaIds: string[]) => void;
  onDraftSynced?: (draft: ProposalOutline) => void;
  className?: string;
}

export function KeyPersonasBox({
  rfpId,
  initialSelectedIds = [],
  onSelectionChange,
  onDraftSynced,
  className = "",
}: KeyPersonasBoxProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>(initialSelectedIds);

  // Keep badge in sync with parent — including Clear / Reset to [].
  useEffect(() => {
    if (isOpen) return;
    setSelectedIds(initialSelectedIds || []);
  }, [initialSelectedIds, isOpen]);

  const handleSelectionChange = (ids: string[]) => {
    setSelectedIds(ids);
    onSelectionChange?.(ids);
  };

  const count = selectedIds.length;

  return (
    <div className={`inline-flex items-center gap-2 ${className}`}>
      <button
        type="button"
        onClick={() => setIsOpen(true)}
        className="zo-btn secondary flex items-center gap-2 px-3.5 py-2 text-xs font-semibold shadow-xs transition-smooth hover:border-[#ef5018] hover:text-[#ef5018]"
        title={
          count === 0
            ? "No key personas selected yet — pick them when you Generate Proposal"
            : `${count} key persona${count === 1 ? "" : "s"} selected for this proposal`
        }
      >
        <span className="flex h-5 w-5 items-center justify-center rounded-md bg-[#ef5018]/12 text-[#ef5018]">
          <svg
            className="h-3.5 w-3.5"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"
            />
          </svg>
        </span>
        <span>Key Personas</span>
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
            count === 0
              ? "bg-[#e5e7eb] text-[#6b7280]"
              : "bg-[#ef5018] text-white"
          }`}
        >
          {count}
        </span>
      </button>

      <KeyPersonasModal
        isOpen={isOpen}
        onClose={() => setIsOpen(false)}
        rfpId={rfpId}
        initialSelectedIds={selectedIds}
        onSelectionChange={handleSelectionChange}
        onDraftSynced={onDraftSynced}
      />
    </div>
  );
}
