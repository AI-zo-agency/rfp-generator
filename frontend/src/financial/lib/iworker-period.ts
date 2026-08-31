const SHEET_DATE = /^([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})$/;

const MONTH_INDEX: Record<string, number> = {
  january: 0,
  jan: 0,
  february: 1,
  feb: 1,
  march: 2,
  mar: 2,
  april: 3,
  apr: 3,
  may: 4,
  june: 5,
  jun: 5,
  july: 6,
  jul: 6,
  august: 7,
  aug: 7,
  september: 8,
  sep: 8,
  sept: 8,
  october: 9,
  oct: 9,
  november: 10,
  nov: 10,
  december: 11,
  dec: 11,
};

/** Parse iWorker sheet dates such as "May 13, 2026" (UTC midnight, no TZ drift). */
export function parseSheetDate(s: string): Date | null {
  const text = (s ?? "").trim();
  if (!text) return null;

  const match = SHEET_DATE.exec(text);
  if (!match) return null;

  const month = MONTH_INDEX[match[1].toLowerCase()];
  if (month === undefined) return null;

  const day = Number(match[2]);
  const year = Number(match[3]);
  const parsed = new Date(Date.UTC(year, month, day));
  if (
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month ||
    parsed.getUTCDate() !== day
  ) {
    return null;
  }
  return parsed;
}

function toIsoDay(value: Date): string {
  return value.toISOString().slice(0, 10);
}

/** True when `dateStr` falls in the inclusive Mon–Sun (or month) ISO range. */
export function entryDateInPeriod(dateStr: string, start: string, end: string): boolean {
  const parsed = parseSheetDate(dateStr);
  if (!parsed) return false;
  const iso = toIsoDay(parsed);
  return iso >= start && iso <= end;
}
