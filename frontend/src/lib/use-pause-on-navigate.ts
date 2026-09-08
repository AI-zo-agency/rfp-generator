"use client";

import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";

/**
 * Soft navigations away from a heavy page (e.g. ProposalDraftWorkspace) stay
 * stuck in Next's "Rendering…" indicator when background polls / autosaves keep
 * issuing urgent setState — those interrupt the navigation transition, so the
 * old tree never unmounts and the new route never commits.
 *
 * Pause background work as soon as the user clicks an in-app link that leaves
 * the current path. Resume if navigation never completes (same path after a
 * grace period).
 */
export function usePauseOnNavigate(graceMs = 12_000): {
  readonly current: boolean;
} {
  const pathname = usePathname();
  const pausedRef = useRef(false);
  const resumeTimerRef = useRef<number | null>(null);

  useEffect(() => {
    pausedRef.current = false;
    if (resumeTimerRef.current != null) {
      window.clearTimeout(resumeTimerRef.current);
      resumeTimerRef.current = null;
    }
  }, [pathname]);

  useEffect(() => {
    const clearResumeTimer = () => {
      if (resumeTimerRef.current != null) {
        window.clearTimeout(resumeTimerRef.current);
        resumeTimerRef.current = null;
      }
    };

    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const anchor = target.closest("a[href]");
      if (!anchor) return;
      const href = anchor.getAttribute("href");
      if (!href || href.startsWith("#") || href.startsWith("mailto:")) return;
      if (href.startsWith("http://") || href.startsWith("https://")) {
        try {
          if (new URL(href).origin !== window.location.origin) return;
        } catch {
          return;
        }
      }

      let nextPath = href;
      try {
        nextPath = new URL(href, window.location.origin).pathname;
      } catch {
        return;
      }

      const staying =
        nextPath === pathname || nextPath.startsWith(`${pathname}/`);
      if (staying) return;

      pausedRef.current = true;
      clearResumeTimer();
      resumeTimerRef.current = window.setTimeout(() => {
        const stillHere =
          window.location.pathname === pathname ||
          window.location.pathname.startsWith(`${pathname}/`);
        if (stillHere) pausedRef.current = false;
        resumeTimerRef.current = null;
      }, graceMs);
    };

    document.addEventListener("pointerdown", onPointerDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      clearResumeTimer();
    };
  }, [pathname, graceMs]);

  return pausedRef;
}
