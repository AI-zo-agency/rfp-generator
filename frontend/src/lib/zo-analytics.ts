/**
 * First-party product analytics tracker (pageviews, clicks, dwell).
 * Batches to POST /api/v1/analytics/ingest. Allowlisted features only.
 */

import { getAuthUserEmail, withAuthUserEmail } from "@/lib/auth-user-email";

export type AnalyticsWorkspace = "rfp" | "financial" | "leads";

const BACKEND =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  process.env.BACKEND_URL ||
  "http://localhost:8001";

const HEARTBEAT_MS = 15_000;
const FLUSH_MS = 8_000;
const ENGAGED_WINDOW_MS = 60_000;

type QueuedEvent = {
  event_type: "page_view" | "tab_view" | "ui_click" | "heartbeat" | "funnel_step";
  path?: string;
  tab?: string;
  view?: string;
  feature?: string;
  entity_type?: string;
  entity_id?: string;
  duration_ms?: number;
  engaged?: boolean;
  metadata?: Record<string, unknown>;
  session_id: string;
  actor_email?: string;
  client_ts: string;
};

let workspace: AnalyticsWorkspace | null = null;
let sessionId = "";
let queue: QueuedEvent[] = [];
let lastEngageAt = 0;
let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
let flushTimer: ReturnType<typeof setInterval> | null = null;
let currentPath = "";
let currentTab = "";
let currentView = "";
let started = false;

function ensureSession(): string {
  if (typeof window === "undefined") return "";
  if (sessionId) return sessionId;
  try {
    const key = "zo_analytics_session";
    let id = sessionStorage.getItem(key);
    if (!id) {
      id = crypto.randomUUID();
      sessionStorage.setItem(key, id);
    }
    sessionId = id;
  } catch {
    sessionId = crypto.randomUUID();
  }
  return sessionId;
}

function markEngaged() {
  lastEngageAt = Date.now();
}

function push(partial: Omit<QueuedEvent, "session_id" | "client_ts" | "actor_email">) {
  if (!workspace || typeof window === "undefined") return;
  queue.push({
    ...partial,
    session_id: ensureSession(),
    actor_email: getAuthUserEmail() || undefined,
    client_ts: new Date().toISOString(),
  });
  if (queue.length >= 20) void flush();
}

async function flush() {
  if (!workspace || queue.length === 0) return;
  const batch = queue.splice(0, 50);
  try {
    await fetch(`${BACKEND}/api/v1/analytics/ingest`, {
      method: "POST",
      headers: withAuthUserEmail({
        Accept: "application/json",
        "Content-Type": "application/json",
      }),
      body: JSON.stringify({
        workspace,
        actor_email: getAuthUserEmail() || undefined,
        events: batch,
      }),
      keepalive: true,
    });
  } catch {
    // drop on failure — analytics must not break UX
  }
}

function onHeartbeat() {
  if (typeof document !== "undefined" && document.visibilityState !== "visible") {
    return;
  }
  const engaged = Date.now() - lastEngageAt < ENGAGED_WINDOW_MS;
  push({
    event_type: "heartbeat",
    path: currentPath || undefined,
    tab: currentTab || undefined,
    view: currentView || undefined,
    duration_ms: HEARTBEAT_MS,
    engaged,
  });
}

function bindEngageListeners() {
  if (typeof window === "undefined") return;
  const handler = () => markEngaged();
  window.addEventListener("pointerdown", handler, { passive: true });
  window.addEventListener("keydown", handler, { passive: true });
  window.addEventListener("scroll", handler, { passive: true });
}

/** Start (or switch) analytics for a workspace shell. */
export function startWorkspaceAnalytics(
  ws: AnalyticsWorkspace,
  opts?: { path?: string; tab?: string; view?: string },
) {
  if (typeof window === "undefined") return;
  workspace = ws;
  ensureSession();
  currentPath = opts?.path || window.location.pathname;
  currentTab = opts?.tab || "";
  currentView = opts?.view || "";
  markEngaged();

  if (!started) {
    started = true;
    bindEngageListeners();
    heartbeatTimer = setInterval(onHeartbeat, HEARTBEAT_MS);
    flushTimer = setInterval(() => void flush(), FLUSH_MS);
    window.addEventListener("pagehide", () => {
      void flush();
    });
  }

  trackPageView(currentPath, { tab: currentTab, view: currentView });
}

export function trackPageView(
  path: string,
  opts?: { tab?: string; view?: string },
) {
  currentPath = path;
  if (opts?.tab !== undefined) currentTab = opts.tab;
  if (opts?.view !== undefined) currentView = opts.view;
  push({
    event_type: "page_view",
    path,
    tab: currentTab || undefined,
    view: currentView || undefined,
  });
}

export function trackTabView(tab: string, opts?: { path?: string; view?: string }) {
  currentTab = tab;
  if (opts?.path) currentPath = opts.path;
  if (opts?.view !== undefined) currentView = opts.view;
  push({
    event_type: "tab_view",
    path: currentPath || undefined,
    tab,
    view: currentView || undefined,
  });
}

export function trackClick(
  feature: string,
  opts?: {
    path?: string;
    tab?: string;
    view?: string;
    entity_type?: string;
    entity_id?: string;
    funnel?: boolean;
  },
) {
  markEngaged();
  push({
    event_type: opts?.funnel ? "funnel_step" : "ui_click",
    feature,
    path: opts?.path || currentPath || undefined,
    tab: opts?.tab || currentTab || undefined,
    view: opts?.view || currentView || undefined,
    entity_type: opts?.entity_type,
    entity_id: opts?.entity_id,
  });
  // Also emit funnel_step for known funnel features when marked
  if (opts?.funnel) {
    /* already funnel_step */
  }
}

export function setAnalyticsContext(opts: {
  path?: string;
  tab?: string;
  view?: string;
}) {
  if (opts.path !== undefined) currentPath = opts.path;
  if (opts.tab !== undefined) currentTab = opts.tab;
  if (opts.view !== undefined) currentView = opts.view;
}
