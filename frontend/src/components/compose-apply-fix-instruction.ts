/** Merge optional user extras into the suggested-fix instruction. */
export function composeApplyFixInstruction(
  fix: { instruction: string },
  extras: string
): string {
  const note = extras.trim();
  if (!note) return fix.instruction;
  return `${fix.instruction}\n\nAdditional user instructions:\n${note}`;
}
