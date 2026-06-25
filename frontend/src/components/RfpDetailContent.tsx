import Link from "next/link";
import { notFound } from "next/navigation";
import { DashboardHeader } from "@/components/DashboardHeader";
import { DeleteRfpButton } from "@/components/DeleteRfpButton";
import { GoNoGoAnalysisPanel } from "@/components/GoNoGoAnalysisPanel";
import { GoSign } from "@/components/GoSign";
import { MarkGoButton } from "@/components/MarkGoButton";
import { RunGoNoGoButton } from "@/components/RunGoNoGoButton";
import { PriorityBadge, StatusBadge } from "@/components/StatusBadge";
import { daysUntil, formatCurrency, formatDate, formatOverallGoScore } from "@/lib/format";
import { STAGE_LABELS } from "@/lib/rfp-process";
import { hasRfpPdf } from "@/lib/rfp-pdf";
import { getRfpById } from "@/lib/rfp-service";

interface RfpDetailContentProps {
  id: string;
}

export async function RfpDetailContent({ id }: RfpDetailContentProps) {
  const rfp = await getRfpById(id);

  if (!rfp) {
    notFound();
  }

  const due = daysUntil(rfp.dueDate);
  const isGoRfp = rfp.goNoGo === "go";

  return (
    <>
      <DashboardHeader
        title={rfp.title}
        subtitle={`${rfp.client} · ${rfp.sector} · ${rfp.location}`}
        showSync={false}
      />

      <section className="zo-card p-8">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              {isGoRfp && <GoSign />}
              <StatusBadge status={rfp.status} />
              <PriorityBadge priority={rfp.priority} />
              <MarkGoButton rfpId={rfp.id} current={rfp.goNoGo} />
            </div>
            <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <dt className="text-xs font-bold uppercase tracking-wide text-zo-text-muted">
                  Stage
                </dt>
                <dd className="mt-1 font-semibold text-foreground">
                  {STAGE_LABELS[rfp.stage]}
                </dd>
              </div>
              <div>
                <dt className="text-xs font-bold uppercase tracking-wide text-zo-text-muted">
                  Due
                </dt>
                <dd className="mt-1 font-semibold text-foreground">
                  {formatDate(rfp.dueDate)}
                  <span
                    className={`ml-2 text-xs ${
                      due.urgent ? "text-zo-error" : "text-zo-text-muted"
                    }`}
                  >
                    {due.label}
                  </span>
                </dd>
              </div>
              <div>
                <dt className="text-xs font-bold uppercase tracking-wide text-zo-text-muted">
                  Overall Go Score
                </dt>
                <dd className="mt-1 font-semibold text-foreground">
                  {formatOverallGoScore(
                    rfp.fitScore,
                    rfp.worthScore,
                    rfp.goNoGoAnalysis?.decisionMatrix
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-xs font-bold uppercase tracking-wide text-zo-text-muted">
                  Est. Value
                </dt>
                <dd className="mt-1 font-semibold text-foreground">
                  {formatCurrency(rfp.estimatedValue)}
                </dd>
              </div>
            </dl>
          </div>

          <div className="flex flex-wrap gap-3">
            <RunGoNoGoButton
              rfpId={rfp.id}
              hasPdf={hasRfpPdf(rfp)}
              hasDescription={Boolean(rfp.description?.trim())}
            />
            {isGoRfp && (
              <Link href={`/proposals?rfp=${rfp.id}`} className="zo-btn">
                Open in Proposals →
              </Link>
            )}
            {rfp.pdfUrl && (
              <a
                href={rfp.pdfUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="zo-btn secondary"
              >
                View RFP PDF →
              </a>
            )}
            <DeleteRfpButton rfpId={rfp.id} title={rfp.title} />
          </div>
        </div>

        {rfp.lastActivityNote && (
          <p className="mt-6 border-t border-zo-border pt-6 text-sm text-zo-text-secondary">
            <span className="font-semibold text-foreground">Latest: </span>
            {rfp.lastActivityNote}
          </p>
        )}
      </section>

      {rfp.goNoGoAnalysis && (
        <GoNoGoAnalysisPanel
          analysis={rfp.goNoGoAnalysis}
          fitScore={rfp.fitScore}
          worthScore={rfp.worthScore}
          recommendation={rfp.goNoGo}
        />
      )}
    </>
  );
}
