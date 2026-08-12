"use client";

import type { CaseStudyMatchResult, CaseStudyMatchStudy } from "@/lib/proposal-api";

type StudyListItem = CaseStudyMatchStudy | { title: string };

function studyHeading(study: StudyListItem): string {
  if ("displayName" in study && typeof study.displayName === "string" && study.displayName) {
    return study.displayName;
  }
  return study.title;
}

function fitBadgeClass(label: string): string {
  if (label === "strong_fit") {
    return "border-emerald-500/35 bg-emerald-500/10 text-emerald-800";
  }
  return "border-amber-500/35 bg-amber-500/10 text-amber-900";
}

function matchSectionTitle(result: CaseStudyMatchResult): string {
  if (result.matchQuality === "strong") return "Strong fits";
  if (result.matchQuality === "closest") return "Closest matches";
  return "Matches";
}

export function CaseStudyMatchModal({
  open,
  onClose,
  result,
  loading,
  error,
}: {
  open: boolean;
  onClose: () => void;
  result: CaseStudyMatchResult | null;
  loading: boolean;
  error: string | null;
}) {
  if (!open) return null;

  const hasStudies =
    (result?.studies.length ?? 0) > 0 || (result?.selectedTitles.length ?? 0) > 0;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="case-study-match-title"
    >
      <button
        type="button"
        className="absolute inset-0 bg-black/40"
        aria-label="Close case study matches"
        onClick={onClose}
      />
      <div className="relative z-[1] flex max-h-[min(90vh,720px)] w-full max-w-2xl flex-col rounded-2xl border border-zo-border bg-white shadow-xl">
        <div className="flex items-start justify-between gap-4 border-b border-zo-border px-5 py-4">
          <div>
            <p
              id="case-study-match-title"
              className="text-lg font-semibold text-foreground"
            >
              Case study matches
            </p>
            {result?.message ? (
              <p className="mt-1 text-sm text-zo-text-secondary">{result.message}</p>
            ) : null}
            {result?.matchQuality === "closest" ? (
              <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-950">
                No case study in the KB fully proves these capabilities. Closest
                matches are shown for review — treat as adjacent work, not proof.
              </p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-zo-text-muted hover:bg-zo-border/40"
            aria-label="Close"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="custom-scrollbar flex-1 overflow-y-auto px-5 py-4">
          {loading ? (
            <p className="text-sm text-zo-text-secondary">
              Matching KB case studies to this RFP… (may take 1–2 minutes)
            </p>
          ) : null}
          {error ? (
            <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
              {error}
            </p>
          ) : null}

          {result && !loading ? (
            <div className="space-y-5">
              {hasStudies ? (
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-wider text-zo-text-muted">
                    {matchSectionTitle(result)} ({result.studies.length || result.selectedTitles.length})
                  </p>
                  <ul className="mt-2 space-y-2">
                    {(result.studies.length > 0
                      ? result.studies
                      : result.selectedTitles.map((title) => ({ title }))
                    ).map((study: StudyListItem) => (
                      <li
                        key={study.title}
                        className="rounded-xl border border-zo-border/80 bg-white px-3 py-3"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium text-foreground">
                            {studyHeading(study)}
                          </span>
                          {"fitLabel" in study && study.fitLabel ? (
                            <span
                              className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${fitBadgeClass(
                                study.fitLabel
                              )}`}
                            >
                              {study.fitLabel.replace("_", " ")}
                            </span>
                          ) : null}
                          {"fitScore" in study && study.fitScore && study.fitScore > 0 ? (
                            <span className="text-xs text-zo-text-muted">
                              score {study.fitScore.toFixed(2)}
                            </span>
                          ) : null}
                        </div>
                        {"capability" in study && study.capability ? (
                          <p className="mt-1 text-xs text-zo-text-secondary">
                            Closest for: {study.capability}
                          </p>
                        ) : null}
                        {"matchedTerms" in study && study.matchedTerms && study.matchedTerms.length > 0 ? (
                          <p className="mt-1 text-xs text-zo-text-muted">
                            Terms: {study.matchedTerms.join(", ")}
                          </p>
                        ) : null}
                        {"excerpt" in study && study.excerpt ? (
                          <p className="mt-2 text-sm leading-relaxed text-zo-text-secondary line-clamp-4">
                            {study.excerpt}
                          </p>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <p className="text-sm text-zo-text-secondary">
                  No case studies returned from the knowledge base for this RFP.
                </p>
              )}

              {result.gaps.length > 0 ? (
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-wider text-amber-800">
                    What we don&apos;t have (gaps)
                  </p>
                  <ul className="mt-2 space-y-2">
                    {result.gaps.map((gap) => (
                      <li
                        key={gap.capability}
                        className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950"
                      >
                        <span className="font-medium">{gap.capability}</span>
                        {gap.closestDisplayName || gap.closestTitle ? (
                          <p className="mt-1.5 text-xs">
                            <span className="font-semibold">Closest:</span>{" "}
                            {gap.closestDisplayName || gap.closestTitle}
                            {gap.closestScore && gap.closestScore > 0
                              ? ` (score ${gap.closestScore.toFixed(2)})`
                              : ""}
                          </p>
                        ) : null}
                        {gap.closestExcerpt ? (
                          <p className="mt-1 text-xs leading-relaxed text-amber-900/90 line-clamp-3">
                            {gap.closestExcerpt}
                          </p>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {result.prefetchedAt ? (
                <p className="text-xs text-zo-text-muted">
                  Saved to research cache — Start from Case Studies can use these
                  {result.matchQuality === "closest" ? " after Sonja review" : ""}.
                </p>
              ) : null}
            </div>
          ) : null}
        </div>

        <div className="border-t border-zo-border px-5 py-3">
          <button type="button" className="zo-btn secondary w-full" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
