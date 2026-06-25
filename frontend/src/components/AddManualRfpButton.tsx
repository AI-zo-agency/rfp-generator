"use client";

import { useState } from "react";
import { AddManualRfpModal } from "./AddManualRfpModal";

interface AddManualRfpButtonProps {
  variant?: "hero" | "header";
  className?: string;
}

export function AddManualRfpButton({
  variant = "hero",
  className = "",
}: AddManualRfpButtonProps) {
  const [open, setOpen] = useState(false);

  const label = variant === "hero" ? "+ Add New RFP" : "+ Add RFP";

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={
          variant === "hero"
            ? `zo-btn ${className}`
            : `rounded-xl border border-zo-border px-4 py-2.5 text-sm font-semibold text-foreground transition-smooth hover:border-zo-orange hover:text-zo-orange ${className}`
        }
      >
        {label}
      </button>
      <AddManualRfpModal open={open} onClose={() => setOpen(false)} />
    </>
  );
}
