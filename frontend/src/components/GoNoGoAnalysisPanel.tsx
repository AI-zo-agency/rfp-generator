import type { GoNoGoAnalysis } from "@/types/rfp";
import {
  computeOverallGoScore,
  isMissingScore,
} from "@/lib/format";
import { MarkdownReportBody } from "./MarkdownReportBody";
import { GoNoGoBadge } from "./StatusBadge";

interface GoNoGoAnalysisPanelProps {
  analysis: GoNoGoAnalysis;
  fitScore: number | null;
  worthScore: number | null;
  recommendation: GoNoGoAnalysis["recommendation"] | null;
}

function DimensionBlock({
  title,
  dimension,
}: {
  title: string;
  dimension: GoNoGoAnalysis["scopeMatch"];
}) {
  const distinctFlags = dimension.flags.filter(
    (flag) => flag.message.trim() !== dimension.summary.trim()
  );

  return (
    <div className="rounded-xl border border-zo-border p-4">
      <h3 className="font-heading text-sm font-bold text-foreground">{title}</h3>
      <p className="mt-2 text-sm text-zo-text-secondary">{dimension.summary}</p>
      {distinctFlags.length > 0 && (
        <ul className="mt-3 space-y-2">
          {distinctFlags.map((flag) => (
            <li
              key={`${flag.category}-${flag.message}`}
              className={`text-xs ${
                flag.severity === "critical"
                  ? "text-zo-error"
                  : flag.severity === "warning"
                    ? "text-zo-orange"
                    : "text-zo-text-muted"
              }`}
            >
              {flag.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function DeadlineBanner({ deadline }: { deadline: GoNoGoDeadlineInfo }) {
  const urgent = deadline.isPast || deadline.isToday;
  if (!urgent && deadline.daysRemaining !== null && deadline.daysRemaining > 7) {
    return null;
  }

  return (
    <div
      className={`rounded-xl border p-4 ${
        deadline.isPast
          ? "border-zo-error/40 bg-zo-error/5"
          : "border-zo-orange/40 bg-zo-orange/5"
      }`}
    >
      <h3
        className={`text-sm font-bold ${
          deadline.isPast ? "text-zo-error" : "text-zo-orange"
        }`}
      >
        {deadline.isPast ? "Proposal Deadline Passed" : "Deadline Approaching"}
      </h3>
      <p className="mt-2 text-sm text-zo-text-secondary">
        {deadline.note}
        {deadline.lateSubmissionDisqualifies && deadline.isPast && (
          <span className="mt-1 block font-medium text-zo-error">
            Late submissions are explicitly disqualified per the RFP.
          </span>
        )}
      </p>
    </div>
  );
}

type GoNoGoDeadlineInfo = NonNullable<GoNoGoAnalysis["deadline"]>;

function ActionFlagsPanel({ flags }: { flags: string[] }) {
  if (!flags.length) return null;

  return (
    <div className="rounded-xl border border-zo-teal/30 bg-zo-teal/5 p-5">
      <h3 className="font-heading text-sm font-bold uppercase tracking-wide text-zo-teal">
        Action Flags — Human Follow-Up Required
      </h3>
      <ul className="mt-3 space-y-2">
        {flags.map((flag) => (
          <li
            key={flag}
            className="rounded-lg border border-zo-teal/20 bg-white/60 px-3 py-2 text-sm text-zo-text-secondary"
          >
            {flag}
          </li>
        ))}
      </ul>
    </div>
  );
}

function StageOneReport({
  report,
  skipDecisionMatrix,
}: {
  report: string;
  skipDecisionMatrix?: boolean;
}) {
  const sections = report
    .trim()
    .split(/\n(?=## )/)
    .map((section) => section.trim())
    .filter(Boolean)
    .filter((section) => {
      if (!skipDecisionMatrix) return true;
      const heading = section.split("\n")[0]?.replace(/^##\s*/, "") ?? "";
      return !/GO\/NO-GO DECISION MATRIX/i.test(heading);
    });

  if (sections.length === 0) return null;

  return (
    <div className="space-y-4 border-t border-zo-border pt-6">
      <h3 className="font-heading text-lg font-bold text-foreground">
        Full Stage 1 Report
      </h3>
      {sections.map((section) => {
        const lines = section.split("\n");
        const heading = lines[0]?.replace(/^##\s*/, "") ?? "Section";
        const body = lines.slice(1).join("\n").trim();
        const isFinalRec = /FINAL RECOMMENDATION/i.test(heading);

        return (
          <div
            key={heading}
            className={`rounded-xl border p-5 ${
              isFinalRec ? "border-zo-orange/40 bg-zo-orange/5" : "border-zo-border"
            }`}
          >
            <h4 className="font-heading text-sm font-bold uppercase tracking-wide text-zo-orange">
              {heading}
            </h4>
            <div className="mt-3">
              <MarkdownReportBody body={body} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function DecisionMatrixTable({
  matrix,
}: {
  matrix: NonNullable<GoNoGoAnalysis["decisionMatrix"]>;
}) {
  const overall =
    Math.round(
      (matrix.reduce((sum, row) => sum + row.score, 0) / matrix.length) * 10
    ) / 10;

  return (
    <div className="rounded-xl border border-zo-border p-5">
      <h3 className="font-heading text-sm font-bold uppercase tracking-wide text-foreground">
        Go/No-Go Decision Matrix
      </h3>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[480px] text-left text-sm">
          <thead>
            <tr className="border-b border-zo-border text-xs uppercase tracking-wide text-zo-text-muted">
              <th className="pb-2 pr-4 font-bold">Dimension</th>
              <th className="pb-2 pr-4 font-bold">Score</th>
              <th className="pb-2 font-bold">Notes</th>
            </tr>
          </thead>
          <tbody>
            {matrix.map((row) => (
              <tr key={row.dimension} className="border-b border-zo-border/60">
                <td className="py-3 pr-4 font-medium text-foreground">
                  {row.dimension}
                </td>
                <td className="py-3 pr-4 font-heading font-bold text-foreground">
                  {row.score}
                  <span className="ml-1 text-xs font-normal text-zo-text-muted">
                    / 5
                  </span>
                </td>
                <td className="py-3 text-zo-text-secondary">{row.notes}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-4 text-sm text-zo-text-secondary">
        <span className="font-semibold text-foreground">Overall Go Score</span>{" "}
        — average of matrix dimensions:{" "}
        <span className="font-heading font-bold text-foreground">
          {overall} / 5
        </span>
      </p>
    </div>
  );
}

function providerLabel(provider: string | undefined): string | null {
  if (!provider) return null;
  if (provider === "content-gate") {
    return "Blocked — PDF text could not be extracted for scoring";
  }
  if (provider === "local-fallback") {
    return "Rules-based response (LLM unavailable)";
  }
  return `Analyzed via ${provider}`;
}

function detectNeedsInput(analysis: GoNoGoAnalysis): boolean {
  if (analysis.insufficientData) return true;
  return (
    analysis.recommendation == null &&
    isMissingScore(analysis.fitScore) &&
    (analysis.clarifyingQuestions?.length ?? 0) > 0
  );
}

export function GoNoGoAnalysisPanel({
  analysis,
  fitScore,
  worthScore,
  recommendation,
}: GoNoGoAnalysisPanelProps) {
  const needsInput = detectNeedsInput(analysis);
  const overallGoScore = computeOverallGoScore(
    fitScore,
    worthScore,
    analysis.decisionMatrix
  );
  const scoresPending = needsInput || overallGoScore === null;
  const hasMatrix = (analysis.decisionMatrix?.length ?? 0) > 0;
  const actionFlags = analysis.actionFlags ?? [];
  const conditionsTitle =
    recommendation === "no_go"
      ? "No-Go Notes & Override Conditions"
      : "Go With Conditions";

  return (
    <section className="zo-card space-y-6 p-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-[11px] uppercase tracking-[0.34em] text-zo-orange">
            Stage 1 — Fit Analysis
          </p>
          <h2 className="font-heading mt-2 text-2xl text-foreground">
            {needsInput ? "More RFP Content Needed" : "Go/No-Go Results"}
          </h2>
          {analysis.provider && (
            <p className="mt-1 text-xs text-zo-text-muted">
              {providerLabel(analysis.provider)}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-end gap-4">
          {!scoresPending && !isMissingScore(fitScore) && (
            <div className="text-right">
              <p className="text-xs font-bold uppercase tracking-wide text-zo-text-muted">
                AI Fit Score
              </p>
              <p className="font-heading text-xl font-bold text-foreground">
                {fitScore}
                <span className="ml-1 text-sm font-normal text-zo-text-muted">
                  / 5
                </span>
              </p>
            </div>
          )}
          {!scoresPending && !isMissingScore(worthScore) && (
            <div className="text-right">
              <p className="text-xs font-bold uppercase tracking-wide text-zo-text-muted">
                Worth It Score
              </p>
              <p className="font-heading text-xl font-bold text-foreground">
                {worthScore}
                <span className="ml-1 text-sm font-normal text-zo-text-muted">
                  / 5
                </span>
              </p>
            </div>
          )}
          <div className="text-right">
            <p className="text-xs font-bold uppercase tracking-wide text-zo-text-muted">
              Overall Go Score
            </p>
            <p className="font-heading text-2xl font-bold text-foreground">
              {scoresPending ? "Pending" : overallGoScore}
              {!scoresPending && (
                <span className="ml-1 text-sm font-normal text-zo-text-muted">
                  / 5
                </span>
              )}
            </p>
            {!scoresPending && hasMatrix && (
              <p className="mt-1 text-[11px] text-zo-text-muted">
                Matrix average
              </p>
            )}
          </div>
          {!needsInput && recommendation && (
            <GoNoGoBadge recommendation={recommendation} />
          )}
        </div>
      </div>

      {!needsInput && analysis.deadline && (
        <DeadlineBanner deadline={analysis.deadline} />
      )}

      <p className="text-sm leading-relaxed text-zo-text-secondary">
        {analysis.summary}
      </p>

      {!needsInput && actionFlags.length > 0 && (
        <ActionFlagsPanel flags={actionFlags} />
      )}

      {!needsInput && hasMatrix && (
        <DecisionMatrixTable matrix={analysis.decisionMatrix!} />
      )}

      {analysis.stageOneReport && !needsInput && (
        <StageOneReport
          report={analysis.stageOneReport}
          skipDecisionMatrix={hasMatrix}
        />
      )}

      {needsInput && (analysis.clarifyingQuestions?.length ?? 0) > 0 && (
        <div className="rounded-xl border border-zo-teal/30 bg-zo-teal/5 p-4">
          <h3 className="text-sm font-bold text-zo-teal">
            Add this before re-running analysis
          </h3>
          <ol className="mt-2 list-decimal space-y-2 pl-5 text-sm text-zo-text-secondary">
            {analysis.clarifyingQuestions!.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ol>
        </div>
      )}

      {!needsInput && analysis.criticalGaps.length > 0 && (
        <div className="rounded-xl border border-zo-error/30 bg-zo-error/5 p-4">
          <h3 className="text-sm font-bold text-zo-error">Critical Gaps</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-zo-text-secondary">
            {analysis.criticalGaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </div>
      )}

      {!needsInput && analysis.conditions.length > 0 && (
        <div className="rounded-xl border border-zo-orange/30 bg-zo-orange/5 p-4">
          <h3 className="text-sm font-bold text-zo-orange">{conditionsTitle}</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-zo-text-secondary">
            {analysis.conditions.map((condition) => (
              <li key={condition}>{condition}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid gap-4 border-t border-zo-border pt-6 lg:grid-cols-2">
        <DimensionBlock title="Scope vs. What We Do" dimension={analysis.scopeMatch} />
        <DimensionBlock title="Sector Match" dimension={analysis.sectorMatch} />
        <DimensionBlock title="Compliance & Eligibility" dimension={analysis.compliance} />
        <DimensionBlock title="Team & Resources" dimension={analysis.teamMatch} />
      </div>

      {analysis.evaluations && analysis.evaluations.length > 0 && (
        <details className="rounded-xl border border-zo-border p-4">
          <summary className="cursor-pointer font-heading text-sm font-bold text-foreground">
            Evaluation Questions ({analysis.evaluations.length})
          </summary>
          <div className="mt-4 space-y-3">
            {analysis.evaluations.map((item) => (
              <div
                key={item.id}
                className="rounded-xl border border-zo-border p-4"
              >
                <p className="text-xs font-bold uppercase tracking-wide text-zo-text-muted">
                  {item.id.replaceAll("_", " ")}
                </p>
                <p className="mt-1 text-sm font-semibold text-foreground">
                  {item.question}
                </p>
                <p className="mt-2 text-sm text-zo-text-secondary">{item.answer}</p>
                {item.impact && (
                  <p className="mt-1 text-xs text-zo-text-muted">{item.impact}</p>
                )}
              </div>
            ))}
          </div>
        </details>
      )}
    </section>
  );
}
