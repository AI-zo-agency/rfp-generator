"use client";

import { useQbChat, type QbChat } from "./use-qb-chat";

/** Grounded Agency chat against weekly evidence. */
export function useAgencyChat(): QbChat {
  return useQbChat("agency");
}

export type { QbChat } from "./use-qb-chat";
