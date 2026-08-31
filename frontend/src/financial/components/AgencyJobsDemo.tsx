"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, ChevronLeft, ChevronRight, RefreshCw, Sparkles } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Empty, Figure, FilterChips, Note, Panel, Pill, usd } from "./qb-ui";
import { AgencyResolutionDrawer, type InvoiceResolutionPayload, type ReceivableFollowUp } from "./AgencyResolutionDrawer";
import { Count, HoursValue } from "./teamwork/kpis";
import { buildAgencyActions, type AgencyAction } from "../lib/agency-action-queue";
import { groupJobsByProject } from "../lib/agency-project-groups";
import type { LinkResult } from "../types/client-map";
import { parseAgencyView, type AgencyJobsViewId, type AgencyViewId } from "../lib/financial-tab";
import type { AgencyOverview, AgencyJoinStatus } from "../types/agency";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";
const ACTIONS_PER_PAGE = 6;
const INVOICES_PER_PAGE = 10;

const AGENCY_JOB_VIEWS: { id: AgencyJobsViewId; label: string }[] = [
  { id: "jobs", label: "Queue" },
  { id: "portfolio", label: "Portfolio" },
  { id: "invoices", label: "Invoices" },
  { id: "orphans", label: "Orphans" },
];
type PortfolioFilter = "all" | "attention" | "late" | "mapping" | "financial";
type QueueFilter = "all" | "delivery" | "mapping" | "ar";
type ProjectGroup = ReturnType<typeof groupJobsByProject>[number];

function joinTone(join: AgencyJoinStatus) {
  return ["confirmed", "job override", "internal"].includes(join) ? "good" as const : ["suggested", "ambiguous"].includes(join) ? "warn" as const : "muted" as const;
}
function moneyCell(value: number | null | undefined) { return value == null ? "—" : usd(value); }
function needsAttention(group: ProjectGroup) { return group.jobs.some((job) => job.status.toLowerCase() === "late" || job.health.toLowerCase() === "bad"); }
export function filterAgencyGroups(groups: ProjectGroup[], filter: PortfolioFilter): ProjectGroup[] {
  if (filter === "all") return groups;
  return groups.filter((group) => filter === "attention" ? needsAttention(group) : filter === "late" ? group.jobs.some((job) => job.status.toLowerCase() === "late") : filter === "mapping" ? group.jobs.some((job) => ["needs mapping", "ambiguous", "suggested"].includes(job.join)) : (group.openAr ?? 0) > 0 || (group.billedYtd ?? 0) > 0);
}
export function formatAgencyFreshness(asOf: string | null | undefined) {
  if (!asOf || Number.isNaN(new Date(asOf).getTime())) return "Freshness unavailable";
  return `Updated ${new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(asOf))}`;
}
export function getAgencyLoadError(message: string, hasCachedData: boolean) { return hasCachedData ? `Refresh failed: ${message}. We’re showing the last loaded data; retry to refresh it.` : `Could not load agency overview: ${message}`; }
export type AgencyLoadResult = { ok: true; data: AgencyOverview } | { ok: false; message: string };
export function applyAgencyLoadResult(previousData: AgencyOverview | null, result: AgencyLoadResult) { return result.ok ? { data: result.data, error: null } : { data: previousData, error: getAgencyLoadError(result.message, previousData !== null) }; }
export function filterAgencyActions(actions: AgencyAction[], filter: QueueFilter): AgencyAction[] {
  if (filter === "all") return actions.filter((action) => action.kind !== "invoice");
  return actions.filter((action) => action.kind === (filter === "ar" ? "receivable" : filter));
}
export function paginate<T>(items: T[], page: number, pageSize: number) {
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
  const safePage = Math.min(Math.max(page, 1), pageCount);
  return { page: safePage, pageCount, items: items.slice((safePage - 1) * pageSize, safePage * pageSize) };
}
export function invoiceRelationshipLabel() { return "No project or job linked"; }
export function getAgencyEmptyMessage(hasOverview: boolean) { return hasOverview ? "No owner actions match this filter." : "No agency overview is available. Retry to load jobs."; }
export function invoiceStatusPresentation(invoice: { status?: string; open_ar: number }) {
  const status = invoice.status?.trim().toLowerCase();
  if (status === "paid") return { label: "paid", tone: "good" as const };
  if (status === "open" || status === "overdue") return { label: status, tone: "warn" as const };
  if (status) return { label: status, tone: "muted" as const };
  return invoice.open_ar > 0 ? { label: "open", tone: "warn" as const } : { label: "paid", tone: "good" as const };
}
export function getInvoiceResolutionOutcome(refreshed: boolean) { return refreshed ? null : "Invoice resolution was saved, but the overview needs a refresh. Retry to confirm the latest data."; }
function actionLabel(action: AgencyAction) { return action.kind === "delivery" ? "Review delivery" : action.kind === "mapping" ? "Resolve mapping" : "Review receivable"; }
function actionStatus(action: AgencyAction) { return action.kind === "delivery" ? "Delivery" : action.kind === "mapping" ? "Mapping" : "Receivable"; }
/** Where the signal originates — delivery/mapping from Teamwork jobs; AR/invoices from QuickBooks. */
export function actionSource(action: Pick<AgencyAction, "kind">) {
  return action.kind === "delivery" || action.kind === "mapping" ? "Teamwork" : "QuickBooks";
}

function Pagination({ page, pageCount, onChange, label }: { page: number; pageCount: number; onChange: (page: number) => void; label: string }) {
  if (pageCount <= 1) return null;
  return <nav className="agency-pagination" aria-label={label}><span>Page {page} of {pageCount}</span><div><button type="button" onClick={() => onChange(page - 1)} disabled={page === 1} aria-label="Previous page"><ChevronLeft size={15} /></button><button type="button" onClick={() => onChange(page + 1)} disabled={page === pageCount} aria-label="Next page"><ChevronRight size={15} /></button></div></nav>;
}

export function useAgencyOverview() {
  const [data, setData] = useState<AgencyOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const cachedData = useRef<AgencyOverview | null>(null);
  const load = useCallback(async (): Promise<boolean> => {
    setLoading(true); setError(null);
    try {
      const response = await fetch(`${API_BASE}/api/v1/financials/agency/overview?year=${new Date().getFullYear()}`, { credentials: "include" });
      if (!response.ok) throw new Error(`Agency overview returned ${response.status}`);
      const overview = (await response.json()) as AgencyOverview;
      cachedData.current = overview; setData(overview); return true;
    } catch (caught) {
      const next = applyAgencyLoadResult(cachedData.current, { ok: false, message: caught instanceof Error ? caught.message : "Could not load agency overview" });
      setData(next.data); setError(next.error); return false;
    } finally { setLoading(false); }
  }, []);
  // The overview is an external data source; this intentionally runs once on mount.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  return { data, loading, error, setError, load };
}

export type AgencyMappingToolbarState = {
  busy: boolean;
  loading: boolean;
  rowCount: number;
  lastLinkResult: LinkResult | null;
  onImport: () => void;
  onFindLinks: () => void;
  onRefresh: () => void;
};

export function AgencyJobsToolbar({
  data,
  loading,
  error,
  onRefresh,
  view = "jobs",
  onViewChange,
  mapping,
  onOpenAi,
  aiHighImpact = 0,
  aiOpen = false,
}: {
  data: AgencyOverview | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  view?: AgencyViewId;
  onViewChange?: (view: AgencyViewId) => void;
  mapping?: AgencyMappingToolbarState;
  onOpenAi?: () => void;
  aiHighImpact?: number;
  aiOpen?: boolean;
}) {
  if (view === "mapping" && mapping) {
    return (
      <div className="qb-toolbar">
        <div className="qb-toolbar-nav">
          <button type="button" className="qb-back" onClick={() => onViewChange?.("jobs")}>
            <ChevronLeft size={16} strokeWidth={2.25} aria-hidden />
            Agency
          </button>
          <p className="qb-toolbar-title">
            Client mapping
            <span className="qb-toolbar-meta">{mapping.rowCount} clients</span>
            {mapping.lastLinkResult ? (
              <span className="qb-toolbar-meta">
                {mapping.lastLinkResult.confirmed} confirmed
                {typeof mapping.lastLinkResult.teamwork_tag === "number"
                  ? ` · ${mapping.lastLinkResult.teamwork_tag} tag`
                  : ""}
                {" · "}
                {mapping.lastLinkResult.suggested} suggested
              </span>
            ) : null}
          </p>
        </div>
        <div className="qb-toolbar-actions">
          <button type="button" className="qb-retry" disabled={mapping.busy} onClick={mapping.onImport}>
            Import from Tags
          </button>
          <button type="button" className="qb-retry" disabled={mapping.busy} onClick={mapping.onFindLinks}>
            Find links
          </button>
          <button
            type="button"
            className="qb-retry"
            disabled={mapping.loading || mapping.busy}
            onClick={mapping.onRefresh}
          >
            <RefreshCw className={mapping.loading ? "animate-spin" : undefined} size={13} strokeWidth={2.25} aria-hidden />
            Refresh
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="qb-toolbar">
      <p className="qb-sync" data-tone={error ? "bad" : undefined}>
        <span className="qb-sync-dot" data-busy={loading ? "true" : undefined} aria-hidden />
        {formatAgencyFreshness(data?.as_of)}
        {data?.position ? <span className="qb-sync-meta">{data.position.live_jobs} live jobs</span> : null}
      </p>
      <div className="qb-toolbar-actions">
        {onOpenAi ? (
          <button
            type="button"
            className="qb-ai-trigger"
            onClick={onOpenAi}
            aria-haspopup="dialog"
            aria-expanded={aiOpen}
          >
            <Sparkles size={14} strokeWidth={2.25} aria-hidden />
            AI Intelligence
            {aiHighImpact ? (
              <span className="qb-ai-trigger-count">{aiHighImpact}</span>
            ) : null}
          </button>
        ) : null}
        {onViewChange ? (
          <button
            type="button"
            className="qb-retry"
            data-active={view === "mapping" ? "true" : undefined}
            aria-pressed={view === "mapping"}
            onClick={() => onViewChange(view === "mapping" ? "jobs" : "mapping")}
          >
            Mapping
          </button>
        ) : null}
        <button type="button" className="qb-retry" disabled={loading} onClick={onRefresh}>
          <RefreshCw className={loading ? "animate-spin" : undefined} size={13} strokeWidth={2.25} aria-hidden />
          Refresh
        </button>
      </div>
    </div>
  );
}

export function AgencyJobsDemo({
  overview,
  view,
  onViewChange,
  onOpenMapping,
}: {
  overview: ReturnType<typeof useAgencyOverview>;
  view: AgencyJobsViewId;
  onViewChange: (view: AgencyViewId) => void;
  onOpenMapping?: (projectId: string, siteId?: string | null) => void;
}) {
  const { data, loading, error, setError, load } = overview;
  const [openIds, setOpenIds] = useState<Set<string>>(() => new Set());
  const [portfolioFilter, setPortfolioFilter] = useState<PortfolioFilter>("all");
  const [queueFilter, setQueueFilter] = useState<QueueFilter>("all");
  const [queuePage, setQueuePage] = useState(1);
  const [invoicePage, setInvoicePage] = useState(1);
  const [orphanPage, setOrphanPage] = useState(1);
  const [selectedAction, setSelectedAction] = useState<AgencyAction | null>(null);
  const [receivableFollowUps, setReceivableFollowUps] = useState<Record<string, ReceivableFollowUp>>({});
  const groups = useMemo(() => groupJobsByProject(data?.jobs ?? []), [data?.jobs]);
  const filteredGroups = useMemo(() => filterAgencyGroups(groups, portfolioFilter), [groups, portfolioFilter]);
  const actions = useMemo(() => data ? buildAgencyActions(data) : [], [data]);
  const filteredActions = useMemo(() => filterAgencyActions(actions, queueFilter), [actions, queueFilter]);
  const pagedActions = paginate(filteredActions, queuePage, ACTIONS_PER_PAGE);
  const pagedInvoices = paginate(data?.unlinked_invoices ?? [], invoicePage, INVOICES_PER_PAGE);
  const pagedOrphans = paginate(data?.billed_without_project ?? [], orphanPage, INVOICES_PER_PAGE);
  const toggle = (id: string) => setOpenIds((old) => { const next = new Set(old); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const resolveInvoice = async (payload: InvoiceResolutionPayload) => {
    const response = await fetch(`${API_BASE}/api/v1/financials/agency/invoice-resolutions`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!response.ok) { const body = await response.json().catch(() => null) as { detail?: string } | null; throw new Error(body?.detail || `Invoice resolution returned ${response.status}`); }
    const outcome = getInvoiceResolutionOutcome(await load()); if (outcome) setError(outcome);
  };
  if (loading && !data) return <div className="qb-skel" aria-label="Loading agency jobs"><div className="qb-skel-block h-24" /><div className="qb-skel-block h-72" /></div>;
  const position = data?.position;
  const portfolioFilterIds: PortfolioFilter[] = ["all", "attention", "late", "mapping", "financial"];
  const queueFilterIds: QueueFilter[] = ["all", "delivery", "mapping", "ar"];
  const portfolioFilters: { id: PortfolioFilter; label: string; count: number }[] = portfolioFilterIds.map((id) => ({ id, label: id === "all" ? "All" : id[0].toUpperCase() + id.slice(1), count: filterAgencyGroups(groups, id).length }));
  const queueFilters: { id: QueueFilter; label: string; count: number }[] = queueFilterIds.map((id) => ({ id, label: id === "ar" ? "Receivables" : id === "all" ? "All priorities" : id[0].toUpperCase() + id.slice(1), count: filterAgencyActions(actions, id).length }));
  return (
    <>
      <Tabs value={view} onValueChange={(id) => onViewChange(parseAgencyView(id))} className="qb-tabs">
        <TabsList className="qb-tablist">
          {AGENCY_JOB_VIEWS.map((item) => (
            <TabsTrigger key={item.id} value={item.id}>
              {item.label}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="jobs" className="qb-view">
          <div className="agency-control-room space-y-5">
            {error ? <p className="agency-snapshot__error" role="alert">{error}</p> : null}
            <div className="qb-moneyline" aria-label="Agency snapshot">
              <Figure label="Booked YTD" size="lg" metric="booked" value={position ? usd(position.booked_ytd) : "—"} sub={`QuickBooks ${data?.year ?? ""}`} />
              <Figure label="Open AR" size="lg" metric="ar" value={position ? usd(position.open_ar) : "—"} tone={position && position.open_ar > 0 ? "warn" : undefined} sub="All customers" />
              <Figure label="Live jobs" size="lg" metric="projects" value={<Count value={position?.live_jobs ?? 0} />} sub={`${position?.overdue_tasks ?? 0} overdue tasks`} />
              <Figure label="Join health" size="lg" metric="flag" value={<span>{position?.join_mapped ?? 0}<span className="qb-figure-rest"> / {position?.join_total ?? 0}</span></span>} tone={position && position.join_mapped < position.join_total ? "warn" : undefined} sub="Confirmed / override / internal" />
            </div>
            <Panel
              className="agency-priority-panel"
              title="Priority queue"
              meta={`${filteredActions.length} need attention`}
              hint="Late or at-risk Teamwork jobs, incomplete client mapping, and QuickBooks open AR. Unlinked invoices stay in the Invoices tab."
            >
              <FilterChips options={queueFilters} value={queueFilter} onChange={(next) => { setQueueFilter(next); setQueuePage(1); }} label="Filter priority queue" />
              {pagedActions.items.length ? (
                <div className="agency-priority-list">
                  {pagedActions.items.map((action) => {
                    const source = actionSource(action);
                    return (
                      <article key={action.id} className="agency-priority-row" data-kind={action.kind} data-source={source}>
                        <div className="agency-priority-row__meta">
                          <span className="agency-priority-row__source">{source}</span>
                          <span className="agency-priority-row__kind">{actionStatus(action)}</span>
                        </div>
                        <div className="agency-priority-row__summary">
                          <strong>{action.title}</strong>
                          <span>{action.detail}</span>
                        </div>
                        <div className="agency-priority-row__impact" data-empty={action.amount ? undefined : "true"}>
                          <span>Open AR</span>
                          <strong>{action.amount ? usd(action.amount) : "None"}</strong>
                        </div>
                        <button type="button" className="agency-resolve-button" onClick={() => setSelectedAction(action)}>
                          {actionLabel(action)}
                          <ArrowRight size={16} aria-hidden />
                        </button>
                      </article>
                    );
                  })}
                </div>
              ) : (
                <Empty>{getAgencyEmptyMessage(data !== null)}</Empty>
              )}
              <Pagination page={pagedActions.page} pageCount={pagedActions.pageCount} onChange={setQueuePage} label="Priority queue pages" />
            </Panel>
          </div>
        </TabsContent>

        <TabsContent value="portfolio" className="qb-view">
          <div className="agency-control-room space-y-5">
            <Panel title="Client portfolio" meta={`${filteredGroups.length} of ${groups.length} projects · client money is never summed across jobs`}><FilterChips options={portfolioFilters} value={portfolioFilter} onChange={setPortfolioFilter} label="Filter client portfolio" />{filteredGroups.length ? <div className="qb-tablewrap"><table className="qb-table"><thead><tr><th>Project</th><th>Status</th><th data-numeric="true">Jobs</th><th data-numeric="true">Hours MTD</th><th data-numeric="true">QB billed YTD</th><th data-numeric="true">Open AR</th><th>Join</th></tr></thead><tbody>{filteredGroups.map((group) => { const open = openIds.has(group.id); return <Fragment key={group.id}><tr data-expandable="true" data-open={open ? "true" : undefined} tabIndex={0} aria-expanded={open} onClick={() => toggle(group.id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(group.id); } }}><td><span className="inline-flex items-center gap-2"><span className="tw-row-chevron" aria-hidden><ChevronRight size={14} className="tw-row-chevron__icon" /></span><span className="qb-name">{group.clientName}</span></span></td><td>{group.status}</td><td data-numeric="true">{group.jobCount}</td><td data-numeric="true"><HoursValue minutes={group.hoursMtdMinutes} /></td><td data-numeric="true">{moneyCell(group.billedYtd)}</td><td data-numeric="true">{moneyCell(group.openAr)}</td><td><Pill label={group.join} tone={joinTone(group.join)} /></td></tr>{open ? group.jobs.map((row) => <tr key={row.project_id} className="agency-job-child"><td><div className="agency-job-child__name"><span className="qb-tag !ml-0">{row.job_label}</span>{row.project_name !== row.job_label ? <span className="agency-job-child__title">{row.project_name}</span> : null}</div></td><td>{row.status || "—"}</td><td data-numeric="true" /><td data-numeric="true"><HoursValue minutes={row.hours_mtd_minutes} /></td><td data-numeric="true" /><td data-numeric="true" /><td><Pill label={row.join} tone={joinTone(row.join)} /></td></tr>) : null}</Fragment>; })}</tbody></table></div> : <Empty>No projects match this filter.</Empty>}</Panel>
            <Note>Expand a client to inspect individual Teamwork jobs. Billed and AR figures remain client-level values.</Note>
          </div>
        </TabsContent>

        <TabsContent value="invoices" className="qb-view">
          <div className="agency-control-room space-y-5">
            <Panel title="Invoice reconciliation" meta={`${data?.unlinked_invoices.length ?? 0} invoices need a project or job relationship`}>{pagedInvoices.items.length ? <div className="qb-tablewrap"><table className="qb-table agency-invoice-table"><thead><tr><th>Invoice</th><th>Customer</th><th>Status</th><th>Relationship</th><th data-numeric="true">Total</th><th data-numeric="true">Open AR</th><th><span className="sr-only">Action</span></th></tr></thead><tbody>{pagedInvoices.items.map((invoice) => { const presentation = invoiceStatusPresentation(invoice); const action = actions.find((candidate) => candidate.kind === "invoice" && candidate.invoiceId === invoice.invoice_id) ?? null; return <tr key={invoice.invoice_id}><td><strong>{invoice.invoice_number || invoice.invoice_id}</strong><span className="agency-table-sub">Due {invoice.due_date || "—"}</span></td><td>{invoice.customer_name || "—"}</td><td><Pill label={presentation.label} tone={presentation.tone} /></td><td>{invoiceRelationshipLabel()}</td><td data-numeric="true">{usd(invoice.total_amt)}</td><td data-numeric="true">{usd(invoice.open_ar)}</td><td><button type="button" className="agency-resolve-button agency-resolve-button--table" disabled={!action} onClick={() => setSelectedAction(action)}>Resolve<ArrowRight size={14} aria-hidden /></button></td></tr>; })}</tbody></table></div> : <Empty>No unlinked invoices.</Empty>}<Pagination page={pagedInvoices.page} pageCount={pagedInvoices.pageCount} onChange={setInvoicePage} label="Invoice reconciliation pages" /></Panel>
          </div>
        </TabsContent>

        <TabsContent value="orphans" className="qb-view">
          <div className="agency-control-room space-y-5">
            <Panel title="Billed without a live Teamwork project" meta={`${data?.billed_without_project.length ?? 0} customers`}>{pagedOrphans.items.length ? <div className="qb-tablewrap"><table className="qb-table"><thead><tr><th>Customer</th><th data-numeric="true">Billed YTD</th><th data-numeric="true">Open AR</th></tr></thead><tbody>{pagedOrphans.items.map((row) => <tr key={row.customer_id}><td>{row.customer_name}</td><td data-numeric="true">{usd(row.billed_ytd)}</td><td data-numeric="true">{usd(row.open_ar)}</td></tr>)}</tbody></table></div> : <Empty>No orphan billed customers.</Empty>}<Pagination page={pagedOrphans.page} pageCount={pagedOrphans.pageCount} onChange={setOrphanPage} label="Billed customers pages" /></Panel>
          </div>
        </TabsContent>
      </Tabs>
      <AgencyResolutionDrawer key={selectedAction?.id ?? "closed"} action={selectedAction} options={data?.resolution_options ?? []} open={selectedAction !== null} onOpenChange={(open) => { if (!open) setSelectedAction(null); }} onResolveInvoice={resolveInvoice} onOpenMapping={(projectId) => onOpenMapping?.(projectId, selectedAction?.kind === "invoice" ? undefined : selectedAction?.source.site_id)} receivableFollowUp={selectedAction?.kind === "receivable" ? receivableFollowUps[selectedAction.id] : undefined} onRecordReceivableFollowUp={(action, note) => setReceivableFollowUps((current) => ({ ...current, [action.id]: { note, recorded: true } }))} />
    </>
  );
}
