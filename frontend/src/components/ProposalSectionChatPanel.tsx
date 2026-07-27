"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { improveProposalSection } from "@/lib/proposal-api";
import {
  messageLooksOutlineStructure,
  messageLooksProposalWide,
  messageLooksStructural,
  messageTargetsBios,
  pinnedSectionConflictsWithMessage,
  resolveChatTarget,
} from "@/lib/proposal-section-resolve";
import type { OutlineSection, ProposalOutline, ProposalResearch } from "@/types/proposal";
import type { SectionRevisionRecord } from "./DraftSectionEditor";

export interface SectionChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
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
  "Check duplicates thoroughly.",
  "Remove fabricated content (content → RFP → KB).",
  "Fill [VERIFY] tags from KB only.",
  "Remove [VERIFY] tags if not required by the RFP.",
  "Does this meet the RFP?",
];

const SECTION_PIN_LABEL = "Improve this section";

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
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, isRunning]);

  useEffect(() => {
    if (reference?.text) {
      window.setTimeout(() => inputRef.current?.focus(), 60);
    }
    if (reference?.mode === "section") {
      setInput((prev) =>
        prev.trim() ? prev : "Improve this section for the RFP."
      );
    }
  }, [reference?.text, reference?.sectionId, reference?.mode]);

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
        pinnedSectionConflictsWithMessage(trimmed, activeReference.sectionId)
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

      const targetSection = resolution.section;
      // Stale Improve pin on another tab must not keep redirecting status/API.
      if (
        activeReference &&
        activeReference.sectionId !== targetSection.id &&
        resolution.reason !== "pinned"
      ) {
        activeReference = null;
        onSetReference(null);
      }

      const proposalWideAsk =
        resolution.reason === "proposal-wide" ||
        resolution.reason === "outline-structure" ||
        messageLooksProposalWide(trimmed);

      setIsRunning(true);
      setError(null);
      setStatusLine(
        proposalWideAsk
          ? "Reviewing the full proposal…"
          : /apply these fixes|patch-wise across|across the proposal/i.test(trimmed)
            ? "Applying patch-wise fixes across the proposal…"
            : activeReference?.mode === "selection" &&
                activeReference.sectionId === targetSection.id
              ? `Editing excerpt in ${targetSection.title}…`
              : activeReference?.mode === "section" &&
                  activeReference.sectionId === targetSection.id
                ? `Improving ${targetSection.title}…`
                : messageLooksOutlineStructure(trimmed) ||
                    messageLooksStructural(trimmed) ||
                    messageTargetsBios(trimmed)
                  ? `Updating proposal outline…`
                  : `Working on ${targetSection.title}…`
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
          // Always send whole-proposal context unless editing a pinned excerpt.
          proposalWide: true,
        });

        onMessagesChange([
          ...nextMessages,
          {
            id: `a-${Date.now()}`,
            role: "assistant",
            content: result.assistantMessage,
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
        setError(detail);
        onMessagesChange([
          ...nextMessages,
          { id: `e-${Date.now()}`, role: "assistant", content: `Error: ${detail}` },
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

  if (sections.length === 0) return null;

  const viewingSection =
    sections.find((s) => s.id === viewingSectionId) ?? sections[0] ?? null;

  const pinViewingSection = () => {
    if (!viewingSection || disabled || isRunning) return;
    onSetReference(
      buildSectionPinReference(viewingSection, viewingSection.content || "")
    );
    setInput("Improve this section for the RFP.");
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
            Ask by section name, or say what to change (e.g. case studies). If I&apos;m unsure
            which tab, I&apos;ll ask. You can also pin with{" "}
            <strong>Revise content</strong> or <strong>Improve full section</strong>.
          </p>
        ) : (
          messages.map((msg) => (
            <div
              key={msg.id}
              className={`proposal-section-chat-bubble proposal-section-chat-bubble--${msg.role}`}
            >
              {msg.content}
            </div>
          ))
        )}
        {isRunning ? (
          <p className="text-sm font-medium text-zo-orange">
            {statusLine ?? "Scanning RFP + proposal…"}
          </p>
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
            <button
              type="button"
              disabled={disabled || isRunning}
              onClick={pinViewingSection}
              className="proposal-section-chat-quick-btn proposal-section-chat-quick-btn--primary"
            >
              {SECTION_PIN_LABEL}
            </button>
          ) : null}
          {QUICK_PROMPTS.map((prompt) => (
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
