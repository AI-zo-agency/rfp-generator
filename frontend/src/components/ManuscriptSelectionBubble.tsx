"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

export type SelectionBubbleAnchor = {
  top: number;
  left: number;
  width: number;
  height: number;
};

type ManuscriptSelectionBubbleProps = {
  /** True when there is a usable text selection in the manuscript. */
  active: boolean;
  /** Viewport rect captured at mouseup — required; do not re-read getSelection after React re-render. */
  anchor: SelectionBubbleAnchor | null;
  disabled?: boolean;
  /** Bold/Italic need a mapped markdown range; Change… can work with text-only. */
  formatEnabled?: boolean;
  onBold: () => void;
  onItalic: () => void;
  onAskToChange: () => void;
};

/**
 * Floating tip near the current text selection — format + ask to change content.
 */
export function ManuscriptSelectionBubble({
  active,
  anchor,
  disabled,
  formatEnabled = true,
  onBold,
  onItalic,
  onAskToChange,
}: ManuscriptSelectionBubbleProps) {
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useEffect(() => {
    if (!active || !anchor || typeof window === "undefined") {
      setPos(null);
      return;
    }
    const place = () => {
      setPos({
        top: Math.max(8, anchor.top - 44),
        left: Math.min(
          window.innerWidth - 200,
          Math.max(8, anchor.left + anchor.width / 2 - 90)
        ),
      });
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [active, anchor]);

  if (!active || !pos || typeof document === "undefined") return null;

  return createPortal(
    <div
      role="toolbar"
      aria-label="Selection actions"
      className="fixed z-[400] flex items-center gap-0.5 rounded-lg border border-[rgba(17,24,39,0.12)] bg-white px-1 py-1 shadow-[0_10px_28px_rgba(15,23,42,0.16)]"
      style={{ top: pos.top, left: pos.left }}
      onMouseDown={(e) => e.preventDefault()}
    >
      <button
        type="button"
        disabled={disabled || !formatEnabled}
        className="rounded-md px-2 py-1 text-[11px] font-bold text-[var(--zo-text)] hover:bg-[rgba(17,24,39,0.06)] disabled:opacity-40"
        title="Bold"
        onClick={onBold}
      >
        B
      </button>
      <button
        type="button"
        disabled={disabled || !formatEnabled}
        className="rounded-md px-2 py-1 text-[11px] italic text-[var(--zo-text)] hover:bg-[rgba(17,24,39,0.06)] disabled:opacity-40"
        title="Italic"
        onClick={onItalic}
      >
        I
      </button>
      <span className="mx-0.5 h-4 w-px bg-[rgba(17,24,39,0.12)]" aria-hidden />
      <button
        type="button"
        disabled={disabled}
        className="rounded-md px-2 py-1 text-[11px] font-semibold text-[var(--zo-orange)] hover:bg-[rgba(239,80,24,0.08)] disabled:opacity-40"
        title="Ask the assistant to change this highlighted text"
        onClick={onAskToChange}
      >
        Change…
      </button>
    </div>,
    document.body
  );
}
