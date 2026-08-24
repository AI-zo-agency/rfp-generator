/** Standing corrections — human notes that override knowledge-base documents. */

export interface KbCorrection {
  id: string;
  customId: string;
  title: string;
  note: string;
  createdAt: string;
  updatedAt: string;
  linkedDocumentId: string | null;
}

export function sortCorrections(rows: KbCorrection[]): KbCorrection[] {
  return [...rows].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export function correctionSummary(correction: KbCorrection): string {
  const note = correction.note.split("\n").join(" ").trim();
  return note || correction.title.trim();
}
