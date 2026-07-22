"use client";

import Link from "next/link";
import { useState, type ReactNode } from "react";
import { DeleteRfpButton } from "@/components/DeleteRfpButton";
import { GoNoGoAnalysisPanel } from "@/components/GoNoGoAnalysisPanel";
import { GoSign } from "@/components/GoSign";
import { MarkGoButton } from "@/components/MarkGoButton";
import { RunGoNoGoButton } from "@/components/RunGoNoGoButton";
import { PriorityBadge, StatusBadge } from "@/components/StatusBadge";
import { formatOverallGoScore } from "@/lib/format";
import type { GoNoGoAnalysis, RfpPriority, RfpStatus } from "@/types/rfp";

interface RfpGoNoGoControlsProps {
  rfpId: string;
  title: string;
  hasPdf: boolean;
  hasDescription: boolean;
  pdfUrl?: string | null;
  status: RfpStatus;
  priority: RfpPriority;
  stageLabel: string;
  dueLabel: ReactNode;
  fitScore: number | null;
  worthScore: number | null;
  goNoGo: GoNoGoAnalysis["recommendation"] | null;
  goNoGoAnalysis?: GoNoGoAnalysis | null;
  lastActivityNote?: string | null;
}

export function RfpGoNoGoControls({
  rfpId,
  title,
  hasPdf,
  hasDescription,
  pdfUrl,
  status,
  priority,
  stageLabel,
  dueLabel,
  fitScore,
  worthScore,
  goNoGo,
  goNoGoAnalysis,
  lastActivityNote,
}: RfpGoNoGoControlsProps) {
  const [analyzing, setAnalyzing] = useState(false);

  // While re-running, hide the previous analysis so stale GO scores never linger.
  const showAnalysis = Boolean(goNoGoAnalysis) && !analyzing;
  const displayFit = analyzing ? null : fitScore;
  const displayWorth = analyzing ? null : worthScore;
  const displayGoNoGo = analyzing ? null : goNoGo;
  const displayNote = analyzing ? null : lastActivityNote;
  const isGoRfp = displayGoNoGo === "go";

  return (
    <>
      <section className="zo-card p-8">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              {isGoRfp && <GoSign />}
              <StatusBadge status={analyzing ? "new" : status} />
              <PriorityBadge priority={priority} />
              <MarkGoButton rfpId={rfpId} current={displayGoNoGo} />
            </div>
            <dl className="grid gap-4 sm:grid-cols-3">
              <div>
                <dt className="text-xs font-bold uppercase tracking-wide text-zo-text-muted">
                  Stage
                </dt>
                <dd className="mt-1 font-semibold text-foreground">
                  {analyzing ? "Go / No-Go" : stageLabel}
                </dd>
              </div>
              <div>
                <dt className="text-xs font-bold uppercase tracking-wide text-zo-text-muted">
                  Due
                </dt>
                <dd className="mt-1 font-semibold text-foreground">{dueLabel}</dd>
              </div>
              <div>
                <dt className="text-xs font-bold uppercase tracking-wide text-zo-text-muted">
                  Overall Go Score
                </dt>
                <dd className="mt-1 font-semibold text-foreground">
                  {analyzing
                    ? "—"
                    : formatOverallGoScore(
                        displayFit,
                        displayWorth,
                        goNoGoAnalysis?.decisionMatrix
                      )}
                </dd>
              </div>
            </dl>
          </div>

          <div className="flex flex-wrap gap-3">
            <RunGoNoGoButton
              rfpId={rfpId}
              hasPdf={hasPdf}
              hasDescription={hasDescription}
              onLoadingChange={setAnalyzing}
            />
            {isGoRfp && (
              <Link href={`/proposals?rfp=${rfpId}`} className="zo-btn">
                Open in Proposals →
              </Link>
            )}
            {pdfUrl && (
              <a
                href={pdfUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="zo-btn secondary"
              >
                View RFP PDF →
              </a>
            )}
            <DeleteRfpButton rfpId={rfpId} title={title} />
          </div>
        </div>

        {displayNote && (
          <p className="mt-6 border-t border-zo-border pt-6 text-sm text-zo-text-secondary">
            <span className="font-semibold text-foreground">Latest: </span>
            {displayNote}
          </p>
        )}
      </section>

      {analyzing && (
        <section
          className="go-nogo-loading zo-card p-8"
          aria-busy="true"
          aria-live="polite"
        >
          <div className="flex flex-wrap items-center gap-5">
            <div className="go-nogo-loading-meter" aria-hidden>
              {[0, 1, 2, 3, 4].map((i) => (
                <span
                  key={i}
                  className="go-nogo-loading-bar"
                  style={{ animationDelay: `${i * 0.14}s` }}
                />
              ))}
              <span className="go-nogo-loading-scan" />
            </div>
            <div>
              <p className="text-sm font-semibold text-foreground">
                Running Go/No-Go analysis…
              </p>
              <p className="go-nogo-loading-steps mt-1.5 text-xs text-zo-text-muted">
                <span>Scoring matrix</span>
                <span>Checking KB</span>
                <span>Worth &amp; win path</span>
              </p>
            </div>
          </div>
        </section>
      )}

      {showAnalysis && goNoGoAnalysis && (
        <GoNoGoAnalysisPanel
          analysis={goNoGoAnalysis}
          fitScore={displayFit}
          worthScore={displayWorth}
          recommendation={displayGoNoGo}
        />
      )}
    </>
  );
}
