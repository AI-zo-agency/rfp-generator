/**
 * Pure derivation for the Teamwork dashboard.
 *
 * Everything the UI computes lives here so it can be tested without a DOM.
 * Components in components/teamwork/ stay presentational.
 */

const MS_PER_DAY = 86_400_000;

/**
 * Parse a YYYY-MM-DD (or full ISO) string to UTC midnight.
 *
 * The API sends calendar dates with no timezone. `new Date("2026-08-18")` is
 * UTC midnight, which renders as the 17th anywhere west of Greenwich — so the
 * date component is pulled out by hand and compared in UTC throughout.
 */
export function toUTCDay(value?: string | null): number | null {
  if (!value) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return null;
  return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

/** Whole days from today to the due date. Negative means overdue. */
export function daysUntil(dueDate: string | null | undefined, todayISO: string): number | null {
  const due = toUTCDay(dueDate);
  const today = toUTCDay(todayISO);
  if (due === null || today === null) return null;
  return Math.round((due - today) / MS_PER_DAY);
}

export type DueTone = "late" | "soon" | "later" | "none";

export interface DueDescriptor {
  label: string;
  days: number | null;
  tone: DueTone;
}

/** A due date as a reader sees it: how far away, and whether to worry. */
export function describeDue(
  dueDate: string | null | undefined,
  todayISO: string,
): DueDescriptor {
  const days = daysUntil(dueDate, todayISO);
  if (days === null) return { label: "—", days: null, tone: "none" };
  if (days < 0) return { label: `${Math.abs(days)}d late`, days, tone: "late" };
  if (days === 0) return { label: "Today", days, tone: "soon" };
  if (days <= 3) return { label: `in ${days}d`, days, tone: "soon" };
  return { label: `in ${days}d`, days, tone: "later" };
}

export function hoursLabel(minutes: number): string {
  const hours = (minutes || 0) / 60;
  if (!hours) return "0h";
  return `${hours.toFixed(hours >= 10 ? 0 : 1)}h`;
}

export function billablePct(billableMinutes: number, totalMinutes: number): number {
  if (!totalMinutes) return 0;
  return Math.round((billableMinutes / totalMinutes) * 100);
}

function joinBase(baseUrl: string | null | undefined, path: string): string | null {
  if (!baseUrl) return null;
  return `${baseUrl.replace(/\/+$/, "")}${path}`;
}

export function projectUrl(baseUrl: string | null | undefined, projectId: string): string | null {
  if (!projectId) return null;
  return joinBase(baseUrl, `/app/projects/${projectId}/tasks`);
}

export function taskUrl(baseUrl: string | null | undefined, taskId: string): string | null {
  if (!taskId) return null;
  return joinBase(baseUrl, `/app/tasks/${taskId}`);
}
