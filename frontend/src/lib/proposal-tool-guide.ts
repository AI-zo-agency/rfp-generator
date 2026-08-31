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
    does: "Staff salvage: reorder the left list or move misplaced paragraphs. Build my proposal already matches RFP tab order.",
    doesnt: "Need to run after every build. Skip unless the left list still looks wrong.",
  },
  {
    id: "reorder",
    name: "Reorder the left list",
    does: "Match section order to the RFP (preview, then apply). Only needed if Build left the list wrong.",
    doesnt: "Rewrite paragraphs, invent facts, or replace Build my proposal.",
  },
  {
    id: "place",
    name: "Move misplaced paragraphs",
    does: "Move existing blocks under the right heading (you approve first).",
    doesnt: "Rewrite voice or invent content for empty tabs.",
  },
  {
    id: "completeClean",
    name: "Review & fix",
    does: "Optional second pass after you edit — full fact-check, compliance, and page-limit audit (all 18 checks).",
    doesnt: "Need to run after every Build — that already runs final checks and Ralph trim.",
  },
  {
    id: "ralph",
    name: "Page limit & anti-invention (Ralph)",
    does: "Fit the RFP page limit when one is stated; strip invented diagrams and fake “see attached” visuals.",
    doesnt: "Invent graphics, pad length, or change your facts.",
  },
  {
    id: "assistant",
    name: "Ask Ralph",
    does: "Chat to revise a pinned section or excerpt. Changes stay on that tab unless you say across the proposal.",
    doesnt: "Reorder the whole packet or run Review & fix for you.",
  },
  {
    id: "improveSection",
    name: "Improve full section",
    does: "Pin this whole tab into chat so Ralph can revise it end-to-end.",
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
    does: "Pick a checkpoint (before Align, Review & fix, chat improve, etc.) to restore or compare.",
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
    doesnt: "Run Build my proposal — Fix outline lives under Staff tools on Review.",
  },
  {
    id: "keyPersonas",
    name: "Key Personas",
    does: "Choose who appears in Team Bios for this proposal. Required before Build my proposal.",
    doesnt: "Edit bios by itself — open a bio section or Ask Ralph after building.",
  },
  {
    id: "generateProposal",
    name: "Build my proposal",
    does: "Draft the full proposal: company/team, RFP tab order, writing, budget, review, and final fact-check + page-limit trim. One click.",
    doesnt: "Need a follow-up Review & fix unless you edited the draft and want a full re-audit.",
  },
  {
    id: "editPreview",
    name: "Edit / Preview",
    does: "Switch between raw markdown editing and the formatted preview of this section.",
    doesnt: "Send changes to Ralph — use Revise content or Improve full section for that.",
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
