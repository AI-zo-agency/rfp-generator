import { notFound } from "next/navigation";
import { DashboardHeader } from "@/components/DashboardHeader";
import { RfpGoNoGoControls } from "@/components/RfpGoNoGoControls";
import { daysUntil, formatDate } from "@/lib/format";
import { describeRfpIntake } from "@/lib/rfp-intake";
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
  const intake = describeRfpIntake(rfp);

  return (
    <>
      <DashboardHeader
        title={rfp.title}
        subtitle={`${rfp.client} · ${rfp.sector} · ${rfp.location} — ${intake.method}, ${intake.syncedLabel}${intake.justwinDateLabel ? `, ${intake.justwinDateLabel}` : ""}`}
        showSync={false}
      />

      <RfpGoNoGoControls
        rfpId={rfp.id}
        title={rfp.title}
        hasPdf={hasRfpPdf(rfp)}
        hasDescription={Boolean(rfp.description?.trim())}
        pdfUrl={rfp.pdfUrl}
        status={rfp.status}
        priority={rfp.priority}
        stageLabel={STAGE_LABELS[rfp.stage]}
        dueLabel={
          <>
            {formatDate(rfp.dueDate)}
            <span
              className={`ml-2 text-xs ${
                due.urgent ? "text-zo-error" : "text-zo-text-muted"
              }`}
            >
              {due.label}
            </span>
          </>
        }
        fitScore={rfp.fitScore}
        worthScore={rfp.worthScore}
        goNoGo={rfp.goNoGo}
        goNoGoAnalysis={rfp.goNoGoAnalysis}
        lastActivityNote={rfp.lastActivityNote}
      />
    </>
  );
}
