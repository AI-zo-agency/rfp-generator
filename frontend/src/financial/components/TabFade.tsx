"use client";

import { useEffect, useRef } from "react";

interface TabFadeProps {
  active: boolean;
  children: React.ReactNode;
  className?: string;
}

/** Plays a quick native fade+rise the moment a tab becomes active — never
 * remounts children, so each tab's own filters/expanded rows persist. */
export function TabFade({ active, children, className = "" }: TabFadeProps) {
  const ref = useRef<HTMLDivElement>(null);
  const wasActive = useRef(active);

  useEffect(() => {
    if (active && !wasActive.current && ref.current) {
      ref.current.animate(
        [
          { opacity: 0, transform: "translateY(4px)" },
          { opacity: 1, transform: "translateY(0)" },
        ],
        { duration: 220, easing: "cubic-bezier(0.16, 1, 0.3, 1)" }
      );
    }
    wasActive.current = active;
  }, [active]);

  return (
    <div ref={ref} className={active ? `flex min-h-0 flex-1 flex-col ${className}` : "hidden"}>
      {children}
    </div>
  );
}
