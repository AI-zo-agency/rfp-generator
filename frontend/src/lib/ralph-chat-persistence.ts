/**
 * Keep Ask Ralph's conversation and any in-flight improve run across a page
 * refresh (per RFP, per browser tab — sessionStorage like section revisions).
 * The run itself keeps going on the server; the reloaded page polls for it.
 */
import type { SectionChatMessage } from "@/components/ProposalSectionChatPanel";

export interface RalphInflightRun {
  runId: string;
  /** "apply" = Apply the suggested fix; "improve" = a typed / quick-prompt ask. */
  kind: "improve" | "apply";
  sectionId: string;
  sectionTitle: string;
  /** Editor tab open when the ask was sent — drives the "Acted on …" note. */
  viewingSectionId: string | null;
  instruction: string;
  statusLine: string | null;
  startedAt: number;
}

const MAX_STORED_MESSAGES = 60;

function chatKey(rfpId: string): string {
  return `zo-ralph-chat:${rfpId}`;
}

function inflightKey(rfpId: string): string {
  return `zo-ralph-inflight:${rfpId}`;
}

export function newRalphRunId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `run-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function loadRalphChat(rfpId: string): SectionChatMessage[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = sessionStorage.getItem(chatKey(rfpId));
    const parsed = raw ? (JSON.parse(raw) as unknown) : null;
    return Array.isArray(parsed) ? (parsed as SectionChatMessage[]) : [];
  } catch {
    return [];
  }
}

export function persistRalphChat(rfpId: string, messages: SectionChatMessage[]): void {
  if (typeof window === "undefined") return;
  try {
    if (messages.length === 0) {
      sessionStorage.removeItem(chatKey(rfpId));
      return;
    }
    sessionStorage.setItem(
      chatKey(rfpId),
      JSON.stringify(messages.slice(-MAX_STORED_MESSAGES)),
    );
  } catch {
    // ignore quota errors
  }
}

export function loadRalphInflight(rfpId: string): RalphInflightRun | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(inflightKey(rfpId));
    const parsed = raw ? (JSON.parse(raw) as RalphInflightRun) : null;
    return parsed && typeof parsed.runId === "string" && typeof parsed.sectionId === "string"
      ? parsed
      : null;
  } catch {
    return null;
  }
}

export function persistRalphInflight(rfpId: string, run: RalphInflightRun | null): void {
  if (typeof window === "undefined") return;
  try {
    if (run) sessionStorage.setItem(inflightKey(rfpId), JSON.stringify(run));
    else sessionStorage.removeItem(inflightKey(rfpId));
  } catch {
    // ignore quota errors
  }
}

let pageUnloading = false;
if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => {
    pageUnloading = true;
  });
  window.addEventListener("pageshow", () => {
    pageUnloading = false;
  });
}

/** True while the page is being refreshed / navigated away — a request that
 * fails now was cut off by the reload, not by the server, and must not be
 * recorded as a failed turn (the reloaded page picks the run back up). */
export function isPageUnloading(): boolean {
  return pageUnloading;
}
