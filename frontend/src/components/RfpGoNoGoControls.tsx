"use client";

import { useCallback, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { BidScopePicker } from "@/components/BidScopePicker";
import { DeleteRfpButton } from "@/components/DeleteRfpButton";
import { GenerateProposalButton } from "@/components/GenerateProposalButton";
import { GoNoGoAnalysisPanel } from "@/components/GoNoGoAnalysisPanel";
import { GoSign } from "@/components/GoSign";
import { RunGoNoGoButton } from "@/components/RunGoNoGoButton";
import { PriorityBadge, StatusBadge } from "@/components/StatusBadge";
import { ZoAmuletLoader } from "@/components/ZoAmuletLoader";
import { formatOverallGoScore, alignGoNoGoRecommendation, computeOverallGoScore, alignGoNoGoSummary } from "@/lib/format";
import {
  maybeApplyPacketExhibitAfterFit,
  type PacketAnalyzeMeta,
} from "@/lib/rfp-packet-files";
import type { GoNoGoAnalysis, RfpPriority, RfpStatus } from "@/types/rfp";

const PACKET_POLL_MS = 2500;
const PACKET_MAX_WAIT_MS = 15 * 60 * 1000;

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

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
  selectedTracks?: string[];
  bidScopeLockedAt?: string | null;
  /** Server-seeded so the CTA paints correctly on first render. */
  hasProposal?: boolean;
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
  selectedTracks: selectedTracksProp = [],
  bidScopeLockedAt: bidScopeLockedAtProp = null,
  hasProposal = false,
}: RfpGoNoGoControlsProps) {
  const router = useRouter();
  const [analyzing, setAnalyzing] = useState(false);
  const [sectionRefreshNote, setSectionRefreshNote] = useState<string | null>(
    null
  );
  const [selectedTracks, setSelectedTracks] = useState(selectedTracksProp);
  const [bidScopeLockedAt, setBidScopeLockedAt] = useState(bidScopeLockedAtProp);

  const onBidScopeLocked = useCallback(
    (payload: { selectedTracks: string[]; bidScopeLockedAt: string | null }) => {
      setSelectedTracks(payload.selectedTracks);
      setBidScopeLockedAt(payload.bidScopeLockedAt);
    },
    [],
  );

  /** After cite-linked upload/delete, API already started Fit — poll, then refresh draft tabs if needed. */
  const onPacketAnalyzeStarted = useCallback(
    async (meta?: PacketAnalyzeMeta) => {
      setAnalyzing(true);
      setSectionRefreshNote(null);
      const started = Date.now();
      try {
        // Immediate status check so a 409 "already running" paints the loader
        // even when the job finishes before the first poll tick.
        let sawRunning = false;
        while (Date.now() - started < PACKET_MAX_WAIT_MS) {
          const res = await fetch(`/api/rfps/${rfpId}/analyze/status`, {
            cache: "no-store",
          });
          const data = (await res.json()) as { status?: string; error?: string };
          if (!res.ok) {
            throw new Error(data.error ?? "Status check failed");
          }
          if (data.status === "running" || data.status === "queued") {
            sawRunning = true;
            await sleep(PACKET_POLL_MS);
            continue;
          }
          if (data.status === "failed") {
            throw new Error(data.error ?? "Analysis failed");
          }
          // completed | idle — if we never saw running, Fit was already done
          // (stale 409); still fall through to draft apply when uploading.
          break;
        }
        if (
          meta?.action === "upload" &&
          meta.citeTarget &&
          !meta.uploadBlocked
        ) {
          setSectionRefreshNote(
            sawRunning
              ? "Checking whether this RFP’s draft needs section updates…"
              : "Fit was already finished — checking draft sections for this attachment…"
          );
          const outcome = await maybeApplyPacketExhibitAfterFit(
            rfpId,
            meta.citeTarget
          );
          if (outcome === "started" || outcome === "already_running") {
            setSectionRefreshNote(
              "Updated only the proposal tabs that depend on the new attachment (this RFP only)."
            );
          } else if (outcome === "skipped_no_draft") {
            setSectionRefreshNote(null);
          } else {
            setSectionRefreshNote(
              "Fit updated. Open the proposal and use Update affected sections if tabs still wait on the exhibit."
            );
          }
        }
        router.refresh();
      } catch {
        router.refresh();
      } finally {
        setAnalyzing(false);
      }
    },
    [rfpId, router]
  );

  // While re-running, hide the previous analysis so stale GO scores never linger.
  const showAnalysis = Boolean(goNoGoAnalysis) && !analyzing;
  const displayFit = analyzing ? null : fitScore;
  const displayWorth = analyzing ? null : worthScore;
  const overallForAlign = analyzing
    ? null
    : computeOverallGoScore(fitScore, worthScore, goNoGoAnalysis?.decisionMatrix);
  const displayGoNoGo = analyzing
    ? null
    : alignGoNoGoRecommendation(goNoGo, overallForAlign);
  const displayNote = analyzing
    ? null
    : alignGoNoGoSummary(lastActivityNote, displayGoNoGo);
  const isGoRfp = displayGoNoGo === "go";

  return (
    <>
      <div className="flex flex-wrap items-start justify-end gap-3">
        <GenerateProposalButton
          rfpId={rfpId}
          isGo={isGoRfp}
          hasProposal={hasProposal}
          disabled={!goNoGoAnalysis || analyzing}
          disabledReason={
            analyzing
              ? "Fit is updating with the new exhibit — wait for Go/No-Go to finish"
              : "Run the Go/No-Go analysis first"
          }
        />
        <RunGoNoGoButton
          rfpId={rfpId}
          hasPdf={hasPdf}
          hasDescription={hasDescription}
          hasAnalysis={Boolean(goNoGoAnalysis)}
          onLoadingChange={setAnalyzing}
        />
      </div>

      <section className="zo-card p-8">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              {isGoRfp && <GoSign />}
              <StatusBadge status={analyzing ? "new" : status} />
              <PriorityBadge priority={priority} />
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

          <div className="flex flex-wrap justify-end gap-3">
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
          className="zo-card flex flex-col items-center gap-4 p-10"
          aria-busy="true"
          aria-live="polite"
        >
          <ZoAmuletLoader label="Updating Fit from attachment" />
          <div className="text-center">
            <p className="text-sm font-semibold text-foreground">
              Updating Fit from the new attachment…
            </p>
            <p className="mt-1.5 text-xs text-zo-text-muted">
              Judges the attached file only · refreshes scores · does not re-read
              the whole RFP
            </p>
          </div>
        </section>
      )}

      {sectionRefreshNote && !analyzing ? (
        <div
          className="rounded-xl border border-zo-orange/40 bg-zo-orange/5 px-4 py-3 text-sm text-zo-text-secondary"
          role="status"
        >
          {sectionRefreshNote}
        </div>
      ) : null}

      {showAnalysis && goNoGoAnalysis && (
        <>
          <GoNoGoAnalysisPanel
            rfpId={rfpId}
            analysis={goNoGoAnalysis}
            fitScore={displayFit}
            worthScore={displayWorth}
            recommendation={displayGoNoGo}
            onPacketAnalyzeStarted={(meta) => {
              void onPacketAnalyzeStarted(meta);
            }}
          />
          <BidScopePicker
            rfpId={rfpId}
            analysis={goNoGoAnalysis}
            selectedTracks={selectedTracks}
            bidScopeLockedAt={bidScopeLockedAt}
            onLocked={onBidScopeLocked}
          />
        </>
      )}
    </>
  );
}
