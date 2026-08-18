"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Figure, Note } from "./qb-ui";
import { billablePct, buildSignals, daysUntil, type SectionId } from "../lib/teamwork-derive";
import { TeamworkAttention } from "./teamwork/TeamworkAttention";
import { TeamworkProjects } from "./teamwork/TeamworkProjects";
import { TeamworkWork } from "./teamwork/TeamworkWork";
import { TeamworkTime } from "./teamwork/TeamworkTime";
import type { TeamworkOverview } from "../types/teamwork";
import "./QuickBooksLedger.css";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";

function isAbortError(err: unknown) {
  return (
    (err instanceof DOMException && err.name === "AbortError") ||
    (err instanceof Error && err.name === "AbortError")
  );
}

/** "4 hours ago" — the form a reader can act on. The timestamp goes in the title. */
function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const minutes = Math.round((Date.now() - then) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

type SyncTone = "ok" | "warn" | "bad";

function syncState(
  data: TeamworkOverview | null,
  loading: boolean,
  error: string | null,
  notConfigured: boolean,
): { label: string; tone: SyncTone } {
  if (loading) return { label: "Reading Teamwork…", tone: "ok" };
  if (error) return { label: "Unavailable", tone: "bad" };
  if (notConfigured) return { label: "Not connected", tone: "bad" };
  if (data?.sync_status === "backfill_pending") return { label: "Backfill pending", tone: "warn" };
  if (data?.sync_status === "missing") return { label: "Snapshot missing", tone: "warn" };
  if (data?.sync_status === "failed") return { label: "Stale cache", tone: "warn" };
  if (Object.keys(data?.errors || {}).length) return { label: "Partial sync", tone: "warn" };
  return { label: "Synced", tone: "ok" };
}

export function TeamworkPanels() {
  const [data, setData] = useState<TeamworkOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/financials/teamwork/overview`, {
        signal: ac.signal,
      });
      if (!res.ok) throw new Error(`Teamwork overview returned ${res.status}`);
      const payload = (await res.json()) as TeamworkOverview;
      if (ac.signal.aborted) return;
      setData(payload);
    } catch (err) {
      if (isAbortError(err) || ac.signal.aborted) return;
      setError(err instanceof Error ? err.message : "Could not reach Teamwork");
    } finally {
      if (!ac.signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    return () => abortRef.current?.abort();
  }, [load]);

  const errorEntries = Object.entries(data?.errors || {});
  const notConfigured = data && !data.connected && Boolean(data.errors.config || data.errors.auth);
  const todayISO = useMemo(
    () => (data?.as_of || new Date().toISOString()).slice(0, 10),
    [data?.as_of],
  );
  const sync = syncState(data, loading, error, Boolean(notConfigured));
  const syncedAt = data?.synced_at || data?.generated_at || null;
  const atRiskCount = data?.projects.filter((p) => p.health === "bad").length ?? 0;
  const oldestLate = useMemo(() => {
    if (!data) return 0;
    let worst = 0;
    for (const t of data.overdue_tasks) {
      const days = daysUntil(t.due_date, todayISO);
      if (days !== null && days < 0) worst = Math.max(worst, Math.abs(days));
    }
    return worst;
  }, [data, todayISO]);

  const signals = useMemo(
    () => (data ? buildSignals(data, todayISO) : []),
    [data, todayISO],
  );

  const goToSection = useCallback((id: SectionId) => {
    document
      .getElementById(`teamwork-${id}`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  return (
    <TooltipProvider delayDuration={120}>
      <div className="qb-ledger" aria-busy={loading || undefined}>
        <div className="qb-toolbar">
          <p className="qb-sync" data-tone={sync.tone}>
            <span className="qb-sync-dot" data-busy={loading ? "true" : undefined} aria-hidden />
            {sync.label}
            {!loading && syncedAt ? (
              <span className="qb-sync-meta" title={new Date(syncedAt).toLocaleString()}>
                {relativeTime(syncedAt)}
              </span>
            ) : null}
            {!loading && data?.summary ? (
              <span className="qb-sync-meta">
                {data.summary.project_count} active projects
              </span>
            ) : null}
          </p>
          <button type="button" className="qb-retry" onClick={() => void load()} disabled={loading}>
            <RefreshCw size={13} />
            Refresh
          </button>
        </div>

        {loading && !data ? (
          <p className="qb-empty">Loading Teamwork projects…</p>
        ) : null}

        {!loading && (error || !data) ? (
          <div className="qb-error">
            <p>{error ?? "No Teamwork data"}</p>
            <button type="button" className="qb-retry" onClick={() => void load()}>
              Try again
            </button>
          </div>
        ) : null}

        {!loading && data && notConfigured ? (
          <div className="qb-error">
            <p>
              {data.errors.auth ||
                data.errors.config ||
                "Teamwork credentials are not configured on the backend."}
            </p>
            <Note>
              Set TEAMWORK_BASE_URL and TEAMWORK_API_KEY on the FastAPI service. The browser never
              receives the API key.
            </Note>
          </div>
        ) : null}

        {!loading && data && !notConfigured ? (
          <div className="min-h-0 flex-1 overflow-auto" style={{ display: "flex", flexDirection: "column", gap: 18 }}>
            <TeamworkAttention signals={signals} onGo={goToSection} />

            <div className="qb-moneyline">
              <Figure
                label="Active projects"
                size="lg"
                value={data.summary.project_count}
                sub={
                  atRiskCount
                    ? `${atRiskCount} at risk`
                    : "All healthy"
                }
              />
              <Figure
                label="Overdue tasks"
                size="lg"
                value={data.summary.overdue_task_count}
                tone={data.summary.overdue_task_count ? "out" : undefined}
                sub={oldestLate ? `oldest ${oldestLate}d late` : "Nothing overdue"}
              />
              <Figure
                label="Due in 14 days"
                size="lg"
                value={data.summary.upcoming_task_count}
                sub="Tasks with a near due date"
              />
              <Figure
                label="Hours this month"
                size="lg"
                value={`${data.summary.hours_this_month}h`}
                sub={`${billablePct(data.time.billable_minutes, data.time.total_minutes)}% billable`}
              />
            </div>

            {errorEntries.length ? (
              <Note>
                Teamwork sync has issues ({errorEntries.map(([key]) => key).join(", ")}). Showing the
                last cached snapshot from our backend mirror.
              </Note>
            ) : null}

            <div id="teamwork-projects">
              <TeamworkProjects data={data} todayISO={todayISO} />
            </div>

            <div id="teamwork-work" style={{ display: "flex", flexDirection: "column", gap: 18 }}>
              <TeamworkWork data={data} todayISO={todayISO} />
            </div>

            <div id="teamwork-time" style={{ display: "flex", flexDirection: "column", gap: 18 }}>
              <TeamworkTime data={data} />
            </div>
          </div>
        ) : null}
      </div>
    </TooltipProvider>
  );
}
