"use client";

import { useEffect, useId, useRef, useState } from "react";
import { CapabilityHoverTip } from "./CapabilityHoverTip";
import { capabilityById } from "@/lib/proposal-tool-guide";

type MatchRfpPacketControlProps = {
  disabled?: boolean;
  isOrdering?: boolean;
  isPlacing?: boolean;
  onOrderTabs: () => void;
  onPlaceContent: () => void;
};

/** Top-toolbar control — sits beside Complete & clean. */
export function MatchRfpPacketControl({
  disabled,
  isOrdering,
  isPlacing,
  onOrderTabs,
  onPlaceContent,
}: MatchRfpPacketControlProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const busy = Boolean(isOrdering || isPlacing);
  const reorder = capabilityById("reorder");
  const place = capabilityById("place");

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const statusLabel = isOrdering
    ? "Reordering…"
    : isPlacing
      ? "Checking…"
      : "Fix outline";

  return (
    <div className="proposal-match-packet is-toolbar" ref={rootRef}>
      <CapabilityHoverTip id="fixOutline" side="bottom">
        <button
          type="button"
          className={`zo-btn secondary proposal-match-packet-toolbar-btn !py-2 !px-3 !text-sm ${
            open ? "is-open" : ""
          } ${busy ? "is-busy" : ""}`}
          disabled={disabled || busy}
          aria-expanded={open}
          aria-controls={menuId}
          aria-haspopup="menu"
          onClick={() => setOpen((v) => !v)}
        >
          {statusLabel}
          <svg
            className="proposal-match-packet-chevron"
            width="12"
            height="12"
            viewBox="0 0 12 12"
            aria-hidden
          >
            <path
              d="M3 4.5 6 7.5 9 4.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </CapabilityHoverTip>
      {open ? (
        <div
          id={menuId}
          className="proposal-match-packet-menu is-toolbar-menu !w-[min(22rem,calc(100vw-2rem))]"
          role="menu"
          aria-label="Fix outline"
        >
          <p className="proposal-match-packet-hint">
            Hover each option for Does / Doesn’t. Pick one job.
          </p>
          <CapabilityHoverTip id="reorder" side="left">
            <button
              type="button"
              role="menuitem"
              className="proposal-match-packet-item"
              disabled={disabled || busy}
              onClick={() => {
                setOpen(false);
                onOrderTabs();
              }}
            >
              <span className="proposal-match-packet-item-title">
                1. {reorder.name}
              </span>
              <span className="proposal-match-packet-item-detail">
                Preview your list vs RFP order, then apply
              </span>
            </button>
          </CapabilityHoverTip>
          <CapabilityHoverTip id="place" side="left">
            <button
              type="button"
              role="menuitem"
              className="proposal-match-packet-item"
              disabled={disabled || busy}
              onClick={() => {
                setOpen(false);
                onPlaceContent();
              }}
            >
              <span className="proposal-match-packet-item-title">
                2. {place.name}
              </span>
              <span className="proposal-match-packet-item-detail">
                Show moves first — you approve
              </span>
            </button>
          </CapabilityHoverTip>
        </div>
      ) : null}
    </div>
  );
}
