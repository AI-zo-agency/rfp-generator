"use client";

import { useMemo, useState, type FormEvent } from "react";
import { ChevronLeft, ChevronRight, RefreshCw, Search } from "lucide-react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Empty, Panel, Pill } from "./qb-ui";
import { AgencyJobsDemo } from "./AgencyJobsDemo";
import { useClientMap } from "../lib/use-client-map";
import { parseAgencyView, type AgencyViewId } from "../lib/financial-tab";
import type { ClientMapCorePatch, ClientMapRow } from "../types/client-map";
import "./QuickBooksLedger.css";

const INPUT =
  "h-8 min-w-24 rounded-md border border-[var(--zo-border)] bg-[var(--zo-card-bg)] px-2 text-xs text-[var(--zo-text)] outline-none focus:border-[var(--zo-teal)]";
const MAPPINGS_PER_PAGE = 25;
type MappingFilter = "all" | ClientMapRow["link_confidence"];

export function filterClientMapRows(rows: ClientMapRow[], query: string, filter: MappingFilter) {
  const normalizedQuery = query.trim().toLowerCase();
  return rows.filter((row) => {
    const matchesFilter = filter === "all" || row.link_confidence === filter;
    const haystack = [row.tag_code, row.client_name, row.current_am, ...row.qb_customer_names, ...row.teamwork_company_names]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return matchesFilter && (!normalizedQuery || haystack.includes(normalizedQuery));
  });
}

export function paginateClientMapRows(rows: ClientMapRow[], page: number, pageSize = MAPPINGS_PER_PAGE) {
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const safePage = Math.min(Math.max(1, page), pageCount);
  return { page: safePage, pageCount, rows: rows.slice((safePage - 1) * pageSize, safePage * pageSize) };
}

function MappingPagination({ page, pageCount, onChange }: { page: number; pageCount: number; onChange: (page: number) => void }) {
  if (pageCount <= 1) return null;
  return <nav className="mapping-pagination" aria-label="Client mapping pages"><span>Page {page} of {pageCount}</span><div><button type="button" onClick={() => onChange(page - 1)} disabled={page === 1} aria-label="Previous mapping page"><ChevronLeft size={15} /></button><button type="button" onClick={() => onChange(page + 1)} disabled={page === pageCount} aria-label="Next mapping page"><ChevronRight size={15} /></button></div></nav>;
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
  selected,
  onSelect,
  onUpdate,
  onAccept,
  onReject,
  onDelete,
}: {
  row: ClientMapRow;
  busy: boolean;
  selected: boolean;
  onSelect: (id: string) => void;
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
      <tr>
        <td><input type="radio" aria-label={`Select ${row.client_name}`} checked={selected} onChange={() => onSelect(row.id)} /></td>
        <td><input aria-label="Tag code" className={INPUT} value={draft.tag_code} onChange={(event) => setDraft({ ...draft, tag_code: event.target.value })} /></td>
        <td><input aria-label="Client name" className={INPUT} value={draft.client_name} onChange={(event) => setDraft({ ...draft, client_name: event.target.value })} /></td>
        <td><input aria-label="Account manager" className={INPUT} value={draft.current_am ?? ""} onChange={(event) => setDraft({ ...draft, current_am: event.target.value })} /></td>
        <td><input aria-label="Status" className={INPUT} value={draft.status ?? ""} onChange={(event) => setDraft({ ...draft, status: event.target.value })} /></td>
        <td colSpan={3}>
          <label className="inline-flex items-center gap-2 text-xs text-[var(--zo-text-secondary)]">
            <input type="checkbox" checked={draft.is_internal} onChange={(event) => setDraft({ ...draft, is_internal: event.target.checked })} />
            Internal
          </label>
        </td>
        <td>
          <div className="flex gap-2">
            <button type="button" className="qb-retry" disabled={busy || !draft.tag_code.trim() || !draft.client_name.trim()} onClick={() => void save()}>Save</button>
            <button type="button" className="qb-more" onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </td>
      </tr>
    );
  }

  return (
    <tr>
      <td><input type="radio" aria-label={`Select ${row.client_name}`} checked={selected} onChange={() => onSelect(row.id)} /></td>
      <td><span className="qb-tag !ml-0">{row.tag_code}</span></td>
      <td><span className="qb-name">{row.client_name}</span>{row.is_internal ? <span className="qb-tag">internal</span> : null}</td>
      <td>{row.current_am || "—"}</td>
      <td>{row.status || "—"}</td>
      <td>{row.qb_customer_names.join(", ") || "—"}</td>
      <td>{row.teamwork_company_names.join(", ") || "—"}</td>
      <td><Pill label={row.link_confidence} tone={tone(row.link_confidence)} /></td>
      <td>
        <div className="flex flex-wrap gap-2">
          {row.link_confidence === "suggested" ? <button type="button" className="qb-more !mt-0" disabled={busy} onClick={() => void onAccept(row.id)}>Accept</button> : null}
          {row.link_confidence !== "unmatched" ? <button type="button" className="qb-more !mt-0" disabled={busy} onClick={() => void onReject(row.id)}>Reject</button> : null}
          <button type="button" className="qb-more !mt-0" disabled={busy} onClick={() => setEditing(true)}>Edit</button>
          <button
            type="button"
            className="qb-more !mt-0 text-[var(--zo-danger)]"
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
      <label className="grid gap-1 text-[11px] text-[var(--zo-text-muted)]">Tag<input className={INPUT} value={draft.tag_code} onChange={(event) => setDraft({ ...draft, tag_code: event.target.value })} required /></label>
      <label className="grid gap-1 text-[11px] text-[var(--zo-text-muted)]">Client<input className={INPUT} value={draft.client_name} onChange={(event) => setDraft({ ...draft, client_name: event.target.value })} required /></label>
      <label className="grid gap-1 text-[11px] text-[var(--zo-text-muted)]">AM<input className={INPUT} value={draft.current_am ?? ""} onChange={(event) => setDraft({ ...draft, current_am: event.target.value })} /></label>
      <label className="grid gap-1 text-[11px] text-[var(--zo-text-muted)]">Status<input className={INPUT} value={draft.status ?? ""} onChange={(event) => setDraft({ ...draft, status: event.target.value })} /></label>
      <label className="mb-2 inline-flex items-center gap-2 text-xs text-[var(--zo-text-secondary)]"><input type="checkbox" checked={draft.is_internal} onChange={(event) => setDraft({ ...draft, is_internal: event.target.checked })} />Internal</label>
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
  prefilledProjectId,
  prefilledSiteId,
}: {
  prefilledProjectId: string | null;
  prefilledSiteId: string | null;
}) {
  const map = useClientMap();
  const [projectId, setProjectId] = useState(prefilledProjectId ?? "");
  const [clientMapId, setClientMapId] = useState("");
  const [selectedRowId, setSelectedRowId] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<MappingFilter>("all");
  const [page, setPage] = useState(1);
  const [overrideError, setOverrideError] = useState<string | null>(null);
  const [overrideNotice, setOverrideNotice] = useState<string | null>(null);
  const selectedRow = map.rows.find((row) => row.id === selectedRowId);
  const siteId = prefilledSiteId?.trim() || null;
  const existingOverride = useMemo(
    () => map.jobOverrides.find((override) => override.site_id === siteId && override.project_id === Number(projectId)),
    [map.jobOverrides, projectId, siteId],
  );
  const resolvedClientMapId = clientMapId || existingOverride?.client_map_id || "";
  const selectedClient = map.rows.find((row) => row.id === resolvedClientMapId);
  const filteredRows = useMemo(() => filterClientMapRows(map.rows, query, filter), [filter, map.rows, query]);
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
    <TabsContent value="mapping" className="qb-view">
      <div className="qb-toolbar mapping-toolbar">
        <p className="qb-sync">
          <span className="qb-sync-dot" data-busy={map.busy ? "true" : undefined} aria-hidden />
          Review client relationships
          {map.lastLinkResult ? (
            <span className="qb-sync-meta">
              {map.lastLinkResult.confirmed} confirmed
              {typeof map.lastLinkResult.teamwork_tag === "number"
                ? ` · ${map.lastLinkResult.teamwork_tag} tag`
                : ""}
              {" · "}
              {map.lastLinkResult.suggested} suggested
            </span>
          ) : null}
        </p>
        <div className="qb-toolbar-actions">
          <button type="button" className="qb-retry" disabled={map.busy} onClick={() => void map.importSheet()}>Import from Tags</button>
          <button type="button" className="qb-retry" disabled={map.busy} onClick={() => void map.findLinks()}>Find links</button>
          <button type="button" className="qb-retry" disabled={map.loading || map.busy} onClick={() => void map.reload()}><RefreshCw size={13} aria-hidden />Refresh</button>
        </div>
      </div>

      {map.error ? <div className="qb-error"><p>{map.error}</p></div> : null}

      <Panel title="Client map" meta={`Showing ${pagedRows.rows.length} of ${filteredRows.length}${filteredRows.length !== map.rows.length ? ` · ${map.rows.length} total` : ""}`} action={<CreateClientForm busy={map.busy} onCreate={map.create} />}>
        <div className="mapping-controls">
          <label className="mapping-search"><Search size={15} aria-hidden /><span className="sr-only">Search client mappings</span><input value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder="Search client, tag, or linked company" /></label>
          <div className="mapping-filters" aria-label="Filter client mappings">{filters.map((item) => <button key={item.id} type="button" data-active={filter === item.id ? "true" : undefined} onClick={() => { setFilter(item.id); setPage(1); }}>{item.label}<span>{item.count}</span></button>)}</div>
        </div>
        {filteredRows.length ? (
          <div className="qb-tablewrap">
            <table className="qb-table mapping-table">
              <thead><tr><th><span className="sr-only">Select</span></th><th>Tag</th><th>Client</th><th>AM</th><th>Status</th><th>QuickBooks</th><th>Teamwork</th><th>Confidence</th><th>Actions</th></tr></thead>
              <tbody>
                {pagedRows.rows.map((row) => (
                  <ClientRow key={row.id} row={row} busy={map.busy} selected={row.id === selectedRowId} onSelect={setSelectedRowId} onUpdate={map.update} onAccept={map.accept} onReject={map.reject} onDelete={map.remove} />
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty>{map.rows.length ? "No client mappings match this review." : "No client mappings yet. Import Tags or add one above."}</Empty>}
        <MappingPagination page={pagedRows.page} pageCount={pagedRows.pageCount} onChange={setPage} />
      </Panel>

      <div className="qb-two">
        <Panel title="Unmatched Teamwork" meta={`${map.unmatched.teamwork.length}`}>
          {map.unmatched.teamwork.length ? <ul className="qb-bars">{map.unmatched.teamwork.map((item) => <li key={`${item.id}-${item.name}`}><button type="button" className="qb-more !mt-0" disabled={map.busy || !selectedRow} onClick={() => selectedRow && void map.attachTeamwork(selectedRow, item)}>Attach {item.name || `Company ${item.id}`}</button></li>)}</ul> : <Empty>All Teamwork companies are linked.</Empty>}
        </Panel>
        <Panel title="Unmatched QuickBooks" meta={`${map.unmatched.quickbooks.length}`}>
          {map.unmatched.quickbooks.length ? <ul className="qb-bars">{map.unmatched.quickbooks.map((item) => <li key={item.qbo_id}><button type="button" className="qb-more !mt-0" disabled={map.busy || !selectedRow} onClick={() => selectedRow && void map.attachQuickBooks(selectedRow, item)}>Attach {item.display_name}</button></li>)}</ul> : <Empty>All QuickBooks customers are linked.</Empty>}
        </Panel>
      </div>

      <Panel title="Job overrides" meta={`${map.jobOverrides.length}`}>
        <form className="mb-4 flex flex-wrap items-end gap-2" onSubmit={(event) => void addOverride(event)}>
          <div className="basis-full text-xs text-[var(--zo-text-secondary)]">
            {siteId
              ? `Teamwork workspace ready · resolve Project ${projectId || "—"} by selecting its mapped client.`
              : "Workspace context is unavailable. Open this from an Agency mapping action so the connected Teamwork workspace is included."}
          </div>
          <label className="grid gap-1 text-[11px] text-[var(--zo-text-muted)]">Project ID<input className={INPUT} type="number" value={projectId} onChange={(event) => setProjectId(event.target.value)} required /></label>
          <label className="grid gap-1 text-[11px] text-[var(--zo-text-muted)]">Mapped client<select aria-label="Mapped client" className={INPUT} value={resolvedClientMapId} onChange={(event) => { setClientMapId(event.target.value); setOverrideError(null); }} required><option value="">Choose a client</option>{map.rows.map((row) => <option key={row.id} value={row.id}>{row.tag_code} — {row.client_name}{row.qb_customer_names.length ? ` · ${row.qb_customer_names.join(", ")}` : " · no QuickBooks customer"}</option>)}</select></label>
          <button type="submit" className="qb-retry" disabled={map.busy || !siteId}>{existingOverride ? "Update override" : "Save override"}</button>
        </form>
        {overrideError ? <p className="qb-error mb-4" role="alert">{overrideError}</p> : null}
        {overrideNotice ? <p className="mb-4 text-xs text-[var(--zo-success)]" role="status">{overrideNotice}</p> : null}
        {map.jobOverrides.length ? (
          <div className="qb-tablewrap"><table className="qb-table"><thead><tr><th>Site</th><th>Project</th><th>Client</th><th>QuickBooks</th><th /></tr></thead><tbody>
            {map.jobOverrides.map((override) => {
              const client = map.rows.find((row) => row.id === override.client_map_id);
              return <tr key={override.id}><td>{override.site_id}</td><td>{override.project_id}</td><td>{client?.client_name || "—"}</td><td>{override.qb_customer_names.join(", ") || "—"}</td><td><button type="button" className="qb-more !mt-0 text-[var(--zo-danger)]" disabled={map.busy} onClick={() => void map.removeJobOverride(override.id)}>Delete</button></td></tr>;
            })}
          </tbody></table></div>
        ) : <Empty>No job-level overrides.</Empty>}
      </Panel>
    </TabsContent>
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
  const openMappingForProject = (projectId: string, siteId?: string | null) => {
    setMappingHandoff({ projectId, siteId: siteId ?? null });
    onViewChange("mapping");
  };
  return (
    <div className="qb-ledger">
      <Tabs value={view} onValueChange={(id) => onViewChange(parseAgencyView(id))} className="qb-tabs">
        <TabsList className="qb-tablist">
          <TabsTrigger value="jobs">Jobs</TabsTrigger>
          <TabsTrigger value="mapping">Mapping</TabsTrigger>
        </TabsList>
        <TabsContent value="jobs" className="qb-view">
          <AgencyJobsDemo onOpenMapping={openMappingForProject} />
        </TabsContent>
        {view === "mapping" ? <MappingView prefilledProjectId={mappingHandoff?.projectId ?? null} prefilledSiteId={mappingHandoff?.siteId ?? null} /> : null}
      </Tabs>
    </div>
  );
}
