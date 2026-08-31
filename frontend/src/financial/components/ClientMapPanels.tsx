"use client";

import { useMemo, useState, type FormEvent } from "react";
import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp, ChevronsUpDown, CircleHelp, Search, Sparkles } from "lucide-react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { AiIntelligenceDrawer, type DrawerChrome } from "./AiIntelligenceDrawer";
import { Empty, Panel, Pill } from "./qb-ui";
import { AgencyJobsDemo, AgencyJobsToolbar, useAgencyOverview } from "./AgencyJobsDemo";
import { useAgencyChat } from "../lib/use-agency-chat";
import { useAgencyInsights } from "../lib/use-agency-insights";
import { useClientMap } from "../lib/use-client-map";
import { type AgencyJobsViewId, type AgencyViewId } from "../lib/financial-tab";
import type { ClientMapCorePatch, ClientMapRow } from "../types/client-map";
import "./QuickBooksLedger.css";

const AGENCY_CHROME: DrawerChrome = {
  source: "Agency",
  seeds: [
    "What carried over from last week?",
    "Which mapping gaps matter most?",
    "Summarize this for Monday standup",
  ],
  viewLabel: {
    jobs: "Queue",
    portfolio: "Portfolio",
    invoices: "Invoices",
    orphans: "Orphans",
    mapping: "Mapping",
  },
  placeholder: "Ask about carryover or the owner queue…",
  empty: "Nothing flagged in the Agency join layer right now.",
};

const INPUT =
  "h-11 min-w-32 rounded-md border border-[var(--zo-border)] bg-[var(--zo-card-bg)] px-3 text-base text-[var(--zo-text)] outline-none focus:border-[var(--zo-teal)]";
const MAPPINGS_PER_PAGE = 10;
const INTERNAL_HINT =
  "Your own agency (e.g. ZO Agency), not a paying client. Skips Teamwork/QuickBooks auto-linking and billed/AR dollars.";
const BLANK_STATUS = "(blank)";
type MappingFilter = "all" | ClientMapRow["link_confidence"];
export type ClientNameSort = "asc" | "desc" | null;

function InternalFlag({
  checked,
  onChange,
  className = "mapping-check",
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  className?: string;
}) {
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <label className={className}>
            <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
            Internal
            <CircleHelp size={15} aria-hidden className="opacity-55" />
          </label>
        </TooltipTrigger>
        <TooltipContent side="bottom" className="max-w-[240px]">
          {INTERNAL_HINT}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export function statusKey(status: string | null | undefined) {
  const trimmed = status?.trim();
  return trimmed ? trimmed : BLANK_STATUS;
}

export function collectClientMapStatuses(rows: ClientMapRow[]) {
  return [...new Set(rows.map((row) => statusKey(row.status)))].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
}

export function filterClientMapRows(
  rows: ClientMapRow[],
  query: string,
  filter: MappingFilter,
  statuses: string[] = [],
) {
  const normalizedQuery = query.trim().toLowerCase();
  const statusSet = new Set(statuses);
  return rows.filter((row) => {
    const matchesFilter = filter === "all" || row.link_confidence === filter;
    const matchesStatus = statusSet.size === 0 || statusSet.has(statusKey(row.status));
    const haystack = [row.tag_code, row.client_name, row.current_am, ...row.qb_customer_names, ...row.teamwork_company_names]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return matchesFilter && matchesStatus && (!normalizedQuery || haystack.includes(normalizedQuery));
  });
}

export function sortClientMapRows(rows: ClientMapRow[], sort: ClientNameSort) {
  if (!sort) return rows;
  return [...rows].sort((a, b) => {
    const cmp = a.client_name.localeCompare(b.client_name, undefined, { sensitivity: "base" });
    return sort === "asc" ? cmp : -cmp;
  });
}

export function paginateClientMapRows(rows: ClientMapRow[], page: number, pageSize = MAPPINGS_PER_PAGE) {
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const safePage = Math.min(Math.max(1, page), pageCount);
  return { page: safePage, pageCount, rows: rows.slice((safePage - 1) * pageSize, safePage * pageSize) };
}

export function nextClientNameSort(current: ClientNameSort): ClientNameSort {
  if (current === null) return "asc";
  if (current === "asc") return "desc";
  return null;
}

function MappingPagination({ page, pageCount, onChange }: { page: number; pageCount: number; onChange: (page: number) => void }) {
  if (pageCount <= 1) return null;
  return <nav className="mapping-pagination" aria-label="Client mapping pages"><span>Page {page} of {pageCount}</span><div><button type="button" onClick={() => onChange(page - 1)} disabled={page === 1} aria-label="Previous mapping page"><ChevronLeft size={15} /></button><button type="button" onClick={() => onChange(page + 1)} disabled={page === pageCount} aria-label="Next mapping page"><ChevronRight size={15} /></button></div></nav>;
}

function ClientNameSortButton({ sort, onChange }: { sort: ClientNameSort; onChange: (next: ClientNameSort) => void }) {
  return (
    <button
      type="button"
      className="qb-sort"
      onClick={() => onChange(nextClientNameSort(sort))}
      aria-label={`Sort clients ${sort === "asc" ? "descending" : sort === "desc" ? "unsorted" : "ascending"}`}
    >
      Client
      {sort === "asc" ? (
        <ChevronUp size={14} strokeWidth={2.5} aria-hidden />
      ) : sort === "desc" ? (
        <ChevronDown size={14} strokeWidth={2.5} aria-hidden />
      ) : (
        <ChevronsUpDown size={14} strokeWidth={2} aria-hidden className="qb-sort-idle" />
      )}
    </button>
  );
}

function StatusMultiselect({
  options,
  selected,
  onChange,
}: {
  options: string[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const active = selected.length > 0;
  const toggle = (value: string) => {
    onChange(selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value]);
  };

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button type="button" className="mapping-status-filter" data-active={active ? "true" : undefined} aria-label="Filter by status">
          Status{active ? <span>{selected.length}</span> : null}
          <ChevronDown size={14} aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="mapping-status-menu w-56 p-2">
        <div className="mapping-status-menu__head">
          <span>Status</span>
          {active ? (
            <button type="button" className="mapping-status-menu__clear" onClick={() => onChange([])}>
              Clear
            </button>
          ) : null}
        </div>
        {options.length ? (
          <ul className="mapping-status-menu__list">
            {options.map((option) => (
              <li key={option}>
                <label className="mapping-status-menu__option">
                  <input type="checkbox" checked={selected.includes(option)} onChange={() => toggle(option)} />
                  <span>{option}</span>
                </label>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mapping-status-menu__empty">No statuses yet.</p>
        )}
      </PopoverContent>
    </Popover>
  );
}

const emptyRow = (): ClientMapCorePatch => ({
  tag_code: "",
  client_name: "",
  current_am: "",
  status: "Active",
  is_internal: false,
});

function tone(confidence: ClientMapRow["link_confidence"]) {
  if (confidence === "confirmed") return "good";
  if (confidence === "suggested") return "warn";
  return "muted";
}

function ClientRow({
  row,
  busy,
  onUpdate,
  onAccept,
  onReject,
  onDelete,
}: {
  row: ClientMapRow;
  busy: boolean;
  onUpdate: (id: string, patch: ClientMapCorePatch) => Promise<unknown>;
  onAccept: (id: string) => Promise<unknown>;
  onReject: (id: string) => Promise<unknown>;
  onDelete: (id: string) => Promise<unknown>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ClientMapCorePatch>({
    tag_code: row.tag_code,
    client_name: row.client_name,
    current_am: row.current_am,
    status: row.status,
    is_internal: row.is_internal,
  });

  const save = async () => {
    await onUpdate(row.id, draft);
    setEditing(false);
  };

  if (editing) {
    return (
      <tr data-editing="true">
        <td><input aria-label="Tag code" className="mapping-field mapping-field--tag" value={draft.tag_code} onChange={(event) => setDraft({ ...draft, tag_code: event.target.value })} /></td>
        <td>
          <div className="mapping-edit-stack">
            <input aria-label="Client name" className="mapping-field" value={draft.client_name} onChange={(event) => setDraft({ ...draft, client_name: event.target.value })} />
            <InternalFlag checked={draft.is_internal} onChange={(is_internal) => setDraft({ ...draft, is_internal })} />
          </div>
        </td>
        <td><input aria-label="Account manager" className="mapping-field mapping-field--am" value={draft.current_am ?? ""} onChange={(event) => setDraft({ ...draft, current_am: event.target.value })} /></td>
        <td><input aria-label="Status" className="mapping-field mapping-field--status" value={draft.status ?? ""} onChange={(event) => setDraft({ ...draft, status: event.target.value })} /></td>
        <td>{row.qb_customer_names.join(", ") || "—"}</td>
        <td>{row.teamwork_company_names.join(", ") || "—"}</td>
        <td><Pill label={row.link_confidence} tone={tone(row.link_confidence)} /></td>
        <td>
          <div className="mapping-actions">
            <button type="button" className="mapping-action mapping-action--primary" disabled={busy || !draft.tag_code.trim() || !draft.client_name.trim()} onClick={() => void save()}>Save</button>
            <button type="button" className="mapping-action" onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </td>
      </tr>
    );
  }

  return (
    <tr>
      <td><span className="qb-tag !ml-0">{row.tag_code}</span></td>
      <td>
        <span className="qb-name">{row.client_name}</span>
        {row.is_internal ? (
          <TooltipProvider delayDuration={200}>
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="qb-tag" tabIndex={0}>internal</span>
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-[240px]">{INTERNAL_HINT}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        ) : null}
      </td>
      <td>{row.current_am || "—"}</td>
      <td>{row.status || "—"}</td>
      <td>{row.qb_customer_names.join(", ") || "—"}</td>
      <td>{row.teamwork_company_names.join(", ") || "—"}</td>
      <td><Pill label={row.link_confidence} tone={tone(row.link_confidence)} /></td>
      <td>
        <div className="mapping-actions">
          {row.link_confidence === "suggested" ? <button type="button" className="mapping-action mapping-action--primary" disabled={busy} onClick={() => void onAccept(row.id)}>Accept</button> : null}
          {row.link_confidence !== "unmatched" ? <button type="button" className="mapping-action" disabled={busy} onClick={() => void onReject(row.id)}>Reject</button> : null}
          <button type="button" className="mapping-action" disabled={busy} onClick={() => setEditing(true)}>Edit</button>
          <button
            type="button"
            className="mapping-action mapping-action--danger"
            disabled={busy}
            onClick={() => {
              if (window.confirm(`Delete ${row.client_name}?`)) void onDelete(row.id);
            }}
          >
            Delete
          </button>
        </div>
      </td>
    </tr>
  );
}

function CreateClientForm({
  busy,
  onCreate,
}: {
  busy: boolean;
  onCreate: (patch: ClientMapCorePatch) => Promise<unknown>;
}) {
  const [draft, setDraft] = useState(emptyRow);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!draft.tag_code.trim() || !draft.client_name.trim()) return;
    await onCreate(draft);
    setDraft(emptyRow());
  };

  return (
    <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => void submit(event)}>
      <label className="grid gap-1 text-[15px] text-[var(--zo-text-muted)]">Tag<input className={INPUT} value={draft.tag_code} onChange={(event) => setDraft({ ...draft, tag_code: event.target.value })} required /></label>
      <label className="grid gap-1 text-[15px] text-[var(--zo-text-muted)]">Client<input className={INPUT} value={draft.client_name} onChange={(event) => setDraft({ ...draft, client_name: event.target.value })} required /></label>
      <label className="grid gap-1 text-[15px] text-[var(--zo-text-muted)]">AM<input className={INPUT} value={draft.current_am ?? ""} onChange={(event) => setDraft({ ...draft, current_am: event.target.value })} /></label>
      <label className="grid gap-1 text-[15px] text-[var(--zo-text-muted)]">Status<input className={INPUT} value={draft.status ?? ""} onChange={(event) => setDraft({ ...draft, status: event.target.value })} /></label>
      <InternalFlag
        className="mb-2 inline-flex items-center gap-2 text-base text-[var(--zo-text-secondary)]"
        checked={draft.is_internal}
        onChange={(is_internal) => setDraft({ ...draft, is_internal })}
      />
      <button type="submit" className="qb-retry" disabled={busy}>Add client</button>
    </form>
  );
}

export function mappingHandoffError({
  projectId,
  siteId,
  client,
}: {
  projectId: string;
  siteId: string | null;
  client: ClientMapRow | undefined;
}): string | null {
  if (!projectId.trim()) return "Choose a Teamwork project before saving the override.";
  if (!siteId?.trim()) return "This mapping needs its Teamwork workspace context. Reopen it from the Agency action and try again.";
  if (!client) return "Choose a client with a QuickBooks customer mapping.";
  if (!client.qb_customer_ids.length) return `${client.client_name} has no QuickBooks customer mapping. Select a mapped client or attach a QuickBooks customer first.`;
  return null;
}

function MappingView({
  map,
  prefilledProjectId,
  prefilledSiteId,
}: {
  map: ReturnType<typeof useClientMap>;
  prefilledProjectId: string | null;
  prefilledSiteId: string | null;
}) {
  const [projectId, setProjectId] = useState(prefilledProjectId ?? "");
  const [clientMapId, setClientMapId] = useState("");
  const [attachTargetId, setAttachTargetId] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<MappingFilter>("all");
  const [clientSort, setClientSort] = useState<ClientNameSort>(null);
  const [statusFilter, setStatusFilter] = useState<string[]>([]);
  const [page, setPage] = useState(1);
  const [overrideError, setOverrideError] = useState<string | null>(null);
  const [overrideNotice, setOverrideNotice] = useState<string | null>(null);
  const attachTarget = map.rows.find((row) => row.id === attachTargetId);
  const siteId = prefilledSiteId?.trim() || null;
  const existingOverride = useMemo(
    () => map.jobOverrides.find((override) => override.site_id === siteId && override.project_id === Number(projectId)),
    [map.jobOverrides, projectId, siteId],
  );
  const resolvedClientMapId = clientMapId || existingOverride?.client_map_id || "";
  const selectedClient = map.rows.find((row) => row.id === resolvedClientMapId);
  const statusOptions = useMemo(() => collectClientMapStatuses(map.rows), [map.rows]);
  const filteredRows = useMemo(
    () => sortClientMapRows(filterClientMapRows(map.rows, query, filter, statusFilter), clientSort),
    [clientSort, filter, map.rows, query, statusFilter],
  );
  const pagedRows = paginateClientMapRows(filteredRows, page);
  const filters: { id: MappingFilter; label: string; count: number }[] = [
    { id: "all", label: "All", count: map.rows.length },
    { id: "suggested", label: "Needs review", count: map.rows.filter((row) => row.link_confidence === "suggested").length },
    { id: "unmatched", label: "Unmatched", count: map.rows.filter((row) => row.link_confidence === "unmatched").length },
    { id: "confirmed", label: "Confirmed", count: map.rows.filter((row) => row.link_confidence === "confirmed").length },
  ];

  const addOverride = async (event: FormEvent) => {
    event.preventDefault();
    const validationError = mappingHandoffError({ projectId, siteId, client: selectedClient });
    if (validationError) {
      setOverrideError(validationError);
      setOverrideNotice(null);
      return;
    }
    if (!siteId) return;
    setOverrideError(null);
    const saved = await map.addJobOverride({
      site_id: siteId,
      project_id: Number(projectId),
      client_map_id: resolvedClientMapId,
    });
    if (saved) setOverrideNotice(existingOverride ? "Job override updated." : "Job override created.");
  };

  if (map.loading && !map.rows.length) {
    return <div className="qb-skel" aria-label="Loading client mapping"><div className="qb-skel-block h-24" /><div className="qb-skel-block h-72" /></div>;
  }

  return (
    <div className="qb-view mapping-view">
      {map.error ? <div className="qb-error"><p>{map.error}</p></div> : null}

      <Panel title="Client map" meta={`Showing ${pagedRows.rows.length} of ${filteredRows.length}${filteredRows.length !== map.rows.length ? ` · ${map.rows.length} total` : ""}`} action={<CreateClientForm busy={map.busy} onCreate={map.create} />}>
        <div className="mapping-controls">
          <label className="mapping-search"><Search size={15} aria-hidden /><span className="sr-only">Search client mappings</span><input value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder="Search client, tag, or linked company" /></label>
          <div className="mapping-filters" aria-label="Filter client mappings">{filters.map((item) => <button key={item.id} type="button" data-active={filter === item.id ? "true" : undefined} onClick={() => { setFilter(item.id); setPage(1); }}>{item.label}<span>{item.count}</span></button>)}</div>
        </div>
        {filteredRows.length ? (
          <div className="qb-tablewrap">
            <table className="qb-table mapping-table">
              <thead>
                <tr>
                  <th>Tag</th>
                  <th>
                    <ClientNameSortButton
                      sort={clientSort}
                      onChange={(next) => {
                        setClientSort(next);
                        setPage(1);
                      }}
                    />
                  </th>
                  <th>AM</th>
                  <th>
                    <StatusMultiselect
                      options={statusOptions}
                      selected={statusFilter}
                      onChange={(next) => {
                        setStatusFilter(next);
                        setPage(1);
                      }}
                    />
                  </th>
                  <th>QuickBooks</th>
                  <th>Teamwork</th>
                  <th>Confidence</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {pagedRows.rows.map((row) => (
                  <ClientRow key={row.id} row={row} busy={map.busy} onUpdate={map.update} onAccept={map.accept} onReject={map.reject} onDelete={map.remove} />
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty>{map.rows.length ? "No client mappings match this review." : "No client mappings yet. Import Tags or add one above."}</Empty>}
        <MappingPagination page={pagedRows.page} pageCount={pagedRows.pageCount} onChange={setPage} />
      </Panel>

      {(map.unmatched.teamwork.length || map.unmatched.quickbooks.length) ? (
        <label className="mapping-attach-target">
          <span>Attach unmatched to</span>
          <select
            aria-label="Client to attach unmatched companies to"
            className={INPUT}
            value={attachTargetId}
            onChange={(event) => setAttachTargetId(event.target.value)}
          >
            <option value="">Choose a client</option>
            {map.rows.map((row) => (
              <option key={row.id} value={row.id}>{row.tag_code} — {row.client_name}</option>
            ))}
          </select>
        </label>
      ) : null}

      <div className="qb-two mapping-unmatched">
        <Panel title="Unmatched Teamwork" meta={`${map.unmatched.teamwork.length}`}>
          {map.unmatched.teamwork.length ? (
            <ul className="mapping-attach-list">
              {map.unmatched.teamwork.map((item) => (
                <li key={`${item.id}-${item.name}`}>
                  <button
                    type="button"
                    className="mapping-attach-item"
                    disabled={map.busy || !attachTarget}
                    onClick={() => attachTarget && void map.attachTeamwork(attachTarget, item)}
                  >
                    Attach {item.name || `Company ${item.id}`}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>All Teamwork companies are linked.</Empty>
          )}
        </Panel>
        <Panel title="Unmatched QuickBooks" meta={`${map.unmatched.quickbooks.length}`}>
          {map.unmatched.quickbooks.length ? (
            <ul className="mapping-attach-list">
              {map.unmatched.quickbooks.map((item) => (
                <li key={item.qbo_id}>
                  <button
                    type="button"
                    className="mapping-attach-item"
                    disabled={map.busy || !attachTarget}
                    onClick={() => attachTarget && void map.attachQuickBooks(attachTarget, item)}
                  >
                    Attach {item.display_name}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>All QuickBooks customers are linked.</Empty>
          )}
        </Panel>
      </div>

      <Panel
        title="Job overrides"
        meta={`${map.jobOverrides.length}`}
        hint="Force one Teamwork project to a mapped client (and its QuickBooks customer) when the usual tag/company match is wrong or missing. Open Mapping from an Agency job action so the workspace is included."
      >
        <form className="mb-4 flex flex-wrap items-end gap-2" onSubmit={(event) => void addOverride(event)}>
          <div className="basis-full text-base text-[var(--zo-text-secondary)]">
            {siteId
              ? `Teamwork workspace ready · resolve Project ${projectId || "—"} by selecting its mapped client.`
              : "Workspace context is unavailable. Open this from an Agency mapping action so the connected Teamwork workspace is included."}
          </div>
          <label className="grid gap-1 text-[15px] text-[var(--zo-text-muted)]">Project ID<input className={INPUT} type="number" value={projectId} onChange={(event) => setProjectId(event.target.value)} required /></label>
          <label className="grid gap-1 text-[15px] text-[var(--zo-text-muted)]">Mapped client<select aria-label="Mapped client" className={INPUT} value={resolvedClientMapId} onChange={(event) => { setClientMapId(event.target.value); setOverrideError(null); }} required><option value="">Choose a client</option>{map.rows.map((row) => <option key={row.id} value={row.id}>{row.tag_code} — {row.client_name}{row.qb_customer_names.length ? ` · ${row.qb_customer_names.join(", ")}` : " · no QuickBooks customer"}</option>)}</select></label>
          <button type="submit" className="qb-retry" disabled={map.busy || !siteId}>{existingOverride ? "Update override" : "Save override"}</button>
        </form>
        {overrideError ? <p className="qb-error mb-4" role="alert">{overrideError}</p> : null}
        {overrideNotice ? <p className="mb-4 text-base text-[var(--zo-success)]" role="status">{overrideNotice}</p> : null}
        {map.jobOverrides.length ? (
          <div className="qb-tablewrap"><table className="qb-table"><thead><tr><th>Site</th><th>Project</th><th>Client</th><th>QuickBooks</th><th /></tr></thead><tbody>
            {map.jobOverrides.map((override) => {
              const client = map.rows.find((row) => row.id === override.client_map_id);
              return <tr key={override.id}><td>{override.site_id}</td><td>{override.project_id}</td><td>{client?.client_name || "—"}</td><td>{override.qb_customer_names.join(", ") || "—"}</td><td><button type="button" className="qb-more !mt-0 text-[var(--zo-danger)]" disabled={map.busy} onClick={() => void map.removeJobOverride(override.id)}>Delete</button></td></tr>;
            })}
          </tbody></table></div>
        ) : <Empty>No job-level overrides.</Empty>}
      </Panel>
    </div>
  );
}

export function ClientMapPanels({
  view,
  onViewChange,
}: {
  view: AgencyViewId;
  onViewChange: (view: AgencyViewId) => void;
}) {
  const [mappingHandoff, setMappingHandoff] = useState<{ projectId: string; siteId: string | null } | null>(null);
  const [aiOpen, setAiOpen] = useState(false);
  const overview = useAgencyOverview();
  const map = useClientMap();
  const insights = useAgencyInsights();
  const chat = useAgencyChat();
  const openMappingForProject = (projectId: string, siteId?: string | null) => {
    setMappingHandoff({ projectId, siteId: siteId ?? null });
    onViewChange("mapping");
  };
  return (
    <div className="qb-ledger">
      <AgencyJobsToolbar
        data={overview.data}
        loading={overview.loading}
        error={overview.error}
        onRefresh={() => void overview.load()}
        view={view}
        onViewChange={onViewChange}
        aiHighImpact={view !== "mapping" ? insights.highImpact : 0}
        onOpenAi={view !== "mapping" ? () => setAiOpen(true) : undefined}
        aiOpen={aiOpen}
        mapping={
          view === "mapping"
            ? {
                busy: map.busy,
                loading: map.loading,
                rowCount: map.rows.length,
                lastLinkResult: map.lastLinkResult,
                onImport: () => void map.importSheet(),
                onFindLinks: () => void map.findLinks(),
                onRefresh: () => void map.reload(),
              }
            : undefined
        }
      />
      {view !== "mapping" ? (
        <AgencyJobsDemo
          overview={overview}
          view={view as AgencyJobsViewId}
          onViewChange={onViewChange}
          onOpenMapping={openMappingForProject}
        />
      ) : (
        <MappingView
          map={map}
          prefilledProjectId={mappingHandoff?.projectId ?? null}
          prefilledSiteId={mappingHandoff?.siteId ?? null}
        />
      )}
      <AiIntelligenceDrawer
        open={aiOpen}
        onClose={() => setAiOpen(false)}
        insights={insights}
        chat={chat}
        onGo={(id) => onViewChange(id as AgencyViewId)}
        chrome={{
          ...AGENCY_CHROME,
          notice: insights.data?.bootstrap
            ? "First weekly snapshot not recorded yet — carryover and “new this week” counts start after Friday’s job."
            : null,
        }}
      />
    </div>
  );
}
