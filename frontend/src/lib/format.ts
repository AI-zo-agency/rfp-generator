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
  const hours = Math.floor(diff / (1000 * 60 * 60));
  if (hours < 1) return "Just now";
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
    return Math.round((fitScore + worthScore) / 2);
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

export function formatOverallGoScore(
  fitScore: number | null | undefined,
  worthScore: number | null | undefined,
  decisionMatrix?: { score: number }[] | null
): string {
  const score = computeOverallGoScore(fitScore, worthScore, decisionMatrix);
  if (score === null) return "Pending";
  return `${score} / 5`;
}

export function formatGoScore(
  fitScore: number | null | undefined,
  worthScore: number | null | undefined
): string {
  const score = computeGoScore(fitScore, worthScore);
  if (score === null) return "Pending";
  return `${score} / 5`;
}

/** @deprecated Use formatGoScore — kept for callers migrating off dual display */
export function formatFitWorthScores(
  fitScore: number | null | undefined,
  worthScore: number | null | undefined
): string {
  return formatGoScore(fitScore, worthScore);
}
