"use client";

import { useState } from "react";
import { UploadKnowledgeDocModal } from "./UploadKnowledgeDocModal";
import { kbBtnPrimary } from "@/lib/kb-brand";

interface UploadKnowledgeDocButtonProps {
  onUploaded?: () => void;
  className?: string;
  variant?: "brand" | "primary";
}

export function UploadKnowledgeDocButton({
  onUploaded,
  className = "",
  variant = "brand",
}: UploadKnowledgeDocButtonProps) {
  const [open, setOpen] = useState(false);
  const buttonClass =
    variant === "brand"
      ? kbBtnPrimary
      : "zo-btn !py-2.5";

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={`${buttonClass} ${className}`}
      >
        + Upload document
      </button>
      <UploadKnowledgeDocModal
        open={open}
        onClose={() => setOpen(false)}
        onSuccess={onUploaded}
      />
    </>
  );
}
