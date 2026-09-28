/**
 * First-party product analytics tracker (pageviews, clicks, dwell).
 * Batches to POST /api/v1/analytics/ingest. Allowlisted features only.
 *
 * Does NOT track Activity / Product analytics surfaces (self-referential).
 * Heartbeats pause when the browser tab is hidden (switch away) and resume
 * when visible again; closing the tab flushes the queue via pagehide.
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

/** Paths / tabs / views that are the analytics product itself — never track. */
const EXCLUDED_PATHS = new Set(["/activity"]);
const EXCLUDED_TABS = new Set(["activity"]);
const EXCLUDED_VIEWS = new Set(["activity", "analytics", "audit"]);

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
/** When true, no pageviews / clicks / heartbeats are recorded. */
let paused = false;

export function isAnalyticsExcludedSurface(opts: {
  path?: string | null;
  tab?: string | null;
  view?: string | null;
}): boolean {
  const path = (opts.path || "").split("?")[0].replace(/\/$/, "") || "/";
  if (EXCLUDED_PATHS.has(path)) return true;
  // RFP pipeline /analytics KPI page is product usage — keep it.
  // Only exclude Activity UAT surfaces and in-app Audit/Analytics modes.
  const tab = (opts.tab || "").trim().toLowerCase();
  const view = (opts.view || "").trim().toLowerCase();
  if (tab && EXCLUDED_TABS.has(tab)) return true;
  if (view && EXCLUDED_VIEWS.has(view)) return true;
  return false;
}

function syncPausedFromContext() {
  paused = isAnalyticsExcludedSurface({
    path: currentPath,
    tab: currentTab,
    view: currentView,
  });
}

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
  if (!workspace || typeof window === "undefined" || paused) return;
  if (
    isAnalyticsExcludedSurface({
      path: partial.path ?? currentPath,
      tab: partial.tab ?? currentTab,
      view: partial.view ?? currentView,
    })
  ) {
    return;
  }
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
  if (paused) return;
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
  const handler = () => {
    if (!paused) markEngaged();
  };
  window.addEventListener("pointerdown", handler, { passive: true });
  window.addEventListener("keydown", handler, { passive: true });
  window.addEventListener("scroll", handler, { passive: true });
  document.addEventListener("visibilitychange", () => {
    // When user returns to the tab, mark engaged and resume heartbeats
    // (interval already no-ops while hidden).
    if (document.visibilityState === "visible" && !paused) {
      markEngaged();
    }
  });
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
  syncPausedFromContext();
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

  if (!paused) {
    trackPageView(currentPath, { tab: currentTab, view: currentView });
  }
}

export function trackPageView(
  path: string,
  opts?: { tab?: string; view?: string },
) {
  currentPath = path;
  if (opts?.tab !== undefined) currentTab = opts.tab;
  if (opts?.view !== undefined) currentView = opts.view;
  syncPausedFromContext();
  if (paused) return;
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
  syncPausedFromContext();
  if (paused) return;
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
  // Never record clicks that only open the UAT surfaces themselves.
  if (
    feature === "nav.click" &&
    isAnalyticsExcludedSurface({ path: opts?.path })
  ) {
    return;
  }
  if (paused && !opts?.path) return;
  if (
    isAnalyticsExcludedSurface({
      path: opts?.path ?? currentPath,
      tab: opts?.tab ?? currentTab,
      view: opts?.view ?? currentView,
    })
  ) {
    return;
  }
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
}

export function setAnalyticsContext(opts: {
  path?: string;
  tab?: string;
  view?: string;
}) {
  if (opts.path !== undefined) currentPath = opts.path;
  if (opts.tab !== undefined) currentTab = opts.tab;
  if (opts.view !== undefined) currentView = opts.view;
  syncPausedFromContext();
}
