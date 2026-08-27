"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { improveProposalSection } from "@/lib/proposal-api";
import {
  chatBusyStatusLabel,
  chatLiveWorkSteps,
  messageLooksOutlineStructure,
  messageLooksProposalWide,
  pinnedSectionConflictsWithMessage,
  resolveChatTarget,
} from "@/lib/proposal-section-resolve";
import type { OutlineSection, ProposalOutline, ProposalResearch } from "@/types/proposal";
import type { SectionRevisionRecord } from "./DraftSectionEditor";
import { MarkdownReportBody } from "./MarkdownReportBody";
import { composeApplyFixInstruction, resolveApplyFixTarget } from "./compose-apply-fix-instruction";
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
  onBusyChange?: (busy: boolean) => void;
  showClose?: boolean;
  onClose?: () => void;
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

const SECTION_PIN_LABEL = "Improve this section";
const REVISE_PIN_LABEL = "Revise content";

export function buildSectionPinReference(
  section: OutlineSection,
  content: string
): SectionChatReference {
  const body = content.trim();
  return {
    mode: "section",
    sectionId: section.id,
    sectionTitle: section.title,
    text: body.slice(0, 1200) || section.title,
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
  onBusyChange,
  showClose = false,
  onClose,
}: ProposalSectionChatPanelProps) {
  const [input, setInput] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusLine, setStatusLine] = useState<string | null>(null);
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
  }, [messages, isRunning, liveStepIndex]);

  useEffect(() => {
    if (!isRunning) {
      setLiveStepIndex(0);
      return;
    }
    const id = window.setInterval(() => {
      setLiveStepIndex((n) => n + 1);
    }, 2200);
    return () => window.clearInterval(id);
  }, [isRunning]);

  useEffect(() => {
    if (reference?.text) {
      window.setTimeout(() => inputRef.current?.focus(), 60);
    }
    if (reference?.mode === "section") {
      // Always bind the compose box to the newly pinned tab — do not keep a
      // leftover question about Client References after Improve on another tab.
      const titleCf = (reference.sectionTitle ?? "").toLowerCase();
      if (titleCf.includes("reference") || titleCf.includes("exhibit k")) {
        setInput(
          "Fix reference contacts — verified ClientList only, no duplicate rows, no agency staff."
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
      if (isRunning || disabled) return;
      setPendingApply({ messageId, fix });
      setApplyExtras("");
      setError(null);
    },
    [disabled, isRunning]
  );

  const cancelApplyPanel = useCallback(() => {
    setPendingApply(null);
    setApplyExtras("");
  }, []);

  const sendMessage = useCallback(
    async (message: string) => {
      const trimmed = message.trim();
      if (!trimmed || isRunning || sections.length === 0) return;

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

      // Structural add/delete: clear an unrelated pin so we don't rewrite the open tab.
      if (
        messageLooksOutlineStructure(trimmed) &&
        activeReference &&
        activeReference.mode !== "selection"
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

      setIsRunning(true);
      setError(null);
      setStatusLine(
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
          onSectionUpdated(result.draft, result.research);

          const changed = result.draft.sections.filter((s) => {
            const prev = beforeById.get(s.id);
            return prev !== undefined && (s.content || "") !== prev;
          });

          for (const s of changed) {
            onRevisionRecorded?.(s.id, {
              before: beforeById.get(s.id) || "",
              after: s.content || "",
              summary: result.assistantMessage,
              instruction: trimmed,
              updatedAt: Date.now(),
            });
          }

          const focusId =
            changed.find((s) => s.id === targetSection.id)?.id ||
            changed[0]?.id ||
            result.section?.id ||
            targetSection.id;
          onFocusSection?.(focusId);
          if (changed.length > 0) {
            onRevisionDrawerOpenChange?.(focusId, true);
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
        setIsRunning(false);
        setStatusLine(null);
        onBusyChange?.(false);
      }
    },
    [
      isRunning,
      messages,
      onBusyChange,
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
      if (isRunning || disabled) return;
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
      setIsRunning(true);
      setError(null);
      setStatusLine(`Applying fix on ${target.title}…`);
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
          onSectionUpdated(result.draft, result.research);
          const changed = result.draft.sections.filter((s) => {
            const prev = beforeById.get(s.id);
            return prev !== undefined && (s.content || "") !== prev;
          });
          for (const s of changed) {
            onRevisionRecorded?.(s.id, {
              before: beforeById.get(s.id) || "",
              after: s.content || "",
              summary: result.assistantMessage,
              instruction,
              updatedAt: Date.now(),
            });
          }
          const focusId =
            changed.find((s) => s.id === target.id)?.id ||
            changed[0]?.id ||
            target.id;
          onFocusSection?.(focusId);
          if (changed.length > 0) {
            onRevisionDrawerOpenChange?.(focusId, true);
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
        setIsRunning(false);
        setStatusLine(null);
        onBusyChange?.(false);
      }
    },
    [
      disabled,
      isRunning,
      messages,
      onBusyChange,
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

  if (sections.length === 0) return null;

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
    return QUICK_PROMPTS;
  }, [viewingSection?.title, reference?.sectionTitle]);

  const pinViewingSection = () => {
    if (!viewingSection || disabled || isRunning) return;
    onSetReference(
      buildSectionPinReference(viewingSection, viewingSection.content || "")
    );
    setInput("Improve this section for the RFP.");
  };

  const pinReviseSection = () => {
    if (!viewingSection || disabled || isRunning) return;
    onSetReference(
      buildSectionPinReference(viewingSection, viewingSection.content || "")
    );
    setInput("Revise this section.");
  };

  return (
    <aside className="proposal-section-chat" aria-label="Proposal assistant">
      <header className="proposal-section-chat-header">
        <div className="min-w-0 flex-1">
          <p className="proposal-section-chat-kicker">Proposal assistant</p>
        </div>
        {showClose && onClose ? (
          <button
            type="button"
            className="proposal-section-chat-icon-btn"
            aria-label="Close assistant"
            onClick={onClose}
          >
            ×
          </button>
        ) : null}
      </header>

      <div ref={scrollRef} className="proposal-section-chat-messages custom-scrollbar">
        {messages.length === 0 ? (
          <p className="text-zo-text-muted">
            Ask by section name, or say what to change (e.g. case studies). Edits stay on
            that tab unless you say <strong>across the proposal</strong> /{" "}
            <strong>every section</strong>. If I&apos;m unsure which tab, I&apos;ll ask.
            You can also pin with <strong>Revise content</strong> or{" "}
            <strong>Improve full section</strong>.
          </p>
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
                  <MarkdownReportBody body={msg.content} variant="chat" />
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
                            disabled={disabled || isRunning}
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
                              disabled={disabled || isRunning}
                              onClick={cancelApplyPanel}
                            >
                              Cancel
                            </button>
                            <button
                              type="button"
                              className="proposal-section-chat-apply-btn"
                              disabled={disabled || isRunning}
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
                            disabled={disabled || isRunning}
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
        {isRunning ? (
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
              <p className="proposal-section-chat-reference-text">“{reference.text}”</p>
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
            disabled={disabled || isRunning}
            rows={1}
            placeholder="Ask anything…"
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
            disabled={disabled || isRunning || !input.trim()}
            className="proposal-section-chat-send"
            aria-label="Send"
            onClick={() => void sendMessage(input)}
          >
            ↑
          </button>
        </div>

        <div className="proposal-section-chat-quick custom-scrollbar">
          {viewingSection ? (
            <>
              <button
                type="button"
                disabled={disabled || isRunning}
                onClick={pinReviseSection}
                className="proposal-section-chat-quick-btn proposal-section-chat-quick-btn--primary"
              >
                {REVISE_PIN_LABEL}
              </button>
              <button
                type="button"
                disabled={disabled || isRunning}
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
              disabled={disabled || isRunning}
              onClick={() => void sendMessage(prompt)}
              className="proposal-section-chat-quick-btn"
            >
              {prompt}
            </button>
          ))}
        </div>
      </div>
    </aside>
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
