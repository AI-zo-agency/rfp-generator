"use client";

import { useEffect, useRef, useState } from "react";
import { Building2, CircleDollarSign, Link2, MapPinned, X } from "lucide-react";

import type { AgencyAction } from "../lib/agency-action-queue";
import type { AgencyResolutionOption } from "../types/agency";

export interface InvoiceResolutionPayload {
  invoice_id: string;
  resolution: "linked" | "internal";
  project_id?: string;
  client_map_id?: string;
}

export interface ReceivableFollowUp {
  note: string;
  recorded: boolean;
}

interface Props {
  action: AgencyAction | null;
  options: AgencyResolutionOption[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onResolveInvoice: (payload: InvoiceResolutionPayload) => Promise<void> | void;
  onOpenMapping: (projectId: string) => void;
  receivableFollowUp?: ReceivableFollowUp;
  onRecordReceivableFollowUp?: (action: Extract<AgencyAction, { kind: "receivable" }>, note: string) => void;
}

function usd(value: number): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
}

function date(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(`${value}T00:00:00`);
  return Number.isNaN(parsed.getTime())
    ? value
    : new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(parsed);
}

/** Keeps the API payload shape aligned with the invoice resolution route. */
export function buildInvoiceResolution(
  action: Extract<AgencyAction, { kind: "invoice" }>,
  option: AgencyResolutionOption | null,
  resolution: "linked" | "internal" = "linked",
): InvoiceResolutionPayload {
  if (resolution === "internal") {
    return { invoice_id: action.invoiceId, resolution: "internal" };
  }
  if (!option) throw new Error("Choose a project before linking this invoice.");
  return {
    invoice_id: action.invoiceId,
    resolution: "linked",
    project_id: option.project_id,
    ...(option.client_map_id ? { client_map_id: option.client_map_id } : {}),
  };
}

/** Opens the existing Mapping tab; the drawer never fetches or mutates mappings itself. */
export function openAgencyMapping(
  action: Exclude<AgencyAction, { kind: "invoice" }>,
  onOpenMapping: (projectId: string) => void,
  onOpenChange: (open: boolean) => void,
) {
  onOpenMapping(action.projectId);
  onOpenChange(false);
}

export function getReceivableFollowUpStatus(followUp: ReceivableFollowUp | undefined) {
  return followUp?.recorded ? "Follow-up recorded" : "Not reviewed";
}

const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "select:not([disabled])",
  "input:not([disabled])",
  "textarea:not([disabled])",
  "a[href]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

/** Keep keyboard navigation inside the modal instead of sending it to the obscured page. */
export function containDrawerFocus(event: KeyboardEvent, dialog: HTMLElement | null) {
  if (event.key !== "Tab" || !dialog) return;
  const focusable = [...dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)];
  if (!focusable.length) {
    event.preventDefault();
    dialog.focus();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const active = document.activeElement;

  if (event.shiftKey && (active === first || !dialog.contains(active))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (active === last || !dialog.contains(active))) {
    event.preventDefault();
    first.focus();
  }
}

export function AgencyResolutionDrawer({
  action,
  options,
  open,
  onOpenChange,
  onResolveInvoice,
  onOpenMapping,
  receivableFollowUp,
  onRecordReceivableFollowUp,
}: Props) {
  const drawer = useRef<HTMLElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const restoreFocus = useRef<HTMLElement | null>(null);
  const [selectedProject, setSelectedProject] = useState<{ actionId: string; projectId: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<{ actionId: string; message: string } | null>(null);
  const [followUpDraft, setFollowUpDraft] = useState(receivableFollowUp?.note ?? "");

  useEffect(() => {
    if (!open) return;
    restoreFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = requestAnimationFrame(() => drawer.current?.focus());
    return () => {
      cancelAnimationFrame(frame);
      restoreFocus.current?.focus();
    };
  }, [open]);

  useEffect(() => {
    if (open) body.current?.scrollTo({ top: 0 });
  }, [action?.id, open]);

  useEffect(() => {
    if (open) return;
    restoreFocus.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !loading) onOpenChange(false);
      else containDrawerFocus(event, drawer.current);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [loading, onOpenChange, open]);

  if (!open || !action) return null;

  const close = () => {
    if (!loading) onOpenChange(false);
  };
  const invoice = action.kind === "invoice" ? action : null;
  const receivableAction = action.kind === "receivable" ? action : null;
  const mappingAction = action.kind === "invoice" || action.kind === "receivable" ? null : action;
  const selectedProjectId = selectedProject?.actionId === action.id ? selectedProject.projectId : "";
  const selected = options.find((option) => option.project_id === selectedProjectId) ?? null;

  const resolve = async (resolution: "linked" | "internal") => {
    if (!invoice || loading) return;
    setError(null);
    try {
      const payload = buildInvoiceResolution(invoice, selected, resolution);
      setLoading(true);
      await onResolveInvoice(payload);
      onOpenChange(false);
    } catch (caught) {
      setError({
        actionId: invoice.id,
        message: caught instanceof Error ? caught.message : "Could not resolve this invoice.",
      });
    } finally {
      setLoading(false);
    }
  };
  const recordReceivableFollowUp = () => {
    if (!receivableAction || !onRecordReceivableFollowUp || !followUpDraft.trim()) return;
    onRecordReceivableFollowUp(receivableAction, followUpDraft.trim());
  };

  return (
    <>
      <button
        type="button"
        className="agency-drawer-scrim"
        aria-label="Close resolution drawer"
        onClick={close}
        disabled={loading}
      />
      <aside
        ref={drawer}
        className="agency-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="agency-drawer-title"
        tabIndex={-1}
      >
        <header className="agency-drawer-head">
          <span className="agency-drawer-mark" aria-hidden>
            {invoice ? <Link2 size={16} /> : receivableAction ? <CircleDollarSign size={16} /> : <MapPinned size={16} />}
          </span>
          <div>
            <h2 id="agency-drawer-title">
              {invoice ? `Resolve invoice ${invoice.source.invoice_number || invoice.invoiceId}` : receivableAction ? "Review receivable" : "Review project mapping"}
            </h2>
            <p>{receivableAction ? "Review and document the outstanding balance" : action.title}</p>
          </div>
          <button type="button" className="agency-drawer-close" onClick={close} disabled={loading} aria-label="Close resolution drawer">
            <X size={17} aria-hidden />
          </button>
        </header>

        <div ref={body} className="agency-drawer-body">
          {invoice ? (
            <>
              <dl className="agency-drawer-details">
                <div><dt>Customer</dt><dd>{invoice.source.customer_name || invoice.source.customer_id || "Unknown customer"}</dd></div>
                <div><dt>Total</dt><dd>{usd(invoice.source.total_amt)}</dd></div>
                <div><dt>Open AR</dt><dd>{usd(invoice.source.open_ar)}</dd></div>
                <div><dt>Due</dt><dd>{date(invoice.source.due_date)}</dd></div>
              </dl>

              <label className="agency-drawer-field" htmlFor="agency-resolution-project">
                <span>Link to project</span>
                <select
                  id="agency-resolution-project"
                  aria-label="Project to link invoice"
                  value={selectedProjectId}
                  onChange={(event) => setSelectedProject({ actionId: invoice.id, projectId: event.target.value })}
                  disabled={loading}
                >
                  <option value="">Choose a project…</option>
                  {options.map((option) => (
                    <option key={option.project_id} value={option.project_id}>
                      {option.project_name} · {option.company_name}
                    </option>
                  ))}
                </select>
              </label>
              {selected?.client_map_id ? <p className="agency-drawer-hint">Uses linked client map {selected.client_map_id}.</p> : null}
            </>
          ) : receivableAction ? (
            <section className="agency-drawer-evidence" aria-label="Receivable details">
              <p className="agency-drawer-eyebrow">Accounts receivable</p>
              <h3>{receivableAction.source.client_name || receivableAction.source.company_name || "Client follow-up"}</h3>
              <dl className="agency-drawer-details">
                <div><dt>Client</dt><dd>{receivableAction.source.client_name || receivableAction.source.company_name || "Unknown client"}</dd></div>
                <div><dt>Open amount</dt><dd>{usd(receivableAction.amount)}</dd></div>
                <div><dt>Related job</dt><dd>{receivableAction.source.project_name || receivableAction.source.job_label || "—"}</dd></div>
                <div><dt>Recommended follow-up</dt><dd>Contact {receivableAction.source.client_name || receivableAction.source.company_name || "the client"} about the outstanding balance.</dd></div>
              </dl>
              <p className="agency-drawer-hint"><strong>{getReceivableFollowUpStatus(receivableFollowUp)}</strong> · Session-only review note; it is not sent to QuickBooks or the client.</p>
              <label className="agency-drawer-field" htmlFor="agency-receivable-follow-up">
                <span>Follow-up note</span>
                <textarea id="agency-receivable-follow-up" value={followUpDraft} onChange={(event) => setFollowUpDraft(event.target.value)} placeholder="Record what you reviewed or plan to do" />
              </label>
            </section>
          ) : mappingAction ? (
            <section className="agency-drawer-evidence" aria-label="Mapping evidence">
              <p className="agency-drawer-eyebrow">Evidence</p>
              <h3>{mappingAction.source.project_name || mappingAction.source.job_label}</h3>
              <dl className="agency-drawer-details">
                <div><dt>Client</dt><dd>{mappingAction.source.company_name || mappingAction.source.client_name || "—"}</dd></div>
                <div><dt>Join status</dt><dd>{mappingAction.source.join}</dd></div>
                <div><dt>Confidence</dt><dd>{mappingAction.source.link_confidence || "Unconfirmed"}</dd></div>
                <div><dt>Matched via</dt><dd>{mappingAction.source.via || "No match evidence"}</dd></div>
              </dl>
              <p className="agency-drawer-hint">Continue in Agency Mapping to review the matching evidence and save a project mapping.</p>
            </section>
          ) : null}
          {error?.actionId === action.id ? <p className="agency-drawer-error" role="alert">{error.message}</p> : null}
        </div>

        <footer className="agency-drawer-actions">
          {invoice ? (
            <>
              <button type="button" className="agency-drawer-secondary" onClick={() => void resolve("internal")} disabled={loading}>
                <Building2 size={15} aria-hidden />
                {loading ? "Saving…" : "Mark internal revenue"}
              </button>
              <button type="button" className="agency-drawer-primary" onClick={() => void resolve("linked")} disabled={loading || !selected}>
                <Link2 size={15} aria-hidden />
                {loading ? "Saving…" : "Link invoice"}
              </button>
            </>
          ) : receivableAction ? (
            <button type="button" className="agency-drawer-primary" onClick={recordReceivableFollowUp} disabled={loading || !followUpDraft.trim()}>
              <CircleDollarSign size={15} aria-hidden />
              Record follow-up
            </button>
          ) : mappingAction ? (
            <button type="button" className="agency-drawer-primary" onClick={() => openAgencyMapping(mappingAction, onOpenMapping, onOpenChange)} disabled={loading}>
              <MapPinned size={15} aria-hidden />
              Open Agency Mapping
            </button>
          ) : null}
        </footer>
      </aside>
    </>
  );
}
