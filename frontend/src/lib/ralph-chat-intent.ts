/**
 * Ask Ralph helpers for Verify / Match zö voice *buttons* only.
 *
 * Free-text asks are never keyword-matched — the user can say anything; the
 * improve LLM + brand-voice block (same as proposal generation) interpret intent.
 */

export type RalphChatPin = {
  mode: "selection" | "section";
  sectionTitle?: string;
  text?: string;
  selection?: { start: number; end: number; text: string };
};

function excerptSnippet(pin: RalphChatPin | null | undefined): string {
  if (!pin) return "";
  if (pin.mode === "selection") {
    return (pin.selection?.text || pin.text || "").trim().slice(0, 1200);
  }
  return "";
}

/** Explicit button: verify passage or section (not used to rewrite free-text). */
export function ralphVerifyPrompt(pin: RalphChatPin | null | undefined): string {
  const excerpt = excerptSnippet(pin);
  if (excerpt) {
    return (
      `Verify this highlighted passage against the knowledge base and RFP. ` +
      `Say what is grounded, what is weak or unverified, and whether any claim should become [VERIFY] or be rewritten. ` +
      `Do not invent facts.\n\nPassage:\n"""${excerpt}"""`
    );
  }
  return (
    `Verify the key claims in this section against the knowledge base and RFP. ` +
    `Call out anything fabricated, weak, or needing [VERIFY]. Do not invent facts.`
  );
}

/** Explicit button: rewrite to default Brand Voice (same as proposal generation). */
export function ralphVoicePrompt(pin: RalphChatPin | null | undefined): string {
  const excerpt = excerptSnippet(pin);
  if (excerpt) {
    return (
      `Rewrite the highlighted passage so it matches the default zö agency brand voice ` +
      `(the same Brand Voice standards used when generating proposals — pinned revision or active default). ` +
      `Keep every fact and RFP ask; change tone, rhythm, and flavor only. ` +
      `Use we/our/us; no generic consultant prose.\n\nPassage:\n"""${excerpt}"""`
    );
  }
  return (
    `Rewrite this section so it matches the default zö agency brand voice ` +
    `(the same Brand Voice standards used when generating proposals — pinned revision or active default). ` +
    `Keep every fact, RFP ask, table, and name; change tone, rhythm, and flavor only. ` +
    `Use we/our/us; no generic consultant prose.`
  );
}

/** Selection payload for the improve API — always include text when pinned. */
export function selectionPayloadForPin(
  pin: RalphChatPin | null | undefined,
  targetSectionId: string,
): { start: number; end: number; text: string } | undefined {
  if (!pin || pin.mode !== "selection") return undefined;
  if (pin.selection?.text?.trim()) {
    return {
      start: pin.selection.start ?? 0,
      end: pin.selection.end ?? 0,
      text: pin.selection.text.trim().slice(0, 8000),
    };
  }
  const text = (pin.text || "").trim();
  if (!text || text === (pin.sectionTitle || "").trim()) return undefined;
  void targetSectionId;
  return { start: 0, end: 0, text: text.slice(0, 8000) };
}
