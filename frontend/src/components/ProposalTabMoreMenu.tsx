"use client";

import { useEffect, useRef, useState } from "react";

export interface ProposalTabMoreMenuItem {
  id: string;
  label: string;
  title?: string;
  disabled?: boolean;
  tone?: "default" | "danger";
  onClick: () => void;
}

export function ProposalTabMoreMenu({
  items,
  disabled,
}: {
  items: ProposalTabMoreMenuItem[];
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        className="proposal-editor-action"
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
          <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 12a.75.75 0 11-1.5 0 .75.75 0 011.5 0zm6 0a.75.75 0 11-1.5 0 .75.75 0 011.5 0zm6 0a.75.75 0 11-1.5 0 .75.75 0 011.5 0z" />
        </svg>
        More
      </button>
      {open ? (
        <div
          className="absolute right-0 top-[calc(100%+0.35rem)] z-40 min-w-[13.5rem] rounded-lg border border-zo-border/80 bg-white p-1 shadow-lg"
          role="menu"
        >
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              role="menuitem"
              className={`block w-full rounded-md px-2.5 py-2 text-left text-xs font-medium leading-snug hover:bg-black/[0.04] disabled:cursor-not-allowed disabled:opacity-50 ${
                item.tone === "danger" ? "text-red-700" : "text-foreground"
              }`}
              disabled={item.disabled}
              title={item.title}
              onClick={() => {
                setOpen(false);
                item.onClick();
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
