"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { improveProposalSection } from "@/lib/proposal-api";
import {
  chatBusyStatusLabel,
  chatLiveWorkSteps,
  isOurWorkSection,
  messageLooksOutlineStructure,
  messageLooksProposalWide,
  pinnedSectionConflictsWithMessage,
  resolveChatTarget,
} from "@/lib/proposal-section-resolve";
import type { OutlineSection, ProposalOutline, ProposalResearch } from "@/types/proposal";
import type { SectionRevisionRecord } from "./DraftSectionEditor";
import { MarkdownReportBody } from "./MarkdownReportBody";
import { composeApplyFixInstruction, resolveApplyFixTarget } from "./compose-apply-fix-instruction";
import { CapabilityHoverTip } from "./CapabilityHoverTip";
import "./ProposalSectionChatPanel.css";

export interface SectionChatSuggestedFix {
  sectionId: string;
  instruction: string;
  summary: string;
  sectionTitle?: string;
}

export interface SectionChatAgentActivity {
  outcome: string;
  steps: string[];
  changes: string[];
  discrepancies: string[];
}

export interface SectionChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** Present when the assistant answered without changing the draft but offered a fix. */
  draftUnchanged?: boolean;
  suggestedFix?: SectionChatSuggestedFix | null;
  /** After Apply the fix succeeds or is dismissed. */
  suggestedFixApplied?: boolean;
  agentActivity?: SectionChatAgentActivity | null;
}

export interface SectionChatReference {
  mode: "selection" | "section";
  sectionId: string;
  sectionTitle: string;
  text: string;
  selection?: { start: number; end: number; text: string };
}

interface ProposalSectionChatPanelProps {
  rfpId: string;
  sections: OutlineSection[];
  /** Section currently open in the editor — fallback only when user does not name a section */
  viewingSectionId: string | null;
  disabled?: boolean;
  reference: SectionChatReference | null;
  onSetReference: (reference: SectionChatReference | null) => void;
  messages: SectionChatMessage[];
  onMessagesChange: (messages: SectionChatMessage[]) => void;
  onSectionUpdated: (draft: ProposalOutline, research: ProposalResearch | null) => void;
  onRevisionRecorded?: (sectionId: string, revision: SectionRevisionRecord) => void;
  onRevisionDrawerOpenChange?: (sectionId: string, open: boolean) => void;
  onFocusSection?: (sectionId: string) => void;
  /** Lifted from the parent (not local state) so an in-flight request's status
   * survives this panel unmounting when the user switches tabs and back. */
  busy: boolean;
  onBusyChange?: (busy: boolean) => void;
  statusLine: string | null;
  onStatusLineChange: (statusLine: string | null) => void;
  showClose?: boolean;
  onClose?: () => void;
}

function normalizeChatPlain(text: string): string {
  return text
    .replace(/\*\*/g, "")
    .replace(/[_`]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

/** True when assistant markdown adds info beyond the structured Recap card. */
export function assistantBodyAddsUniqueDetail(
  content: string,
  activity: SectionChatAgentActivity | null | undefined
): boolean {
  const body = (content || "").trim();
  if (!body) return false;
  if (!activity) return true;

  const plain = normalizeChatPlain(body)
    .replace(/\u2192/g, "->") // →
    .replace(/[“”]/g, '"');
  if (!plain) return false;

  // Pure apply-fix / word-count echos already covered under Changes.
  if (
    /^(applied the suggested fix to .+ \(\d+\s*->\s*\d+ words\)\.?)$/i.test(plain) ||
    /^(updated ["']?.+["']? \(\d+\s*->\s*\d+ words\)\.?)$/i.test(plain) ||
    /^(i could not apply the fix to .+)$/i.test(plain)
  ) {
    return false;
  }

  const covered = normalizeChatPlain(
    [...activity.steps, ...activity.changes, ...activity.discrepancies].join(" ")
  );
  if (covered && (covered.includes(plain) || plain.length < 120 && covered.includes(plain.slice(0, 40)))) {
    return false;
  }

  // Short body that only restates the first change line.
  const firstChange = normalizeChatPlain(activity.changes[0] || "");
  if (firstChange && (plain === firstChange || firstChange.includes(plain) || plain.includes(firstChange))) {
    return false;
  }

  return true;
}

const QUICK_PROMPTS = [
  "Designer-compact: tables + layout, keep every RFP ask.",
  "Check duplicates thoroughly.",
  "Remove fabricated content (content → RFP → KB).",
  "Fill [VERIFY] tags from KB only.",
  "Remove VERIFY and FLAG tags from this section.",
  "Does this meet the RFP?",
];

const REFERENCE_QUICK_PROMPTS = [
  "Fix duplicate reference contacts (KB verified only — no agency staff).",
  "Remove fabricated contacts; keep ClientList references only.",
  "Fill [VERIFY] tags from KB only.",
  "Does this meet the RFP?",
];

const CASE_STUDY_QUICK_PROMPTS = [
  "Is this case study relevant to the RFP? Suggest alternatives from KB if not.",
  "Add the best-matching case studies from the knowledge base.",
  "Strengthen relevance to the RFP — tie outcomes to their requirements.",
  "Check facts against the knowledge base.",
  "Designer-compact: tables + layout, keep every RFP ask.",
  "Does this meet the RFP?",
];

const SECTION_PIN_LABEL = "Improve this section";
const REVISE_PIN_LABEL = "Revise content";

export function buildSectionPinReference(
  section: OutlineSection,
  content: string
): SectionChatReference {
  // Full-section pins use title only so Ralph does not show raw markdown
  // (## headings) in the composer chip.
  return {
    mode: "section",
    sectionId: section.id,
    sectionTitle: section.title,
    text: section.title,
  };
}

/** Pin a highlighted manuscript excerpt into Ask Ralph (selection revise). */
export function buildSelectionPinReference(
  section: OutlineSection,
  selectedText: string
): SectionChatReference {
  const text = selectedText.replace(/\u00a0/g, " ").trim().slice(0, 8000);
  return {
    mode: "selection",
    sectionId: section.id,
    sectionTitle: section.title,
    text: text || section.title,
  };
}

export function ProposalSectionChatPanel({
  rfpId,
  sections,
  viewingSectionId,
  disabled,
  reference,
  onSetReference,
  messages,
  onMessagesChange,
  onSectionUpdated,
  onRevisionRecorded,
  onRevisionDrawerOpenChange,
  onFocusSection,
  busy,
  onBusyChange,
  statusLine,
  onStatusLineChange,
  showClose = false,
  onClose,
}: ProposalSectionChatPanelProps) {
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [liveStepIndex, setLiveStepIndex] = useState(0);
  const [pendingApply, setPendingApply] = useState<{
    messageId: string;
    fix: SectionChatSuggestedFix;
  } | null>(null);
  const [applyExtras, setApplyExtras] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const applyExtrasRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy, liveStepIndex]);

  useEffect(() => {
    if (!busy) {
      setLiveStepIndex(0);
      return;
    }
    const id = window.setInterval(() => {
      setLiveStepIndex((n) => n + 1);
    }, 2200);
    return () => window.clearInterval(id);
  }, [busy]);

  useEffect(() => {
    if (reference?.text) {
      window.setTimeout(() => inputRef.current?.focus(), 60);
    }
    if (reference?.mode === "section") {
      // Always bind the compose box to the newly pinned tab — do not keep a
      // leftover question about Client References after Improve on another tab.
      const titleCf = (reference.sectionTitle ?? "").toLowerCase();
      const pinnedSection = sections.find((s) => s.id === reference.sectionId) ?? null;
      if (titleCf.includes("reference") || titleCf.includes("exhibit k")) {
        setInput(
          "Fix reference contacts — verified ClientList only, no duplicate rows, no agency staff."
        );
      } else if (isOurWorkSection(pinnedSection)) {
        setInput(
          "Check this case study's relevance to the RFP. If weak, suggest a better match from the knowledge base."
        );
      } else {
        setInput("Improve this section for the RFP.");
      }
    }
  }, [reference?.text, reference?.sectionId, reference?.mode]);

  useEffect(() => {
    if (!pendingApply) return;
    window.setTimeout(() => applyExtrasRef.current?.focus(), 40);
  }, [pendingApply?.messageId]);

  const openApplyPanel = useCallback(
    (messageId: string, fix: SectionChatSuggestedFix) => {
      if (busy || disabled) return;
      setPendingApply({ messageId, fix });
      setApplyExtras("");
      setError(null);
    },
    [disabled, busy]
  );

  const cancelApplyPanel = useCallback(() => {
    setPendingApply(null);
    setApplyExtras("");
  }, []);

  const sendMessage = useCallback(
    async (message: string) => {
      const trimmed = message.trim();
      if (!trimmed || busy || sections.length === 0) return;

      // 1) Explicit pin (Revise excerpt / Improve full section) wins when it
      //    matches the ask. 2) Otherwise resolve from the query. 3) If ambiguous,
      //    ask the user which section — do not guess the open tab.
      let activeReference = reference;
      if (
        activeReference &&
        pinnedSectionConflictsWithMessage(trimmed, activeReference.sectionId, {
          viewingSectionId,
          sections,
        })
      ) {
        activeReference = null;
        onSetReference(null);
      }

      // Structural add/delete of sidebar tabs: clear pin. Keep Improve pin for
      // in-place asks on the open tab (any phrasing — replace person, fill gaps, etc.).
      if (
        messageLooksOutlineStructure(trimmed) &&
        activeReference &&
        activeReference.mode !== "selection" &&
        activeReference.mode !== "section"
      ) {
        activeReference = null;
        onSetReference(null);
      }

      const pinnedSectionId = activeReference?.sectionId;
      const pinnedSection = pinnedSectionId
        ? sections.find((s) => s.id === pinnedSectionId) ?? null
        : null;

      const resolution = resolveChatTarget(sections, trimmed, {
        viewingSectionId: viewingSectionId,
        pinnedSection: messageLooksOutlineStructure(trimmed) ? null : pinnedSection,
        conversationHistory: messages.map((m) => ({
          role: m.role,
          content: m.content,
        })),
      });

      const userMsg: SectionChatMessage = {
        id: `u-${Date.now()}`,
        role: "user",
        content: trimmed,
      };
      const nextMessages = [...messages, userMsg];
      onMessagesChange(nextMessages);
      setInput("");
      setPendingApply(null);
      setApplyExtras("");

      if (!resolution) return;

      if (resolution.kind === "clarify") {
        onMessagesChange([
          ...nextMessages,
          {
            id: `c-${Date.now()}`,
            role: "assistant",
            content: resolution.question,
          },
        ]);
        return;
      }

      // Revise-excerpt / Improve-full-section pins always win the API target.
      // Mentions of §21 / Experience / "move into Qualifications" must NOT steal
      // the edit — Improve means this tab only.
      const selectionPin =
        activeReference?.mode === "selection" && activeReference.selection
          ? activeReference
          : null;
      const improvePin =
        activeReference?.mode === "section" && activeReference.sectionId
          ? activeReference
          : null;
      let targetSection = resolution.section;
      if (selectionPin) {
        const pinnedSec = sections.find((s) => s.id === selectionPin.sectionId);
        if (pinnedSec) targetSection = pinnedSec;
      } else if (improvePin && resolution.reason !== "outline-structure") {
        const pinnedSec = sections.find((s) => s.id === improvePin.sectionId);
        if (pinnedSec) targetSection = pinnedSec;
      }

      // Stale Improve pin on another tab must not keep redirecting status/API.
      // Clarify-reply / outline-structure may leave the pin intentionally.
      if (
        activeReference &&
        activeReference.mode !== "selection" &&
        activeReference.sectionId !== targetSection.id &&
        resolution.reason !== "pinned" &&
        resolution.reason !== "clarify-reply" &&
        resolution.reason !== "outline-structure"
      ) {
        activeReference = null;
        onSetReference(null);
      }

      const proposalWideAsk =
        !improvePin &&
        (resolution.reason === "proposal-wide" ||
          resolution.reason === "outline-structure" ||
          messageLooksProposalWide(trimmed));

      setError(null);
      onStatusLineChange(
        chatBusyStatusLabel(trimmed, targetSection.title, {
          proposalWide: proposalWideAsk,
          referenceMode: activeReference?.mode ?? null,
          sameSectionPinned: activeReference?.sectionId === targetSection.id,
        })
      );
      onBusyChange?.(true);

      const selectionForRequest =
        activeReference?.mode === "selection" &&
        activeReference.sectionId === targetSection.id
          ? activeReference.selection
          : undefined;

      try {
        const history = nextMessages.slice(0, -1).map((m) => ({
          role: m.role,
          content: m.content,
        }));
        const result = await improveProposalSection(rfpId, targetSection.id, trimmed, {
          selection: selectionForRequest,
          conversationHistory: history,
          proposalWide: proposalWideAsk,
          improveSectionPinned:
            activeReference?.mode === "section" &&
            activeReference.sectionId === targetSection.id,
        });

        // The resolver can legitimately land on a section other than the one
        // open in the editor (a named mention, a proposal-wide ask, a stale
        // pin). When that happens, say so up front — a reader who has a
        // different tab open should never have to infer which section a
        // response is actually about from its content.
        const viewingSection = viewingSectionId
          ? sections.find((s) => s.id === viewingSectionId) ?? null
          : null;
        const targetDiffersFromOpenTab =
          viewingSection !== null && viewingSection.id !== targetSection.id;
        const responseContent = targetDiffersFromOpenTab
          ? `_Acted on "${targetSection.title}" — not "${viewingSection.title}", which is open in the editor._\n\n${result.assistantMessage}`
          : result.assistantMessage;

        onMessagesChange([
          ...nextMessages,
          {
            id: `a-${Date.now()}`,
            role: "assistant",
            content: responseContent,
            draftUnchanged: !result.draftChanged,
            suggestedFix:
              !result.draftChanged && result.suggestedFix ? result.suggestedFix : null,
            agentActivity: result.agentActivity,
          },
        ]);

        if (result.draftChanged) {
          const beforeById = new Map(
            sections.map((s) => [s.id, s.content || ""] as const)
          );
          const changed = result.draft.sections.filter((s) => {
            const prev = beforeById.get(s.id);
            return prev !== undefined && (s.content || "") !== prev;
          });
          const focusId = targetSection.id;
          const targetChanged =
            changed.find((s) => s.id === focusId) ?? changed[0] ?? null;

          if (result.previewPending) {
            // Preview-first: do NOT write the live draft until Apply.
            if (targetChanged) {
              onRevisionRecorded?.(focusId, {
                before: beforeById.get(focusId) || "",
                after: targetChanged.content || "",
                summary: result.assistantMessage,
                instruction: trimmed,
                updatedAt: Date.now(),
                awaitingConfirm: true,
                pendingDraft: result.draft,
                pendingResearch: result.research,
              });
              onRevisionDrawerOpenChange?.(focusId, true);
            }
            onFocusSection?.(focusId);
          } else {
            onSectionUpdated(result.draft, result.research);
            if (targetChanged) {
              onRevisionRecorded?.(focusId, {
                before: beforeById.get(focusId) || "",
                after: targetChanged.content || "",
                summary: result.assistantMessage,
                instruction: trimmed,
                updatedAt: Date.now(),
              });
              onRevisionDrawerOpenChange?.(focusId, true);
            }
            onFocusSection?.(focusId);
          }
        }
      } catch (err) {
        const detail = err instanceof Error ? err.message : "Chat request failed";
        onMessagesChange([
          ...nextMessages,
          {
            id: `e-${Date.now()}`,
            role: "assistant",
            content: detail,
            draftUnchanged: true,
            agentActivity: {
              outcome: "needs_review",
              steps: ["Tried to run your instruction"],
              changes: ["No manuscript text was changed."],
              discrepancies: [detail],
            },
          },
        ]);
      } finally {
        onStatusLineChange(null);
        onBusyChange?.(false);
      }
    },
    [
      busy,
      messages,
      onBusyChange,
      onStatusLineChange,
      onMessagesChange,
      onRevisionDrawerOpenChange,
      onRevisionRecorded,
      onFocusSection,
      onSectionUpdated,
      onSetReference,
      reference,
      rfpId,
      sections,
      viewingSectionId,
    ]
  );

  const applySuggestedFix = useCallback(
    async (messageId: string, fix: SectionChatSuggestedFix, extras: string) => {
      if (busy || disabled) return;
      // Always the audited tab from suggestedFix — never the currently open
      // sidebar tab (that rewrote Exhibit 2 when Exhibit 5's Apply was clicked).
      const target = resolveApplyFixTarget(sections, fix, viewingSectionId);
      if (!target) {
        setError("That section is no longer in the draft.");
        return;
      }

      const instruction = composeApplyFixInstruction(fix, extras);
      const extrasNote = extras.trim();
      const applyUser: SectionChatMessage = {
        id: `u-apply-${Date.now()}`,
        role: "user",
        content: extrasNote
          ? `Apply the fix: ${fix.summary || "suggested changes"}\n\nAlso: ${extrasNote}`
          : `Apply the fix: ${fix.summary || fix.instruction}`,
      };
      const nextMessages = [
        ...messages.map((m) =>
          m.id === messageId ? { ...m, suggestedFixApplied: true } : m
        ),
        applyUser,
      ];
      onMessagesChange(nextMessages);
      setPendingApply(null);
      setApplyExtras("");
      setError(null);
      onStatusLineChange(`Applying fix on ${target.title}…`);
      onBusyChange?.(true);
      onFocusSection?.(target.id);

      try {
        const history = nextMessages.slice(0, -1).map((m) => ({
          role: m.role,
          content: m.content,
        }));
        const result = await improveProposalSection(
          rfpId,
          target.id,
          instruction,
          {
            conversationHistory: history,
            applyFix: true,
            improveSectionPinned: true,
          }
        );

        onMessagesChange([
          ...nextMessages,
          {
            id: `a-${Date.now()}`,
            role: "assistant",
            content: result.assistantMessage,
            draftUnchanged: !result.draftChanged,
            agentActivity: result.agentActivity,
          },
        ]);

        if (result.draftChanged) {
          const beforeById = new Map(
            sections.map((s) => [s.id, s.content || ""] as const)
          );
          const changed = result.draft.sections.filter((s) => {
            const prev = beforeById.get(s.id);
            return prev !== undefined && (s.content || "") !== prev;
          });
          const focusId = target.id;
          const targetChanged =
            changed.find((s) => s.id === focusId) ?? changed[0] ?? null;

          if (result.previewPending) {
            if (targetChanged) {
              onRevisionRecorded?.(focusId, {
                before: beforeById.get(focusId) || "",
                after: targetChanged.content || "",
                summary: result.assistantMessage,
                instruction,
                updatedAt: Date.now(),
                awaitingConfirm: true,
                pendingDraft: result.draft,
                pendingResearch: result.research,
              });
              onRevisionDrawerOpenChange?.(focusId, true);
            }
            onFocusSection?.(focusId);
          } else {
            onSectionUpdated(result.draft, result.research);
            if (targetChanged) {
              onRevisionRecorded?.(focusId, {
                before: beforeById.get(focusId) || "",
                after: targetChanged.content || "",
                summary: result.assistantMessage,
                instruction,
                updatedAt: Date.now(),
              });
              onRevisionDrawerOpenChange?.(focusId, true);
            }
            onFocusSection?.(focusId);
          }
        }
      } catch (err) {
        const detail = err instanceof Error ? err.message : "Apply fix failed";
        onMessagesChange([
          ...nextMessages,
          {
            id: `e-${Date.now()}`,
            role: "assistant",
            content: detail,
            draftUnchanged: true,
            agentActivity: {
              outcome: "needs_review",
              steps: ["Tried to apply the suggested fix"],
              changes: ["No manuscript text was changed."],
              discrepancies: [detail],
            },
          },
        ]);
      } finally {
        onStatusLineChange(null);
        onBusyChange?.(false);
      }
    },
    [
      disabled,
      busy,
      messages,
      onBusyChange,
      onStatusLineChange,
      onFocusSection,
      onMessagesChange,
      onRevisionDrawerOpenChange,
      onRevisionRecorded,
      onSectionUpdated,
      rfpId,
      sections,
      viewingSectionId,
    ]
  );

  const viewingSection =
    sections.find((s) => s.id === viewingSectionId) ?? sections[0] ?? null;

  const quickPrompts = useMemo(() => {
    const title = (
      viewingSection?.title ??
      reference?.sectionTitle ??
      ""
    ).toLowerCase();
    if (title.includes("reference") || title.includes("exhibit k")) {
      return REFERENCE_QUICK_PROMPTS;
    }
    if (isOurWorkSection(viewingSection)) {
      return CASE_STUDY_QUICK_PROMPTS;
    }
    return QUICK_PROMPTS;
  }, [viewingSection?.title, viewingSection, reference?.sectionTitle]);

  const pinViewingSection = () => {
    if (!viewingSection || disabled || busy) return;
    onSetReference(
      buildSectionPinReference(viewingSection, viewingSection.content || "")
    );
    setInput("Improve this section for the RFP.");
  };

  const pinReviseSection = () => {
    if (!viewingSection || disabled || busy) return;
    onSetReference(
      buildSectionPinReference(viewingSection, viewingSection.content || "")
    );
    setInput("Revise this section.");
  };

  // After all hooks — empty outline mid-Strict rebuild must not skip useMemo.
  if (sections.length === 0) return null;

  return (
    <aside className="proposal-section-chat" aria-label="Ask Ralph">
      <header className="proposal-section-chat-header">
        <div className="min-w-0 flex-1">
          <CapabilityHoverTip id="assistant" side="bottom">
            <p className="proposal-section-chat-kicker cursor-help">
              Ask Ralph
            </p>
          </CapabilityHoverTip>
        </div>
        {showClose && onClose ? (
          <button
            type="button"
            className="proposal-section-chat-icon-btn"
            aria-label="Close Ask Ralph"
            onClick={onClose}
          >
            ×
          </button>
        ) : null}
      </header>

      <div ref={scrollRef} className="proposal-section-chat-messages custom-scrollbar">
        {messages.length === 0 ? (
          <div className="proposal-ralph-empty">
            <RalphPortrait />
            <p className="proposal-ralph-empty-hello">Hi, I&apos;m Ralph.</p>
            <p className="proposal-ralph-empty-copy">
              Ask by section name, or say what to change. Edits stay on this tab
              unless you say across the proposal.
            </p>
            <div className="proposal-ralph-actions">
              {viewingSection ? (
                <>
                  <button
                    type="button"
                    disabled={disabled || busy}
                    onClick={pinViewingSection}
                    className="proposal-ralph-action"
                  >
                    <RalphActionIcon kind="sparkle" />
                    <span>Improve this section</span>
                    <RalphActionIcon kind="chevron" />
                  </button>
                  <button
                    type="button"
                    disabled={disabled || busy}
                    onClick={() =>
                      void sendMessage(
                        "Make this section shorter. Keep every RFP ask.",
                      )
                    }
                    className="proposal-ralph-action"
                  >
                    <RalphActionIcon kind="compress" />
                    <span>Make this shorter</span>
                    <RalphActionIcon kind="chevron" />
                  </button>
                  <button
                    type="button"
                    disabled={disabled || busy}
                    onClick={() => void sendMessage("Does this meet the RFP?")}
                    className="proposal-ralph-action"
                  >
                    <RalphActionIcon kind="check" />
                    <span>Check RFP compliance</span>
                    <RalphActionIcon kind="chevron" />
                  </button>
                  <button
                    type="button"
                    disabled={disabled || busy}
                    onClick={() =>
                      void sendMessage(
                        "Fix issues in this section. Remove fabricated content; fill VERIFY tags from KB only.",
                      )
                    }
                    className="proposal-ralph-action"
                  >
                    <RalphActionIcon kind="wrench" />
                    <span>Fix issues in this section</span>
                    <RalphActionIcon kind="chevron" />
                  </button>
                </>
              ) : (
                <p className="proposal-ralph-empty-copy">
                  Open a section, then pick a task or type below.
                </p>
              )}
            </div>
          </div>
        ) : (
          messages.map((msg) => (
            <div
              key={msg.id}
              className={`proposal-section-chat-bubble proposal-section-chat-bubble--${msg.role}`}
            >
              {msg.role === "assistant" ? (
                <>
                  {msg.draftUnchanged ? (
                    <p className="proposal-section-chat-draft-badge">DRAFT UNCHANGED</p>
                  ) : null}
                  {msg.agentActivity ? (
                    <AgentActivityCard activity={msg.agentActivity} />
                  ) : null}
                  {assistantBodyAddsUniqueDetail(msg.content, msg.agentActivity) ? (
                    <MarkdownReportBody body={msg.content} variant="chat" />
                  ) : null}
                  {msg.suggestedFix && !msg.suggestedFixApplied ? (
                    <div className="proposal-section-chat-apply">
                      {pendingApply?.messageId === msg.id ? (
                        <div
                          className="proposal-section-chat-apply-panel"
                          role="group"
                          aria-label="Confirm apply fix"
                        >
                          <p className="proposal-section-chat-apply-panel-kicker">
                            Planned fix
                          </p>
                          <p className="proposal-section-chat-apply-summary">
                            {msg.suggestedFix.summary ||
                              "Apply the suggested changes to this section."}
                          </p>
                          <label
                            className="proposal-section-chat-apply-label"
                            htmlFor={`apply-extras-${msg.id}`}
                          >
                            Anything else?{" "}
                            <span className="proposal-section-chat-apply-optional">
                              optional
                            </span>
                          </label>
                          <textarea
                            id={`apply-extras-${msg.id}`}
                            ref={applyExtrasRef}
                            value={applyExtras}
                            onChange={(e) => setApplyExtras(e.target.value)}
                            disabled={disabled || busy}
                            rows={2}
                            placeholder="e.g. keep Bend, only scrub invented contacts…"
                            className="proposal-section-chat-apply-extras"
                            onKeyDown={(e) => {
                              if (e.key === "Escape") {
                                e.preventDefault();
                                cancelApplyPanel();
                              }
                              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                                e.preventDefault();
                                void applySuggestedFix(
                                  msg.id,
                                  msg.suggestedFix!,
                                  applyExtras
                                );
                              }
                            }}
                          />
                          <div className="proposal-section-chat-apply-actions">
                            <button
                              type="button"
                              className="proposal-section-chat-apply-btn proposal-section-chat-apply-btn--ghost"
                              disabled={disabled || busy}
                              onClick={cancelApplyPanel}
                            >
                              Cancel
                            </button>
                            <button
                              type="button"
                              className="proposal-section-chat-apply-btn"
                              disabled={disabled || busy}
                              onClick={() =>
                                void applySuggestedFix(
                                  msg.id,
                                  msg.suggestedFix!,
                                  applyExtras
                                )
                              }
                            >
                              Apply
                            </button>
                          </div>
                        </div>
                      ) : (
                        <>
                          <button
                            type="button"
                            className="proposal-section-chat-apply-btn"
                            disabled={disabled || busy}
                            onClick={() =>
                              openApplyPanel(msg.id, msg.suggestedFix!)
                            }
                          >
                            Apply the fix
                          </button>
                          {msg.suggestedFix.summary ? (
                            <p className="proposal-section-chat-apply-summary">
                              {msg.suggestedFix.summary}
                            </p>
                          ) : null}
                        </>
                      )}
                    </div>
                  ) : null}
                </>
              ) : (
                msg.content
              )}
            </div>
          ))
        )}
        {busy ? (
          <ChatLiveTicker
            statusLine={statusLine}
            stepIndex={liveStepIndex}
          />
        ) : null}
      </div>

      <div className="proposal-section-chat-composer">
        {reference?.text ? (
          <div className="proposal-section-chat-reference">
            <div className="min-w-0 flex-1">
              <p className="proposal-section-chat-reference-label">
                {reference.sectionTitle}
                {reference.mode === "selection" ? " · excerpt" : " · section"}
              </p>
              {reference.mode === "selection" ? (
                <p className="proposal-section-chat-reference-text">
                  “{reference.text}”
                </p>
              ) : (
                <p className="proposal-section-chat-reference-text text-zo-text-muted">
                  Whole section pinned — describe what to change in your message
                  below, then send.
                </p>
              )}
            </div>
            <button
              type="button"
              className="proposal-section-chat-icon-btn"
              aria-label="Clear reference"
              onClick={() => onSetReference(null)}
            >
              ×
            </button>
          </div>
        ) : null}

        {error ? <p className="proposal-section-chat-error mb-1">{error}</p> : null}

        <div className="proposal-section-chat-input-row">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={disabled || busy}
            rows={1}
            placeholder="Ask Ralph anything…"
            className="proposal-section-chat-input"
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void sendMessage(input);
              }
            }}
          />
          <button
            type="button"
            disabled={disabled || busy || !input.trim()}
            className="proposal-section-chat-send"
            aria-label="Send"
            onClick={() => void sendMessage(input)}
          >
            ↑
          </button>
        </div>

        {messages.length > 0 ? (
        <div className="proposal-section-chat-quick custom-scrollbar">
          {viewingSection ? (
            <>
              <button
                type="button"
                disabled={disabled || busy}
                onClick={pinReviseSection}
                className="proposal-section-chat-quick-btn proposal-section-chat-quick-btn--primary"
              >
                {REVISE_PIN_LABEL}
              </button>
              <button
                type="button"
                disabled={disabled || busy}
                onClick={pinViewingSection}
                className="proposal-section-chat-quick-btn proposal-section-chat-quick-btn--primary"
              >
                {SECTION_PIN_LABEL}
              </button>
            </>
          ) : null}
          {quickPrompts.map((prompt) => (
            <button
              key={prompt}
              type="button"
              disabled={disabled || busy}
              onClick={() => void sendMessage(prompt)}
              className="proposal-section-chat-quick-btn"
            >
              {prompt}
            </button>
          ))}
        </div>
        ) : null}
      </div>
    </aside>
  );
}

function RalphPortrait() {
  return (
    <svg
      className="proposal-ralph-portrait"
      width="88"
      height="88"
      viewBox="0 0 88 88"
      fill="none"
      aria-hidden
    >
      <ellipse cx="44" cy="78" rx="22" ry="5" fill="#ef5018" opacity="0.12" />
      <rect
        x="18"
        y="22"
        width="52"
        height="48"
        rx="16"
        fill="#fff7f2"
        stroke="#f0c4b0"
        strokeWidth="1.5"
      />
      <rect x="24" y="30" width="40" height="22" rx="11" fill="#1f2933" />
      <circle cx="36" cy="41" r="5.5" fill="#7dd3c0" />
      <circle cx="52" cy="41" r="5.5" fill="#7dd3c0" />
      <circle cx="37.5" cy="39.5" r="1.6" fill="#fff" />
      <circle cx="53.5" cy="39.5" r="1.6" fill="#fff" />
      <path
        d="M34 58c3.2 4 16.8 4 20 0"
        stroke="#ef5018"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
      <rect x="40" y="10" width="8" height="12" rx="4" fill="#ef5018" />
      <circle cx="44" cy="10" r="4.5" fill="#ffd652" stroke="#ef5018" strokeWidth="1.5" />
      <rect x="12" y="40" width="8" height="14" rx="4" fill="#ff8939" />
      <rect x="68" y="40" width="8" height="14" rx="4" fill="#ff8939" />
    </svg>
  );
}

function RalphActionIcon({
  kind,
}: {
  kind: "sparkle" | "compress" | "check" | "wrench" | "chevron";
}) {
  const common = {
    width: 16,
    height: 16,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    "aria-hidden": true,
  } as const;
  if (kind === "sparkle") {
    return (
      <svg {...common}>
        <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8" />
      </svg>
    );
  }
  if (kind === "compress") {
    return (
      <svg {...common}>
        <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" />
      </svg>
    );
  }
  if (kind === "check") {
    return (
      <svg {...common}>
        <path d="M9 11l3 3L22 4" />
        <path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11" />
      </svg>
    );
  }
  if (kind === "chevron") {
    return (
      <svg {...common} className="proposal-ralph-action-chevron">
        <path d="M9 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <path d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z" />
    </svg>
  );
}

function ChatLiveTicker({
  statusLine,
  stepIndex,
}: {
  statusLine: string | null;
  stepIndex: number;
}) {
  const steps = chatLiveWorkSteps(statusLine);
  const active = stepIndex % steps.length;
  return (
    <div className="proposal-section-chat-live" role="status" aria-live="polite">
      <p className="proposal-section-chat-live-kicker">Agent working</p>
      <ul className="proposal-section-chat-live-steps">
        {steps.map((step, i) => (
          <li
            key={step}
            className={
              i === active
                ? "proposal-section-chat-live-step proposal-section-chat-live-step--active"
                : i < active
                  ? "proposal-section-chat-live-step proposal-section-chat-live-step--done"
                  : "proposal-section-chat-live-step"
            }
          >
            {step}
          </li>
        ))}
      </ul>
    </div>
  );
}

function AgentActivityCard({ activity }: { activity: SectionChatAgentActivity }) {
  const discrepancies = activity.discrepancies.filter(Boolean);
  const none = discrepancies.length === 0;
  return (
    <div className="proposal-section-chat-activity">
      <p className="proposal-section-chat-activity-kicker">
        {activity.outcome === "needs_review"
          ? "Recap — needs review"
          : activity.outcome === "unchanged"
            ? "Recap — no manuscript change"
            : "Recap"}
      </p>
      {activity.steps.length > 0 ? (
        <>
          <p className="proposal-section-chat-activity-label">What I did</p>
          <ul>
            {activity.steps.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </>
      ) : null}
      {activity.changes.length > 0 ? (
        <>
          <p className="proposal-section-chat-activity-label">Changes</p>
          <ul>
            {activity.changes.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </>
      ) : null}
      <p className="proposal-section-chat-activity-label">Discrepancies</p>
      {none ? (
        <p className="proposal-section-chat-activity-none">None found</p>
      ) : (
        <ul>
          {discrepancies.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
