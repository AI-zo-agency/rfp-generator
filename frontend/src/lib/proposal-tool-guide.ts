/**
 * Shared “does / doesn’t” copy for Review-tab tools.
 * Used by hover tooltips and confirm dialogs — not a static rail dump.
 */

export type ToolCapabilityId =
  | "fixOutline"
  | "reorder"
  | "place"
  | "completeClean"
  | "ralph"
  | "assistant"
  | "improveSection"
  | "reviseSelection"
  | "savedVersion"
  | "restore"
  | "matchStudies"
  | "moreMenu"
  | "keyPersonas"
  | "generateProposal"
  | "editPreview";

export type ToolCapability = {
  id: ToolCapabilityId;
  name: string;
  does: string;
  doesnt: string;
};

export const PROPOSAL_TOOL_CAPABILITIES: readonly ToolCapability[] = [
  {
    id: "fixOutline",
    name: "Fix outline",
    does: "Open two jobs: reorder the left list to match the RFP, or move misplaced paragraphs under the right heading.",
    doesnt: "Rewrite wording, invent facts, or run Complete & clean.",
  },
  {
    id: "reorder",
    name: "Reorder the left list",
    does: "Match section order to the RFP (you preview, then apply).",
    doesnt: "Rewrite paragraphs or invent new facts.",
  },
  {
    id: "place",
    name: "Move misplaced paragraphs",
    does: "Move existing blocks under the right heading (you approve first).",
    doesnt: "Rewrite voice or invent content for empty tabs.",
  },
  {
    id: "completeClean",
    name: "Complete & clean",
    does: "Structure, fact-check, compliance, and submission readiness on this draft. Includes Ralph (page limit & anti-invention).",
    doesnt: "Wipe good sections or regenerate the whole proposal from scratch.",
  },
  {
    id: "ralph",
    name: "Page limit & anti-invention (Ralph)",
    does: "Fit the RFP page limit when one is stated; strip invented diagrams and fake “see attached” visuals.",
    doesnt: "Invent graphics, pad length, or change your facts.",
  },
  {
    id: "assistant",
    name: "Proposal assistant",
    does: "Chat to revise a pinned section or excerpt. Changes stay on that tab unless you say across the proposal.",
    doesnt: "Reorder the whole packet or run Complete & clean for you.",
  },
  {
    id: "improveSection",
    name: "Improve full section",
    does: "Pin this whole tab into chat so the assistant can revise it end-to-end.",
    doesnt: "Touch other tabs or invent facts that aren’t in the knowledge base.",
  },
  {
    id: "reviseSelection",
    name: "Revise content",
    does: "Pin only the highlighted passage into chat for a focused rewrite.",
    doesnt: "Rewrite the rest of the section unless you ask it to.",
  },
  {
    id: "savedVersion",
    name: "Saved version",
    does: "Pick a checkpoint (before Align, Complete & clean, chat improve, etc.) to restore or compare.",
    doesnt: "Change the live draft until you click Restore.",
  },
  {
    id: "restore",
    name: "Restore",
    does: "Load the selected saved version as the live draft (current draft is saved first).",
    doesnt: "Delete other checkpoints or regenerate the proposal.",
  },
  {
    id: "matchStudies",
    name: "Match studies",
    does: "Rank knowledge-base case studies against this RFP so you can pick the best fits.",
    doesnt: "Rewrite the manuscript or invent case studies that aren’t in the KB.",
  },
  {
    id: "moreMenu",
    name: "More",
    does: "Extra draft actions: designer-compact, reset, or restart from Case Studies / Intelligence.",
    doesnt: "Run Complete & clean or Fix outline — those stay on Review.",
  },
  {
    id: "keyPersonas",
    name: "Key Personas",
    does: "Choose who appears in Team Bios for this proposal. Required before Generate.",
    doesnt: "Edit bios by itself — open a bio section or chat after generating.",
  },
  {
    id: "generateProposal",
    name: "Generate proposal",
    does: "Draft the proposal from research, personas, and the knowledge base (full run or resume).",
    doesnt: "Only tidy an existing draft — use Complete & clean on Review for that.",
  },
  {
    id: "editPreview",
    name: "Edit / Preview",
    does: "Switch between raw markdown editing and the formatted preview of this section.",
    doesnt: "Send changes to the assistant — use Revise content or Improve full section for that.",
  },
] as const;

export function capabilityById(id: ToolCapabilityId): ToolCapability {
  const found = PROPOSAL_TOOL_CAPABILITIES.find((c) => c.id === id);
  if (!found) throw new Error(`Unknown tool capability: ${id}`);
  return found;
}

/** Compact Does / Doesn’t block for confirm dialogs. */
export function formatDoesDoesntBlock(...ids: ToolCapabilityId[]): string {
  return ids
    .map((id) => {
      const c = capabilityById(id);
      return `${c.name}\nDoes: ${c.does}\nDoesn’t: ${c.doesnt}`;
    })
    .join("\n\n");
}
