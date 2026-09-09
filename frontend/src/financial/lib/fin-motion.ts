/**
 * Financial dashboard motion kit.
 * Motion = ongoing UI. GSAP = one-shot entrance choreography.
 */

import gsap from "gsap";
import { expoOutEase } from "@/lib/motion";

export const FIN_EASE = "power3.out";
export const FIN_MOTION_EASE = expoOutEase;

export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Bold tab/panel entrance: KPIs → chrome → charts → rows. */
export function playFinTabEnter(root: HTMLElement): gsap.core.Timeline | null {
  if (prefersReducedMotion()) return null;

  const q = gsap.utils.selector(root);
  const kpis = q('[data-fin="kpi"], .qb-figure, .qb-moneyline > *');
  const chrome = q(
    '[data-fin="chrome"], .qb-toolbar, .qb-panel-head, .qb-chips, [role="tablist"], .qb-legend',
  );
  const charts = q('[data-fin="chart"], .qb-chart-swap, .recharts-responsive-container');
  const panels = q('[data-fin="panel"], .qb-panel, .qb-two > *, .qb-aging');
  const rows = q('[data-fin="row"], .qb-table tbody tr, [data-fin-card]');

  const tl = gsap.timeline({ defaults: { ease: FIN_EASE } });

  if (kpis.length) {
    tl.fromTo(
      kpis,
      { opacity: 0, y: 28, scale: 0.94 },
      { opacity: 1, y: 0, scale: 1, duration: 0.55, stagger: 0.07 },
      0,
    );
  }

  if (chrome.length) {
    tl.fromTo(
      chrome,
      { opacity: 0, y: 16 },
      { opacity: 1, y: 0, duration: 0.42, stagger: 0.04 },
      0.12,
    );
  }

  if (panels.length) {
    tl.fromTo(
      panels,
      { opacity: 0, y: 22 },
      { opacity: 1, y: 0, duration: 0.48, stagger: 0.06 },
      0.18,
    );
  }

  if (charts.length) {
    tl.fromTo(
      charts,
      { opacity: 0, y: 24, scale: 0.98 },
      { opacity: 1, y: 0, scale: 1, duration: 0.55, stagger: 0.05 },
      0.28,
    );
  }

  if (rows.length) {
    tl.fromTo(
      rows,
      { opacity: 0, y: 14, x: -6 },
      { opacity: 1, y: 0, x: 0, duration: 0.38, stagger: 0.028 },
      0.32,
    );
  }

  // Fallback: animate direct children if nothing matched
  if (!kpis.length && !chrome.length && !panels.length && !charts.length && !rows.length) {
    const kids = Array.from(root.children) as HTMLElement[];
    if (kids.length) {
      tl.fromTo(
        kids,
        { opacity: 0, y: 18 },
        { opacity: 1, y: 0, duration: 0.45, stagger: 0.06 },
        0,
      );
    }
  }

  return tl;
}

export function finHoverProps() {
  if (prefersReducedMotion()) return {};
  return {
    whileHover: { y: -3, transition: { duration: 0.22, ease: FIN_MOTION_EASE } },
    whileTap: { scale: 0.98 },
  };
}

export const finCardVariants = {
  hidden: { opacity: 0, y: 18, scale: 0.97 },
  show: (i: number) => ({
    opacity: 1,
    y: 0,
    scale: 1,
    transition: {
      delay: prefersReducedMotion() ? 0 : 0.04 + i * 0.05,
      duration: prefersReducedMotion() ? 0 : 0.42,
      ease: FIN_MOTION_EASE,
    },
  }),
};

export const finRowVariants = {
  hidden: { opacity: 0, y: 10, x: -4 },
  show: (i: number) => ({
    opacity: 1,
    y: 0,
    x: 0,
    transition: {
      delay: prefersReducedMotion() ? 0 : Math.min(i, 24) * 0.028,
      duration: prefersReducedMotion() ? 0 : 0.32,
      ease: FIN_MOTION_EASE,
    },
  }),
};
