"use client";

import { useState, type FormEvent } from "react";
import { RefreshCw } from "lucide-react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Empty, Panel, Pill } from "./qb-ui";
import { useClientMap } from "../lib/use-client-map";
import { parseAgencyView, type AgencyViewId } from "../lib/financial-tab";
import type { ClientMapCorePatch, ClientMapRow } from "../types/client-map";
import "./QuickBooksLedger.css";

const INPUT =
  "h-8 min-w-24 rounded-md border border-[var(--zo-border)] bg-[var(--zo-card-bg)] px-2 text-xs text-[var(--zo-text)] outline-none focus:border-[var(--zo-teal)]";

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
      <tr>
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

function MappingView() {
  const map = useClientMap();
  const [siteId, setSiteId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [clientMapId, setClientMapId] = useState("");

  const addOverride = async (event: FormEvent) => {
    event.preventDefault();
    if (!siteId.trim() || !projectId) return;
    await map.addJobOverride({
      site_id: siteId.trim(),
      project_id: Number(projectId),
      client_map_id: clientMapId || null,
    });
    setProjectId("");
  };

  if (map.loading && !map.rows.length) {
    return <div className="qb-skel" aria-label="Loading client mapping"><div className="qb-skel-block h-24" /><div className="qb-skel-block h-72" /></div>;
  }

  return (
    <TabsContent value="mapping" className="qb-view">
      <div className="qb-toolbar">
        <p className="qb-sync">
          <span className="qb-sync-dot" data-busy={map.busy ? "true" : undefined} aria-hidden />
          {map.rows.length} client mappings
          {map.lastLinkResult ? <span className="qb-sync-meta">{map.lastLinkResult.confirmed} confirmed · {map.lastLinkResult.suggested} suggested</span> : null}
        </p>
        <div className="qb-toolbar-actions">
          <button type="button" className="qb-retry" disabled={map.busy} onClick={() => void map.importSheet()}>Import from Tags</button>
          <button type="button" className="qb-retry" disabled={map.busy} onClick={() => void map.findLinks()}>Find links</button>
          <button type="button" className="qb-retry" disabled={map.loading || map.busy} onClick={() => void map.reload()}><RefreshCw size={13} aria-hidden />Refresh</button>
        </div>
      </div>

      {map.error ? <div className="qb-error"><p>{map.error}</p></div> : null}

      <Panel title="Client map" meta={`${map.rows.length} rows`} action={<CreateClientForm busy={map.busy} onCreate={map.create} />}>
        {map.rows.length ? (
          <div className="qb-tablewrap">
            <table className="qb-table">
              <thead><tr><th>Tag</th><th>Client</th><th>AM</th><th>Status</th><th>QuickBooks</th><th>Teamwork</th><th>Confidence</th><th>Actions</th></tr></thead>
              <tbody>
                {map.rows.map((row) => (
                  <ClientRow key={row.id} row={row} busy={map.busy} onUpdate={map.update} onAccept={map.accept} onReject={map.reject} onDelete={map.remove} />
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty>No client mappings yet. Import Tags or add one above.</Empty>}
      </Panel>

      <div className="qb-two">
        <Panel title="Unmatched Teamwork" meta={`${map.unmatched.teamwork.length}`}>
          {map.unmatched.teamwork.length ? <ul className="qb-bars">{map.unmatched.teamwork.map((item) => <li key={`${item.id}-${item.name}`}><span className="qb-name">{item.name || `Company ${item.id}`}</span></li>)}</ul> : <Empty>All Teamwork companies are linked.</Empty>}
        </Panel>
        <Panel title="Unmatched QuickBooks" meta={`${map.unmatched.quickbooks.length}`}>
          {map.unmatched.quickbooks.length ? <ul className="qb-bars">{map.unmatched.quickbooks.map((item) => <li key={item.qbo_id}><span className="qb-name">{item.display_name}</span></li>)}</ul> : <Empty>All QuickBooks customers are linked.</Empty>}
        </Panel>
      </div>

      <Panel title="Job overrides" meta={`${map.jobOverrides.length}`}>
        <form className="mb-4 flex flex-wrap items-end gap-2" onSubmit={(event) => void addOverride(event)}>
          <label className="grid gap-1 text-[11px] text-[var(--zo-text-muted)]">Site ID<input className={INPUT} value={siteId} onChange={(event) => setSiteId(event.target.value)} required /></label>
          <label className="grid gap-1 text-[11px] text-[var(--zo-text-muted)]">Project ID<input className={INPUT} type="number" value={projectId} onChange={(event) => setProjectId(event.target.value)} required /></label>
          <label className="grid gap-1 text-[11px] text-[var(--zo-text-muted)]">Client<select className={INPUT} value={clientMapId} onChange={(event) => setClientMapId(event.target.value)}><option value="">No client</option>{map.rows.map((row) => <option key={row.id} value={row.id}>{row.tag_code} — {row.client_name}</option>)}</select></label>
          <button type="submit" className="qb-retry" disabled={map.busy}>Add override</button>
        </form>
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
  return (
    <div className="qb-ledger">
      <Tabs value={view} onValueChange={(id) => onViewChange(parseAgencyView(id))} className="qb-tabs">
        <TabsList className="qb-tablist">
          <TabsTrigger value="mapping">Mapping</TabsTrigger>
          <TabsTrigger value="jobs">Jobs</TabsTrigger>
        </TabsList>
        {view === "mapping" ? <MappingView /> : null}
        <TabsContent value="jobs" className="qb-view">
          <Panel title="Agency Jobs"><Empty>Agency Jobs ships in Phase B — use Mapping to confirm links.</Empty></Panel>
        </TabsContent>
      </Tabs>
    </div>
  );
}
