"use client";

import { useCallback, useEffect, useState } from "react";

import type {
  ClientMapCorePatch,
  ClientMapRow,
  JobOverride,
  LinkResult,
  UnmatchedResponse,
} from "../types/client-map";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";
const BASE = `${API_BASE}/api/v1/financials/client-map`;

const EMPTY_UNMATCHED: UnmatchedResponse = { teamwork: [], quickbooks: [] };

async function request<T>(path = "", init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: init?.body
      ? { "Content-Type": "application/json", ...init.headers }
      : init?.headers,
  });
  if (!response.ok) throw new Error(`Client map returned ${response.status}`);
  return response.json() as Promise<T>;
}

export function useClientMap() {
  const [rows, setRows] = useState<ClientMapRow[]>([]);
  const [unmatched, setUnmatched] = useState<UnmatchedResponse>(EMPTY_UNMATCHED);
  const [jobOverrides, setJobOverrides] = useState<JobOverride[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastLinkResult, setLastLinkResult] = useState<LinkResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextRows, nextUnmatched, nextOverrides] = await Promise.all([
        request<ClientMapRow[]>(),
        request<UnmatchedResponse>("/unmatched"),
        request<JobOverride[]>("/job-overrides"),
      ]);
      setRows(nextRows);
      setUnmatched(nextUnmatched);
      setJobOverrides(nextOverrides);
    } catch (requestError) {
      console.error("Client map load failed", { operation: "client_map_load", error: requestError });
      setError(requestError instanceof Error ? requestError.message : "Could not load client map");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Loading remote state on mount is the synchronization this effect owns.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const mutate = useCallback(
    async <T,>(operation: string, path: string, init: RequestInit): Promise<T | null> => {
      setBusy(true);
      setError(null);
      try {
        const result = await request<T>(path, init);
        await load();
        return result;
      } catch (requestError) {
        console.error("Client map mutation failed", {
          operation,
          error: requestError,
        });
        setError(requestError instanceof Error ? requestError.message : "Client map update failed");
        return null;
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const patch = useCallback(
    (id: string, body: Partial<ClientMapRow>) =>
      mutate<ClientMapRow>("client_map_patch", `/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    [mutate],
  );

  return {
    rows,
    unmatched,
    jobOverrides,
    loading,
    busy,
    error,
    lastLinkResult,
    reload: load,
    create: (body: ClientMapCorePatch) =>
      mutate<ClientMapRow>("client_map_create", "", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    update: (id: string, body: ClientMapCorePatch) => patch(id, body),
    accept: (id: string) => patch(id, { link_confidence: "confirmed" }),
    attachQuickBooks: (
      row: ClientMapRow,
      customer: UnmatchedResponse["quickbooks"][number],
    ) =>
      patch(row.id, {
        qb_customer_ids: [...new Set([...row.qb_customer_ids, customer.qbo_id])],
        qb_customer_names: [...new Set([...row.qb_customer_names, customer.display_name])],
        link_confidence: "confirmed",
        link_reason: "manual",
      }),
    attachTeamwork: (
      row: ClientMapRow,
      company: UnmatchedResponse["teamwork"][number],
    ) =>
      patch(row.id, {
        teamwork_company_ids:
          company.id === null
            ? row.teamwork_company_ids
            : [...new Set([...row.teamwork_company_ids, company.id])],
        teamwork_company_names: [...new Set([...row.teamwork_company_names, company.name])],
        link_confidence: "confirmed",
        link_reason: "manual",
      }),
    reject: (id: string) =>
      patch(id, {
        qb_customer_ids: [],
        qb_customer_names: [],
        link_confidence: "unmatched",
        link_reason: null,
      }),
    remove: (id: string) =>
      mutate<{ deleted: boolean }>("client_map_delete", `/${id}`, { method: "DELETE" }),
    importSheet: () =>
      mutate<Record<string, number>>("client_map_import", "/import-sheet", { method: "POST" }),
    findLinks: async () => {
      const result = await mutate<LinkResult>("client_map_link", "/link", {
        method: "POST",
        body: JSON.stringify({ include_ai: true }),
      });
      if (result) setLastLinkResult(result);
      return result;
    },
    addJobOverride: (body: Pick<JobOverride, "site_id" | "project_id" | "client_map_id">) =>
      mutate<JobOverride>("client_map_job_override_create", "/job-overrides", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    removeJobOverride: (id: string) =>
      mutate<{ deleted: boolean }>("client_map_job_override_delete", `/job-overrides/${id}`, {
        method: "DELETE",
      }),
  };
}
