"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { ProposalsWorkspace } from "@/components/ProposalsWorkspace";
import { ProposalsWorkspaceSkeleton } from "@/components/loading/ProposalsWorkspaceSkeleton";
import type { RfpRecord } from "@/types/rfp";

const GO_RFP_FETCH_TIMEOUT_MS = 0; // 0 = wait; no artificial abort

function filterGoRfps(all: RfpRecord[]): RfpRecord[] {
  return all.filter(
    (r) =>
      (r.goNoGo === "go" || r.goNoGo === "review") &&
      !["won", "lost", "passed", "submitted"].includes(r.status)
  );
}

async function fetchRfpById(id: string, signal?: AbortSignal): Promise<RfpRecord | null> {
  const res = await fetch(`/api/rfps/${encodeURIComponent(id)}`, {
    cache: "no-store",
    ...(signal ? { signal } : {}),
  });
  if (!res.ok) return null;
  const data = (await res.json()) as RfpRecord;
  return data?.id ? data : null;
}

function ProposalsWorkspaceClientInner() {
  const searchParams = useSearchParams();
  const rfpFromUrl = searchParams.get("rfp");

  const [goRfps, setGoRfps] = useState<RfpRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    const controller = new AbortController();
    const timer =
      GO_RFP_FETCH_TIMEOUT_MS > 0
        ? window.setTimeout(() => controller.abort(), GO_RFP_FETCH_TIMEOUT_MS)
        : null;

    let list: RfpRecord[] = [];
    let errorMessage: string | null = null;

    try {
      const res = await fetch("/api/rfps/list", {
        cache: "no-store",
        ...(GO_RFP_FETCH_TIMEOUT_MS > 0 ? { signal: controller.signal } : {}),
      });
      const body = await res.json();
      if (!res.ok) {
        errorMessage =
          (typeof body === "object" && body && "error" in body
            ? String((body as { error: string }).error)
            : null) || `Could not load RFP list (${res.status})`;
      } else if (Array.isArray(body)) {
        list = filterGoRfps(body as RfpRecord[]);
      }
    } catch (error) {
      errorMessage =
        error instanceof Error && error.name === "AbortError"
          ? "Loading Go RFPs timed out — the API may be busy generating another proposal."
          : error instanceof Error
            ? error.message
            : "Could not load Go RFPs.";
    } finally {
      if (timer != null) window.clearTimeout(timer);
    }

    if (rfpFromUrl && !list.some((r) => r.id === rfpFromUrl)) {
      try {
        const one = await fetchRfpById(rfpFromUrl);
        if (one) {
          list = [...list, one];
          errorMessage = null;
        }
      } catch {
        // keep list / error from main fetch
      }
    }

    setGoRfps(list);
    setLoadError(errorMessage);
    setLoading(false);
  }, [rfpFromUrl]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <div className="space-y-3">
        <p className="px-1 text-xs text-zo-text-muted" role="status">
          Loading Go RFPs… (if this takes long, generation may be using the API)
        </p>
        <ProposalsWorkspaceSkeleton />
      </div>
    );
  }

  if (loadError && goRfps.length === 0) {
    return (
      <section className="proposal-workspace-card p-8 text-center">
        <p className="text-sm font-semibold text-foreground">Could not load workspace</p>
        <p className="mx-auto mt-2 max-w-md text-sm text-zo-text-muted">{loadError}</p>
        <button type="button" className="zo-btn mt-6" onClick={() => void load()}>
          Retry
        </button>
        <p className="mt-4 text-xs text-zo-text-muted">
          <Link href="/rfps" className="text-zo-orange hover:underline">
            Browse RFP pipeline
          </Link>
        </p>
      </section>
    );
  }

  if (goRfps.length === 0) {
    return (
      <section className="proposal-workspace-card">
        <div className="flex flex-col items-center px-8 py-16 text-center">
          <p className="font-heading text-2xl font-bold text-foreground">No Go RFPs yet</p>
          <p className="mx-auto mt-3 max-w-md text-sm text-zo-text-muted">
            Mark an RFP as Go from the pipeline, then return here to draft proposals.
          </p>
          <Link href="/rfps" className="zo-btn mt-8">
            Browse RFPs →
          </Link>
        </div>
      </section>
    );
  }

  return (
    <>
      {loadError ? (
        <p className="mb-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          {loadError} — showing {goRfps.length} RFP(s) from URL or partial load.
        </p>
      ) : null}
      <ProposalsWorkspace goRfps={goRfps} />
    </>
  );
}

export function ProposalsWorkspaceClient() {
  return (
    <Suspense fallback={<ProposalsWorkspaceSkeleton />}>
      <ProposalsWorkspaceClientInner />
    </Suspense>
  );
}
