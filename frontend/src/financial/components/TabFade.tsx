"use client";

import { useRef } from "react";
import { useGSAP } from "@gsap/react";
import { playFinTabEnter } from "../lib/fin-motion";

interface TabFadeProps {
  active: boolean;
  children: React.ReactNode;
  className?: string;
  id?: string;
}

/** GSAP entrance when a tab becomes active — never remounts children. */
export function TabFade({ active, children, className = "", id }: TabFadeProps) {
  const ref = useRef<HTMLDivElement>(null);

  useGSAP(
    () => {
      if (!active || !ref.current) return;
      playFinTabEnter(ref.current);
    },
    { dependencies: [active], scope: ref },
  );

  return (
    <div
      ref={ref}
      id={id}
      role="tabpanel"
      hidden={!active}
      aria-labelledby={id?.replace("financial-panel-", "financial-tab-")}
      className={active ? `flex min-h-0 flex-1 flex-col ${className}` : className}
    >
      {children}
    </div>
  );
}
