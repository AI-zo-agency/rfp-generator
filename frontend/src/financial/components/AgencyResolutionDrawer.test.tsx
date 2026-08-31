import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import {
  AgencyResolutionDrawer,
  buildInvoiceResolution,
  containDrawerFocus,
  getReceivableFollowUpStatus,
  openAgencyMapping,
} from "./AgencyResolutionDrawer";
import type { AgencyAction } from "../lib/agency-action-queue";
import type { AgencyResolutionOption } from "../types/agency";

const invoiceAction = {
  id: "invoice:inv-9",
  kind: "invoice",
  priority: 3,
  title: "Reconcile invoice INV-9",
  detail: "Acme Studio",
  amount: 2400,
  invoiceId: "inv-9",
  source: {
    invoice_id: "inv-9",
    invoice_number: "INV-9",
    customer_id: "customer-4",
    customer_name: "Acme Studio",
    txn_date: "2026-08-01",
    due_date: "2026-08-15",
    total_amt: 2400,
    open_ar: 1800,
  },
} satisfies Extract<AgencyAction, { kind: "invoice" }>;

const options: AgencyResolutionOption[] = [{
  project_id: "project-7",
  project_name: "Website refresh",
  company_name: "Acme Studio",
  client_map_id: "map-42",
}];

describe("AgencyResolutionDrawer", () => {
  it("renders an invoice resolution dialog with its accessible controls", () => {
    const html = renderToStaticMarkup(
      createElement(AgencyResolutionDrawer, {
        action: invoiceAction,
        options,
        open: true,
        onOpenChange: vi.fn(),
        onResolveInvoice: vi.fn(),
        onOpenMapping: vi.fn(),
      }),
    );

    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-modal="true"');
    expect(html).toContain("Resolve invoice INV-9");
    expect(html).toContain("Acme Studio");
    expect(html).toContain("$2,400.00");
    expect(html).toContain("$1,800.00");
    expect(html).toContain('aria-label="Project to link invoice"');
    expect(html).toContain("Link invoice");
    expect(html).toContain("Mark internal revenue");
  });

  it("builds a linked invoice submission with the selected client map", () => {
    expect(buildInvoiceResolution(invoiceAction, options[0])).toEqual({
      invoice_id: "inv-9",
      resolution: "linked",
      project_id: "project-7",
      client_map_id: "map-42",
    });
  });

  it("builds an internal invoice submission without project or client map fields", () => {
    expect(buildInvoiceResolution(invoiceAction, options[0], "internal")).toEqual({
      invoice_id: "inv-9",
      resolution: "internal",
    });
  });

  it("rejects an invoice link without a selected project so the drawer can render its inline error", () => {
    expect(() => buildInvoiceResolution(invoiceAction, null)).toThrow("Choose a project before linking this invoice.");
  });

  it("contains Tab and Shift+Tab within the drawer controls", () => {
    const first = { focus: vi.fn() } as unknown as HTMLElement;
    const last = { focus: vi.fn() } as unknown as HTMLElement;
    const dialog = {
      querySelectorAll: () => [first, last],
      contains: (node: Node | null) => node === first || node === last,
    } as unknown as HTMLElement;
    const priorDocument = Object.getOwnPropertyDescriptor(globalThis, "document");

    try {
      Object.defineProperty(globalThis, "document", {
        configurable: true,
        value: { activeElement: last },
      });
      const forward = { key: "Tab", shiftKey: false, preventDefault: vi.fn() } as unknown as KeyboardEvent;
      containDrawerFocus(forward, dialog);
      expect(forward.preventDefault).toHaveBeenCalledOnce();
      expect(first.focus).toHaveBeenCalledOnce();

      Object.defineProperty(globalThis, "document", {
        configurable: true,
        value: { activeElement: first },
      });
      const backward = { key: "Tab", shiftKey: true, preventDefault: vi.fn() } as unknown as KeyboardEvent;
      containDrawerFocus(backward, dialog);
      expect(backward.preventDefault).toHaveBeenCalledOnce();
      expect(last.focus).toHaveBeenCalledOnce();
    } finally {
      if (priorDocument) Object.defineProperty(globalThis, "document", priorDocument);
      else delete (globalThis as { document?: Document }).document;
    }
  });

  it("keeps Tab on the dialog when its controls are unavailable during saving", () => {
    const dialog = {
      querySelectorAll: () => [],
      focus: vi.fn(),
    } as unknown as HTMLElement;
    const forwardTab = { key: "Tab", shiftKey: false, preventDefault: vi.fn() } as unknown as KeyboardEvent;
    const backwardTab = { key: "Tab", shiftKey: true, preventDefault: vi.fn() } as unknown as KeyboardEvent;

    containDrawerFocus(forwardTab, dialog);
    containDrawerFocus(backwardTab, dialog);

    expect(forwardTab.preventDefault).toHaveBeenCalledOnce();
    expect(backwardTab.preventDefault).toHaveBeenCalledOnce();
    expect(dialog.focus).toHaveBeenCalledTimes(2);
  });

  it("opens mapping actions in the existing Agency Mapping tab", () => {
    const onOpenMapping = vi.fn();
    const mappingAction: AgencyAction = {
      id: "mapping:project-7",
      kind: "mapping",
      priority: 1,
      title: "Map: Website refresh",
      detail: "Acme Studio",
      amount: 0,
      projectId: "project-7",
      source: {
        project_id: "project-7",
        job_label: "Web",
        project_name: "Website refresh",
        company_name: "Acme Studio",
        client_name: "Acme Studio",
        status: "active",
        health: "good",
        hours_mtd_minutes: 0,
        billed_ytd: null,
        open_ar: null,
        join: "needs mapping",
        client_map_id: null,
        link_confidence: null,
        via: null,
      },
    };

    const html = renderToStaticMarkup(
      createElement(AgencyResolutionDrawer, {
        action: mappingAction,
        options,
        open: true,
        onOpenChange: vi.fn(),
        onResolveInvoice: vi.fn(),
        onOpenMapping,
      }),
    );

    expect(html).toContain("Open Agency Mapping");
    const onOpenChange = vi.fn();
    openAgencyMapping(mappingAction, onOpenMapping, onOpenChange);
    expect(onOpenMapping).toHaveBeenCalledWith("project-7");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("renders contextual AR follow-up instead of a mapping action", () => {
    const receivableAction: AgencyAction = {
      id: "receivable:acme",
      kind: "receivable",
      priority: 2,
      title: "Collect: Acme Studio",
      detail: "Website refresh",
      amount: 1800,
      projectId: "project-7",
      source: {
        project_id: "project-7", job_label: "Web", project_name: "Website refresh", company_name: "Acme Studio", client_name: "Acme Studio",
        status: "active", health: "good", hours_mtd_minutes: 0, billed_ytd: 2400, open_ar: 1800,
        join: "confirmed", client_map_id: "map-42", link_confidence: "high", via: "client map",
      },
    };
    const html = renderToStaticMarkup(createElement(AgencyResolutionDrawer, {
      action: receivableAction, options, open: true, onOpenChange: vi.fn(), onResolveInvoice: vi.fn(), onOpenMapping: vi.fn(),
      receivableFollowUp: { note: "Left a voicemail", recorded: true }, onRecordReceivableFollowUp: vi.fn(),
    }));

    expect(html).toContain("Review receivable");
    expect(html).not.toContain("Collect: Acme Studio");
    expect(html).toContain("Recommended follow-up");
    expect(html).toContain("Contact Acme Studio");
    expect(html).toContain("Follow-up recorded");
    expect(html).toContain("Left a voicemail");
    expect(html).toContain("Record follow-up");
    expect(html).not.toContain("Open Agency Mapping");
  });

  it("labels a receivable as not reviewed until a session follow-up is recorded", () => {
    expect(getReceivableFollowUpStatus(undefined)).toBe("Not reviewed");
    expect(getReceivableFollowUpStatus({ note: "Emailed accounts payable", recorded: true })).toBe("Follow-up recorded");
  });
});
