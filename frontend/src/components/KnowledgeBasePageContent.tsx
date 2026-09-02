"use client";

import { useCallback, useEffect, useState } from "react";
import {
  fetchCorrectionCount,
  KnowledgeBaseCorrectionsModal,
} from "@/components/KnowledgeBaseCorrectionsModal";
import { KnowledgeBaseGrid } from "@/components/KnowledgeBaseGrid";
import { UploadKnowledgeDocButton } from "@/components/UploadKnowledgeDocButton";
import type { KnowledgeBaseStatus } from "@/lib/knowledge-base-api";
import { kbBtnSecondary } from "@/lib/kb-brand";

interface PageStats {
  documentCount: number;
  categoryCount: number;
  loading: boolean;
}

export function KnowledgeBasePageContent() {
  const [correctionsOpen, setCorrectionsOpen] = useState(false);
  const [correctionCount, setCorrectionCount] = useState(0);
  const [reloadToken, setReloadToken] = useState(0);
  const [status, setStatus] = useState<KnowledgeBaseStatus | null>(null);
  const [stats, setStats] = useState<PageStats>({
    documentCount: 0,
    categoryCount: 0,
    loading: true,
  });

  const loadCorrectionCount = useCallback(async () => {
    setCorrectionCount(await fetchCorrectionCount());
  }, []);

  useEffect(() => {
    void loadCorrectionCount();
  }, [loadCorrectionCount, reloadToken]);

  const bumpDocuments = useCallback(() => {
    setReloadToken((token) => token + 1);
  }, []);

  const containerTag = status?.containerTag ?? "zo-agency";

  return (
    <div className="space-y-8">
      <header className="border-l-[5px] border-[#ef5018] pl-8">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <h1 className="font-heading text-3xl font-bold text-foreground md:text-4xl">
              Knowledge Base
            </h1>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <button
              type="button"
              className={kbBtnSecondary}
              onClick={() => setCorrectionsOpen(true)}
            >
              Add note
              {correctionCount > 0 ? (
                <span className="rounded-full bg-[rgba(239,80,24,0.18)] px-2 py-0.5 text-[10px] font-bold text-[#d44312]">
                  {correctionCount}
                </span>
              ) : null}
            </button>
            <UploadKnowledgeDocButton onUploaded={bumpDocuments} variant="brand" />
          </div>
        </div>

        <p className="mt-4 max-w-2xl text-base leading-relaxed text-zo-text-secondary">
          Upload verified agency documents directly to Supermemory — facts, case
          studies, bios, pricing, and won proposals. Nothing is stored locally.
        </p>

        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs">
          {status?.supermemoryConfigured ? (
            <span className="rounded-full bg-emerald-50 px-2.5 py-1 font-semibold text-emerald-700 ring-1 ring-emerald-200">
              Supermemory · {containerTag}
            </span>
          ) : (
            <span className="rounded-full bg-red-50 px-2.5 py-1 font-semibold text-red-700 ring-1 ring-red-200">
              Supermemory not configured
            </span>
          )}
          <span className="rounded-full bg-amber-50 px-2.5 py-1 font-semibold text-amber-800 ring-1 ring-amber-200">
            {status?.driveConnected ? "Drive connected" : "Drive not connected"}
          </span>
          {!stats.loading ? (
            <span className="text-zo-text-muted">
              <span className="font-semibold text-foreground">
                {stats.documentCount}
              </span>{" "}
              documents ·{" "}
              <span className="font-semibold text-foreground">
                {stats.categoryCount}
              </span>{" "}
              categories
            </span>
          ) : null}
        </div>
      </header>

      <KnowledgeBaseCorrectionsModal
        open={correctionsOpen}
        onClose={() => setCorrectionsOpen(false)}
        onChanged={() => {
          void loadCorrectionCount();
        }}
      />

      <KnowledgeBaseGrid
        reloadToken={reloadToken}
        onStatusChange={setStatus}
        onStatsChange={setStats}
      />
    </div>
  );
}
