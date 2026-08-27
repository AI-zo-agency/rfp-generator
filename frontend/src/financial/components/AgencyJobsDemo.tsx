"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, RefreshCw } from "lucide-react";
import { Empty, Figure, FilterChips, Note, Panel, Pill, usd } from "./qb-ui";
import { AgencyResolutionDrawer, type InvoiceResolutionPayload } from "./AgencyResolutionDrawer";
import { Count, HoursValue } from "./teamwork/kpis";
import { buildAgencyActions, type AgencyAction } from "../lib/agency-action-queue";
import { groupJobsByProject } from "../lib/agency-project-groups";
import type { AgencyOverview, AgencyJoinStatus } from "../types/agency";
import type { ReceivableFollowUp } from "./AgencyResolutionDrawer";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";
type PortfolioFilter = "all" | "attention" | "late" | "mapping" | "financial";
type QueueFilter = "all" | "delivery" | "mapping" | "ar" | "invoices";
type ProjectGroup = ReturnType<typeof groupJobsByProject>[number];

function joinTone(join: AgencyJoinStatus) { return ["confirmed", "job override", "internal"].includes(join) ? "good" as const : ["suggested", "ambiguous"].includes(join) ? "warn" as const : "muted" as const; }
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
export function getAgencyLoadError(message: string, hasCachedData: boolean) {
  return hasCachedData
    ? `Refresh failed: ${message}. We’re showing the last loaded data; retry to refresh it.`
    : `Could not load agency overview: ${message}`;
}
export type AgencyLoadResult = { ok: true; data: AgencyOverview } | { ok: false; message: string };
export function applyAgencyLoadResult(previousData: AgencyOverview | null, result: AgencyLoadResult) {
  if (result.ok) return { data: result.data, error: null };
  return { data: previousData, error: getAgencyLoadError(result.message, previousData !== null) };
}
export function filterAgencyActions(actions: AgencyAction[], filter: QueueFilter): AgencyAction[] {
  if (filter === "all") return actions;
  const kind = filter === "ar" ? "receivable" : filter === "invoices" ? "invoice" : filter;
  return actions.filter((action) => action.kind === kind);
}
export function invoiceRelationshipLabel(invoice: { relationship?: "missing relationship"; status?: string }) {
  return invoice.relationship === "missing relationship" ? "No project or job linked" : "No project or job linked";
}
export function getAgencyEmptyMessage(hasOverview: boolean) {
  return hasOverview ? "No owner actions match this filter." : "No agency overview is available. Retry to load the control room.";
}
export function invoiceStatusPresentation(invoice: { status?: string; open_ar: number }) {
  const status = invoice.status?.trim().toLowerCase();
  if (status === "paid") return { label: "paid", tone: "good" as const };
  if (status === "open" || status === "overdue") return { label: status, tone: "warn" as const };
  if (status) return { label: status, tone: "muted" as const };
  return invoice.open_ar > 0 ? { label: "open", tone: "warn" as const } : { label: "paid", tone: "good" as const };
}
export function getInvoiceResolutionOutcome(refreshed: boolean) {
  return refreshed ? null : "Invoice resolution was saved, but the overview needs a refresh. Retry to confirm the latest data.";
}
function actionStatus(action: AgencyAction) { return action.kind === "delivery" ? "Delivery" : action.kind === "mapping" ? "Mapping" : action.kind === "receivable" ? "Financial" : "Invoice"; }

export function AgencyJobsDemo({ onOpenMapping }: { onOpenMapping?: (projectId: string, siteId?: string | null) => void }) {
  const [data, setData] = useState<AgencyOverview | null>(null), [loading, setLoading] = useState(true), [error, setError] = useState<string | null>(null);
  const cachedData = useRef<AgencyOverview | null>(null);
  const [openIds, setOpenIds] = useState<Set<string>>(() => new Set()), [filter, setFilter] = useState<PortfolioFilter>("all"), [queueFilter, setQueueFilter] = useState<QueueFilter>("all"), [selectedAction, setSelectedAction] = useState<AgencyAction | null>(null);
  const [receivableFollowUps, setReceivableFollowUps] = useState<Record<string, ReceivableFollowUp>>({});
  const load = useCallback(async (): Promise<boolean> => { setLoading(true); setError(null); try { const res = await fetch(`${API_BASE}/api/v1/financials/agency/overview?year=${new Date().getFullYear()}`, { credentials: "include" }); if (!res.ok) throw new Error(`Agency overview returned ${res.status}`); const overview = await res.json() as AgencyOverview; const next = applyAgencyLoadResult(cachedData.current, { ok: true, data: overview }); cachedData.current = next.data; setData(next.data); setError(next.error); return true; } catch (caught) { console.error("Agency overview failed", { operation: "agency_overview_load", error: caught }); const message = caught instanceof Error ? caught.message : "Could not load agency overview"; const next = applyAgencyLoadResult(cachedData.current, { ok: false, message }); setData(next.data); setError(next.error); return false; } finally { setLoading(false); } }, []);
  useEffect(() => { const timer = window.setTimeout(() => { void load(); }, 0); return () => window.clearTimeout(timer); }, [load]);
  const groups = useMemo(() => groupJobsByProject(data?.jobs ?? []), [data?.jobs]);
  const filteredGroups = useMemo(() => filterAgencyGroups(groups, filter), [filter, groups]);
  const actions = useMemo(() => data ? buildAgencyActions(data) : [], [data]);
  const filteredActions = useMemo(() => filterAgencyActions(actions, queueFilter), [actions, queueFilter]);
  const toggle = (id: string) => setOpenIds((old) => { const next = new Set(old); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const resolveInvoice = async (payload: InvoiceResolutionPayload) => { const res = await fetch(`${API_BASE}/api/v1/financials/agency/invoice-resolutions`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); if (!res.ok) { const body = await res.json().catch(() => null) as { detail?: string } | null; throw new Error(body?.detail || `Invoice resolution returned ${res.status}`); } const outcome = getInvoiceResolutionOutcome(await load()); if (outcome) setError(outcome); };
  if (loading && !data) return <div className="qb-skel" aria-label="Loading agency jobs"><div className="qb-skel-block h-24" /><div className="qb-skel-block h-72" /></div>;
  const position = data?.position;
  const filterIds: PortfolioFilter[] = ["all", "attention", "late", "mapping", "financial"];
  const filters: { id: PortfolioFilter; label: string; count: number }[] = filterIds.map((id) => ({ id, label: id === "all" ? "All" : id[0].toUpperCase() + id.slice(1), count: filterAgencyGroups(groups, id).length }));
  const queueFilterIds: QueueFilter[] = ["all", "delivery", "mapping", "ar", "invoices"];
  const queueFilters = queueFilterIds.map((id) => ({ id, label: id === "ar" ? "AR" : id === "all" ? "All" : id[0].toUpperCase() + id.slice(1), count: filterAgencyActions(actions, id).length }));
  return <div className="space-y-4 agency-control-room">
    <section className="agency-snapshot" aria-label="Agency snapshot"><div><p className="agency-snapshot__eyebrow">Agency control room</p><p className="agency-snapshot__freshness">{formatAgencyFreshness(data?.as_of)}</p>{error ? <p className="agency-snapshot__error" role="alert">{error}</p> : null}</div><div className="agency-snapshot__tools"><button type="button" className="qb-retry" disabled={loading} onClick={() => void load()}><RefreshCw className={loading ? "animate-spin" : undefined} size={14} aria-hidden />Refresh</button></div><div className="qb-moneyline agency-snapshot__kpis"><Figure label="Booked YTD" size="lg" metric="booked" value={position ? usd(position.booked_ytd) : "—"} sub={`QuickBooks ${data?.year ?? ""}`} /><Figure label="Open AR" size="lg" metric="ar" value={position ? usd(position.open_ar) : "—"} tone={position && position.open_ar > 0 ? "warn" : undefined} sub="All customers" /><Figure label="Live jobs" size="lg" metric="projects" value={<Count value={position?.live_jobs ?? 0} />} sub={`${position?.overdue_tasks ?? 0} overdue tasks`} /><Figure label="Join health" size="lg" metric="flag" value={<span>{position?.join_mapped ?? 0}<span className="qb-figure-rest"> / {position?.join_total ?? 0}</span></span>} tone={position && position.join_mapped < position.join_total ? "warn" : undefined} sub="Confirmed / override / internal" /></div></section>
    <Panel title="Needs your attention" meta={`${filteredActions.length} of ${actions.length} actions`}><FilterChips options={queueFilters} value={queueFilter} onChange={setQueueFilter} label="Filter owner action queue" />{filteredActions.length ? <div className="agency-action-grid">{filteredActions.map((action) => <article className="agency-action" key={action.id} data-kind={action.kind}><div><p className="agency-action__status">{actionStatus(action)}</p><h4>{action.title}</h4><p>{action.detail}</p></div><div className="agency-action__foot"><span>Impact {action.amount ? usd(action.amount) : "—"}</span><button type="button" className="qb-more !mt-0" onClick={() => setSelectedAction(action)}>Resolve {action.kind === "invoice" ? "invoice" : action.kind}</button></div></article>)}</div> : <Empty>{getAgencyEmptyMessage(data !== null)}</Empty>}</Panel>
    <Panel title="Client portfolio" meta={`${filteredGroups.length} of ${groups.length} projects · money is calculated once per project`}><FilterChips options={filters} value={filter} onChange={setFilter} label="Filter client portfolio" />{filteredGroups.length ? <div className="qb-tablewrap"><table className="qb-table"><thead><tr><th>Project</th><th>Status</th><th>Jobs</th><th>Hours MTD</th><th>QB billed YTD</th><th>Open AR</th><th>Join</th></tr></thead><tbody>{filteredGroups.map((group) => { const open = openIds.has(group.id); return <Fragment key={group.id}><tr data-expandable="true" data-open={open ? "true" : undefined} tabIndex={0} aria-expanded={open} onClick={() => toggle(group.id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(group.id); } }}><td><span className="inline-flex items-center gap-2"><span className="tw-row-chevron" aria-hidden><ChevronRight size={14} className="tw-row-chevron__icon" /></span><span className="qb-name">{group.clientName}</span></span></td><td>{group.status}</td><td data-numeric="true">{group.jobCount}</td><td data-numeric="true"><HoursValue minutes={group.hoursMtdMinutes} /></td><td data-numeric="true">{moneyCell(group.billedYtd)}</td><td data-numeric="true">{moneyCell(group.openAr)}</td><td><Pill label={group.join} tone={joinTone(group.join)} /></td></tr>{open ? group.jobs.map((row) => <tr key={row.project_id} className="agency-job-child"><td><div className="agency-job-child__name"><span className="qb-tag !ml-0">{row.job_label}</span>{row.project_name !== row.job_label ? <span className="agency-job-child__title">{row.project_name}</span> : null}</div></td><td>{row.status || "—"}</td><td data-numeric="true" /><td data-numeric="true"><HoursValue minutes={row.hours_mtd_minutes} /></td><td data-numeric="true" /><td data-numeric="true" /><td><Pill label={row.join} tone={joinTone(row.join)} /></td></tr>) : null}</Fragment>; })}</tbody></table></div> : <Empty>No projects match this filter.</Empty>}</Panel>
    <div className="grid gap-4 lg:grid-cols-2"><Panel title="Unlinked invoices watchlist" meta={`${data?.unlinked_invoices.length ?? 0}`}>{data?.unlinked_invoices.length ? <div className="qb-tablewrap"><table className="qb-table"><thead><tr><th>Invoice</th><th>Customer</th><th>Status</th><th>Relationship</th><th>Total</th><th>Open AR</th><th /></tr></thead><tbody>{data.unlinked_invoices.map((invoice) => { const presentation = invoiceStatusPresentation(invoice); return <tr key={invoice.invoice_id}><td>{invoice.invoice_number || invoice.invoice_id}</td><td>{invoice.customer_name || "—"}</td><td><Pill label={presentation.label} tone={presentation.tone} /></td><td>{invoiceRelationshipLabel(invoice)}</td><td data-numeric="true">{usd(invoice.total_amt)}</td><td data-numeric="true">{usd(invoice.open_ar)}</td><td><button type="button" className="qb-more !mt-0" onClick={() => setSelectedAction(actions.find((action) => action.kind === "invoice" && action.invoiceId === invoice.invoice_id) ?? null)}>Resolve</button></td></tr>; })}</tbody></table></div> : <Empty>No unlinked invoices.</Empty>}</Panel><Panel title="Billed, no live Teamwork project" meta={`${data?.billed_without_project.length ?? 0}`}>{data?.billed_without_project.length ? <div className="qb-tablewrap"><table className="qb-table"><thead><tr><th>Customer</th><th>Billed YTD</th><th>Open AR</th></tr></thead><tbody>{data.billed_without_project.map((row) => <tr key={row.customer_id}><td>{row.customer_name}</td><td data-numeric="true">{usd(row.billed_ytd)}</td><td data-numeric="true">{usd(row.open_ar)}</td></tr>)}</tbody></table></div> : <Empty>No orphan billed customers.</Empty>}</Panel></div>
    <Note>Expand a project to see its Teamwork jobs on the same columns. QB billed / AR stay on the project row.</Note><AgencyResolutionDrawer key={selectedAction?.id ?? "closed"} action={selectedAction} options={data?.resolution_options ?? []} open={selectedAction !== null} onOpenChange={(open) => { if (!open) setSelectedAction(null); }} onResolveInvoice={resolveInvoice} onOpenMapping={(projectId) => onOpenMapping?.(projectId, selectedAction?.kind === "invoice" ? undefined : selectedAction?.source.site_id)} receivableFollowUp={selectedAction?.kind === "receivable" ? receivableFollowUps[selectedAction.id] : undefined} onRecordReceivableFollowUp={(action, note) => setReceivableFollowUps((current) => ({ ...current, [action.id]: { note, recorded: true } }))} />
  </div>;
}
