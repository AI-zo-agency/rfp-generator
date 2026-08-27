"use client";

import type { ReactNode } from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  capabilityById,
  type ToolCapabilityId,
} from "@/lib/proposal-tool-guide";

/** Hover card: what this control Does / Doesn’t. */
export function CapabilityHoverTip({
  id,
  children,
  side = "bottom",
}: {
  id: ToolCapabilityId;
  children: ReactNode;
  side?: "top" | "bottom" | "left" | "right";
}) {
  const tip = capabilityById(id);
  return (
    <TooltipProvider delayDuration={350}>
      <Tooltip>
        {/* Span wrapper so disabled buttons still receive hover. */}
        <TooltipTrigger asChild>
          <span className="inline-flex max-w-full">{children}</span>
        </TooltipTrigger>
        <TooltipContent
          side={side}
          sideOffset={8}
          className="z-[220] max-w-[18rem] border border-[rgba(17,24,39,0.12)] bg-white px-3 py-2.5 text-left text-[var(--zo-text)] shadow-[0_8px_24px_rgba(15,23,42,0.14)]"
        >
          <p className="m-0 text-[12px] font-semibold leading-snug">{tip.name}</p>
          <p className="m-0 mt-1.5 text-[11.5px] leading-snug text-[var(--zo-text-secondary)]">
            <span className="font-semibold text-[var(--zo-text)]">Does </span>
            {tip.does}
          </p>
          <p className="m-0 mt-1 text-[11.5px] leading-snug text-[var(--zo-text-secondary)]">
            <span className="font-semibold text-[var(--zo-text)]">Doesn’t </span>
            {tip.doesnt}
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
