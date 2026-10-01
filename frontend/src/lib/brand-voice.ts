export type BrandVoiceRevision = {
  id: string;
  label: string;
  sha256: string;
  notes: string;
  createdBy: string;
  createdAt: string;
  size: number;
  isActive: boolean;
};

export type BrandVoiceRevisionList = {
  activeId: string;
  enabled: boolean;
  revisions: BrandVoiceRevision[];
};

export type RevisionRef = { id: string; label: string };
export type ProposalVoiceRev = { pinned: RevisionRef | null; active: RevisionRef };

/** "2026-09_zo_Brand_and_Writing_Standards_rev7.md" -> "rev 7" */
export function suggestRevisionLabel(filename: string): string {
  const match = /rev[\s_-]*(\d+)/i.exec(filename);
  return match ? `rev ${match[1]}` : "";
}

export function shortHash(sha: string): string {
  return sha.slice(0, 8);
}

/** The revision a proposal is written under: its pin, else the current default. */
export function pinSelection(pin: ProposalVoiceRev): string {
  return pin.pinned?.id ?? pin.active.id;
}

/** Select options: the listed revisions, plus the pin if the list no longer returns it. */
export function voiceOptions(data: BrandVoiceRevisionList, pin: ProposalVoiceRev): RevisionRef[] {
  const options = data.revisions.map(({ id, label }) => ({ id, label }));
  const current = pin.pinned ?? pin.active;
  return options.some((o) => o.id === current.id) ? options : [...options, current];
}

function nonEmpty(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** Readable message from a FastAPI or proxy error body. */
export function errorMessage(data: unknown, status: number): string {
  const body = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  const { detail } = body;
  if (nonEmpty(detail)) return detail as string;
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{ loc: ["body", "label"], msg: "Field required" }]
    const first = (detail[0] ?? {}) as { loc?: unknown; msg?: unknown };
    const msg = nonEmpty(first.msg);
    if (msg) {
      const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : undefined;
      return field !== undefined ? `${field}: ${msg}` : msg;
    }
  } else if (detail && typeof detail === "object") {
    const message = nonEmpty((detail as { message?: unknown }).message);
    if (message) return message;
  }
  return nonEmpty(body.error) || `Request failed (${status})`;
}

/** True while new proposals fall back to the repo copy because no default is stored. */
export function showNoDefaultNote(data: BrandVoiceRevisionList): boolean {
  if (!data.enabled) return false; // nothing can be set, so the "no connection" notice covers it
  return data.activeId === "builtin" || !data.revisions.some((rev) => rev.isActive);
}

/** Only stored revisions can be made the default, never the repo copy. */
export function canSetDefault(rev: BrandVoiceRevision, enabled: boolean): boolean {
  return enabled && !rev.isActive && rev.id !== "builtin";
}

export function addedMessage(label: string): string {
  return `Added ${label}. Set it as the default below if you want new proposals to use it.`;
}
