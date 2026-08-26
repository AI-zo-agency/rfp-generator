/** Merge optional user extras into the suggested-fix instruction. */
export function composeApplyFixInstruction(
  fix: { instruction: string },
  extras: string
): string {
  const note = extras.trim();
  if (!note) return fix.instruction;
  return `${fix.instruction}\n\nAdditional user instructions:\n${note}`;
}

export type ApplyFixSectionRef = {
  id: string;
  title: string;
};

/**
 * Resolve which sidebar tab "Apply the fix" must edit.
 *
 * The suggested fix carries the audited section id — that ALWAYS wins over
 * whatever tab is currently open. Preferring the open tab caused Exhibit 5
 * VERIFY applies to rewrite Exhibit 2 after the user navigated away.
 */
export function resolveApplyFixTarget<T extends ApplyFixSectionRef>(
  sections: T[],
  fix: { sectionId: string; sectionTitle?: string },
  viewingSectionId?: string | null
): T | null {
  if (!sections.length) return null;

  const byId = sections.find((s) => s.id === fix.sectionId);
  if (byId) return byId;

  const wanted = fix.sectionTitle?.trim().toLowerCase();
  if (wanted) {
    const byTitle = sections.find((s) => s.title.trim().toLowerCase() === wanted);
    if (byTitle) return byTitle;
    // Soft match: title contains distinctive head (Exhibit 5 …)
    const soft = sections.filter((s) => {
      const t = s.title.trim().toLowerCase();
      return (
        (t.includes(wanted) || wanted.includes(t)) &&
        Math.min(t.length, wanted.length) >= 12
      );
    });
    if (soft.length === 1) return soft[0];
  }

  // Last resort only — never prefer viewing over a known fix.sectionId above.
  if (viewingSectionId) {
    const viewing = sections.find((s) => s.id === viewingSectionId);
    if (viewing) return viewing;
  }
  return sections[0] ?? null;
}
