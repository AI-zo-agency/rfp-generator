"use client";

import { useQbInsights, type QbInsights } from "./use-qb-insights";

/** Agency weekly intelligence — signals arrive on the GET response only. */
export function useAgencyInsights(): QbInsights {
  return useQbInsights([], "agency");
}

export type { InsightsData, NoteCard, QbInsights } from "./use-qb-insights";
