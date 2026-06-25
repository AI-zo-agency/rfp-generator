"use client";

import Link from "next/link";
import { formatDate } from "@/lib/format";
import { STAGE_LABELS } from "@/lib/rfp-process";
import type { RfpRecord } from "@/types/rfp";
import { StatusBadge } from "./StatusBadge";

interface RecentRfpsTableProps {
  rfps: RfpRecord[];
  limit?: number;
}

export function RecentRfpsTable({ rfps, limit = 6 }: RecentRfpsTableProps) {
  const recent = [...rfps]
    .sort(
      (a, b) =>
        new Date(b.receivedDate).getTime() - new Date(a.receivedDate).getTime()
    )
    .slice(0, limit);

  return (
    <section className="zo-card overflow-hidden">
      <div className="flex items-center justify-between border-b border-zo-border px-8 py-6">
        <h2 className="font-heading text-xl font-bold text-foreground">
          Recent RFPs
        </h2>
        <Link
          href="/rfps"
          className="text-sm font-semibold text-zo-teal transition-colors duration-200 hover:text-zo-orange"
        >
          View all →
        </Link>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[800px]">
          <thead>
            <tr className="border-b border-zo-border text-left text-[11px] font-bold uppercase tracking-[0.12em] text-zo-text-muted">
              <th className="px-8 py-4">RFP Name</th>
              <th className="px-5 py-4">Role</th>
              <th className="px-5 py-4">Sector</th>
              <th className="px-5 py-4">Stage</th>
              <th className="px-5 py-4">Status</th>
              <th className="px-5 py-4" />
            </tr>
          </thead>
          <tbody>
            {recent.map((rfp) => (
              <tr
                key={rfp.id}
                className="group border-b border-zo-border/60 transition-colors duration-150 last:border-b-0 hover:bg-[var(--zo-hover-bg)]"
              >
                <td className="px-8 py-5">
                  <div className="flex items-start gap-4">
                    <Link
                      href={`/rfps/${rfp.id}`}
                      className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#ef5018]/15 text-[#ef5018] transition-smooth hover:bg-[#ef5018]/10"
                      aria-label={`Open ${rfp.title}`}
                    >
                      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                      </svg>
                    </Link>
                    <div className="min-w-0">
                      <Link
                        href={`/rfps/${rfp.id}`}
                        className="font-semibold text-foreground transition-colors hover:text-zo-orange"
                      >
                        {rfp.title}
                      </Link>
                      <p className="mt-1 text-xs font-medium uppercase tracking-wide text-zo-text-muted">
                        Created {formatDate(rfp.receivedDate)}
                      </p>
                      {rfp.pdfUrl && (
                        <a
                          href={rfp.pdfUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="mt-2 inline-flex text-xs font-semibold text-zo-teal hover:text-zo-orange"
                        >
                          View PDF →
                        </a>
                      )}
                    </div>
                  </div>
                </td>
                <td className="px-5 py-5 text-sm font-medium text-zo-text-secondary">
                  {rfp.contractRole === "prime" ? "Prime" : "Sub"}
                </td>
                <td className="px-5 py-5 text-sm text-zo-text-secondary">
                  {rfp.sector}
                </td>
                <td className="px-5 py-5 text-sm font-medium text-foreground">
                  {STAGE_LABELS[rfp.stage]}
                </td>
                <td className="px-5 py-5">
                  <StatusBadge status={rfp.status} />
                </td>
                <td className="px-5 py-5">
                  <button
                    type="button"
                    className="flex h-8 w-8 items-center justify-center rounded-lg text-zo-text-muted opacity-0 transition-opacity duration-150 group-hover:opacity-100 hover:bg-zo-warm-gray"
                    aria-label="Actions"
                  >
                    <svg className="h-5 w-5" fill="currentColor" viewBox="0 0 24 24">
                      <circle cx="12" cy="6" r="1.5" />
                      <circle cx="12" cy="12" r="1.5" />
                      <circle cx="12" cy="18" r="1.5" />
                    </svg>
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
