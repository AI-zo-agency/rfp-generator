export function formatDate(dateStr: string) {
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function formatCurrency(value: number | null) {
  if (value === null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);
}

export function daysUntil(dateStr: string) {
  const diff = Math.ceil(
    (new Date(dateStr).getTime() - Date.now()) / (1000 * 60 * 60 * 24)
  );
  if (diff < 0) return { label: "Overdue", urgent: true };
  if (diff === 0) return { label: "Due today", urgent: true };
  if (diff === 1) return { label: "1 day left", urgent: true };
  if (diff <= 3) return { label: `${diff} days left`, urgent: true };
  return { label: `${diff} days left`, urgent: false };
}

export function formatRelativeTime(dateStr: string) {
  const diff = Date.now() - new Date(dateStr).getTime();
  const minutes = Math.floor(diff / (1000 * 60));
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "Yesterday";
  return `${days}d ago`;
}

export function isMissingScore(value: number | null | undefined): boolean {
  return value === null || value === undefined;
}

/** Single Go Score (0–5): average of fit + worth when both exist. */
export function computeGoScore(
  fitScore: number | null | undefined,
  worthScore: number | null | undefined
): number | null {
  if (isMissingScore(fitScore) && isMissingScore(worthScore)) return null;
  if (!isMissingScore(fitScore) && !isMissingScore(worthScore)) {
    return Math.round((fitScore! + worthScore!) / 2);
  }
  return fitScore ?? worthScore ?? null;
}

export function computeMatrixAverage(
  matrix: { score: number }[] | null | undefined
): number | null {
  if (!matrix?.length) return null;
  const scores = matrix
    .map((row) => row.score)
    .filter((score) => score >= 0 && score <= 5);
  if (!scores.length) return null;
  return Math.round((scores.reduce((sum, score) => sum + score, 0) / scores.length) * 10) / 10;
}

/** Overall Go Score: decision-matrix average when present, else fit/worth average. */
export function computeOverallGoScore(
  fitScore: number | null | undefined,
  worthScore: number | null | undefined,
  decisionMatrix?: { score: number }[] | null
): number | null {
  const matrixAverage = computeMatrixAverage(decisionMatrix);
  if (matrixAverage !== null) return matrixAverage;
  return computeGoScore(fitScore, worthScore);
}

/**
 * Scores ≥ 3.0 cannot display as No-Go (pipeline go threshold).
 * Fixes stale analyses that stored recommendation=no_go beside Overall 3.8.
 */
export function alignGoNoGoRecommendation(
  recommendation: "go" | "no_go" | "review" | null | undefined,
  overallScore: number | null | undefined
): "go" | "no_go" | "review" | null {
  if (!recommendation) return null;
  if (
    recommendation === "no_go" &&
    overallScore !== null &&
    overallScore !== undefined &&
    overallScore >= 3
  ) {
    return "review";
  }
  return recommendation;
}

/** Rewrite a stale "NO-GO — …" summary lead when the badge was aligned to conditions. */
export function alignGoNoGoSummary(
  summary: string | null | undefined,
  recommendation: "go" | "no_go" | "review" | null | undefined
): string {
  const text = (summary || "").trim();
  if (!text) return "";
  if (recommendation !== "review" && recommendation !== "go") return text;
  return text.replace(/^\s*NO[\s-]?GO\s*[—\-–:]?\s*/i, "GO WITH CONDITIONS — ");
}

export function formatOverallGoScore(
  fitScore: number | null | undefined,
  worthScore: number | null | undefined,
  decisionMatrix?: { score: number }[] | null
): string {
  const score = computeOverallGoScore(fitScore, worthScore, decisionMatrix);
  if (score === null) return "Pending";
  const scaled = score > 5 ? Math.round((score / 20) * 10) / 10 : score;
  return `${scaled} / 5`;
}

export function formatGoScore(
  fitScore: number | null | undefined,
  worthScore: number | null | undefined
): string {
  const score = computeGoScore(fitScore, worthScore);
  if (score === null) return "Pending";
  const scaled = score > 5 ? Math.round((score / 20) * 10) / 10 : score;
  return `${scaled} / 5`;
}

/** @deprecated Use formatGoScore — kept for callers migrating off dual display */
export function formatFitWorthScores(
  fitScore: number | null | undefined,
  worthScore: number | null | undefined
): string {
  return formatGoScore(fitScore, worthScore);
}
