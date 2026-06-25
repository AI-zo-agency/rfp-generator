"use client";

import { useState } from "react";
import { UploadKnowledgeDocModal } from "./UploadKnowledgeDocModal";

interface UploadKnowledgeDocButtonProps {
  onUploaded?: () => void;
  className?: string;
}

export function UploadKnowledgeDocButton({
  onUploaded,
  className = "",
}: UploadKnowledgeDocButtonProps) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={`zo-btn !py-2.5 ${className}`}
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
