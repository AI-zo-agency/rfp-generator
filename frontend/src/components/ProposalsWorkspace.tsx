"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { daysUntil, formatDate } from "@/lib/format";
import { STAGE_LABELS } from "@/lib/rfp-process";
import type { RfpRecord } from "@/types/rfp";
import { GoSign } from "./GoSign";
import { ProposalDraftWorkspace } from "./ProposalDraftWorkspace";

interface ProposalsWorkspaceProps {
  goRfps: RfpRecord[];
}

export function ProposalsWorkspace({ goRfps }: ProposalsWorkspaceProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const rfpFromUrl = searchParams.get("rfp");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (rfpFromUrl && goRfps.some((r) => r.id === rfpFromUrl)) {
      setSelectedId(rfpFromUrl);
      return;
    }
    setSelectedId((current) => {
      if (current && goRfps.some((r) => r.id === current)) {
        return current;
      }
      return goRfps[0]?.id ?? null;
    });
  }, [rfpFromUrl, goRfps]);

  const selectRfp = useCallback(
    (id: string) => {
      setSelectedId(id);
      const params = new URLSearchParams(searchParams.toString());
      params.set("rfp", id);
      router.replace(`/proposals?${params.toString()}`, { scroll: false });
    },
    [router, searchParams]
  );

  const selectedRfp = useMemo(
    () => goRfps.find((r) => r.id === selectedId) ?? goRfps[0] ?? null,
    [goRfps, selectedId]
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return goRfps;
    return goRfps.filter(
      (r) =>
        r.title.toLowerCase().includes(q) ||
        r.client.toLowerCase().includes(q) ||
        r.location.toLowerCase().includes(q)
    );
  }, [goRfps, query]);

  if (goRfps.length === 0) {
    return (
      <section className="proposal-workspace-card">
        <div className="flex flex-col items-center px-8 py-16 text-center">
          <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-[#ef5018]/12 text-[#ef5018]">
            <svg
              className="h-8 w-8"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={1.5}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0115.75 21H5.25A2.25 2.25 0 013 18.75V8.25A2.25 2.25 0 015.25 6H10"
              />
            </svg>
          </div>
          <p className="font-heading mt-6 text-2xl font-bold text-foreground">
            No Go RFPs yet
          </p>
          <p className="mx-auto mt-3 max-w-md text-sm leading-relaxed text-zo-text-muted">
            Mark an RFP as Go from the pipeline, then return here to draft
            proposals with custom outlines and generated content.
          </p>
          <Link href="/rfps" className="zo-btn mt-8">
            Browse RFPs →
          </Link>
        </div>
      </section>
    );
  }

  return (
    <div className="grid items-start gap-6 xl:grid-cols-[300px_minmax(0,1fr)] xl:gap-5">
      <aside className="proposal-go-sidebar flex flex-col overflow-hidden xl:sticky xl:top-24 xl:max-h-[calc(100vh-7rem)]">
        <div className="border-b border-zo-border/80 bg-[#fafbfc] px-5 py-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-[#ef5018]">
                Go RFPs
              </p>
              <p className="mt-1 text-sm font-medium text-zo-text-secondary">
                {goRfps.length} approved to bid
              </p>
            </div>
            <span className="flex h-8 min-w-8 items-center justify-center rounded-full bg-[#ef5018] px-2 text-xs font-bold text-white shadow-[0_8px_20px_rgba(239,80,24,0.25)]">
              {goRfps.length}
            </span>
          </div>
          <div className="relative mt-4">
            <svg
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zo-text-muted"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z"
              />
            </svg>
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search Go RFPs…"
              className="zo-input w-full rounded-xl py-2.5 pl-10 pr-3 text-sm outline-none transition-smooth focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10"
            />
          </div>
        </div>

        <ul className="custom-scrollbar flex-1 divide-y divide-zo-border/50 overflow-y-auto">
          {filtered.length === 0 ? (
            <li className="px-5 py-10 text-center text-sm text-zo-text-muted">
              No matches for &ldquo;{query}&rdquo;
            </li>
          ) : (
            filtered.map((rfp) => {
              const active = selectedRfp?.id === rfp.id;
              const due = daysUntil(rfp.dueDate);
              return (
                <li key={rfp.id}>
                  <button
                    type="button"
                    onClick={() => selectRfp(rfp.id)}
                    className={`flex w-full items-start gap-3 px-5 py-4 text-left transition-smooth hover:bg-[var(--zo-hover-bg)] ${
                      active ? "proposal-go-item-active" : ""
                    }`}
                  >
                    <GoSign className="mt-0.5 h-7 w-7 shrink-0 text-[9px]" />
                    <div className="min-w-0 flex-1">
                      <p
                        className={`text-sm font-semibold leading-snug ${
                          active ? "text-zo-orange" : "text-foreground"
                        }`}
                      >
                        {rfp.title}
                      </p>
                      <p className="mt-1 truncate text-xs text-zo-text-muted">
                        {rfp.client}
                        {rfp.location ? ` · ${rfp.location}` : ""}
                      </p>
                      <div className="mt-2.5 flex flex-wrap items-center gap-2">
                        <span className="rounded-md border border-zo-border bg-white px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-zo-text-secondary">
                          {STAGE_LABELS[rfp.stage]}
                        </span>
                        <span
                          className={`text-[11px] font-medium ${
                            due.urgent ? "text-zo-error" : "text-zo-text-muted"
                          }`}
                        >
                          {formatDate(rfp.dueDate)}
                        </span>
                      </div>
                    </div>
                  </button>
                </li>
              );
            })
          )}
        </ul>
      </aside>

      <div className="min-w-0">
        {selectedRfp && (
          <ProposalDraftWorkspace key={selectedRfp.id} rfp={selectedRfp} />
        )}
      </div>
    </div>
  );
}
