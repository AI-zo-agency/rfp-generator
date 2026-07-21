"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { improveProposalSection } from "@/lib/proposal-api";
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
  "Does this meet the RFP?",
  "Fill [VERIFY] tags from KB only.",
  "More client-specific — less generic.",
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

/** Resolve which section the user means from their message (no dropdown). */
export function resolveSectionFromMention(
  sections: OutlineSection[],
  message: string,
  fallbackId: string | null
): OutlineSection | null {
  const text = message.trim();
  if (!text || sections.length === 0) {
    return sections.find((s) => s.id === fallbackId) ?? sections[0] ?? null;
  }
  const lower = text.toLowerCase();

  // Prefer longer title matches first
  const byTitle = [...sections].sort(
    (a, b) => (b.title?.length ?? 0) - (a.title?.length ?? 0)
  );
  for (const section of byTitle) {
    const title = (section.title || "").trim();
    if (title.length >= 4 && lower.includes(title.toLowerCase())) {
      return section;
    }
  }

  // "1.1", "section 3", "§ 2.1"
  const numMatch = lower.match(
    /\b(?:section\s*)?(\d+(?:\.\d+)?)\b|\b(\d+\.\d+)\s*[—–-]/
  );
  const num = numMatch?.[1] || numMatch?.[2];
  if (num) {
    const hit = sections.find((s) => {
      const t = (s.title || "").toLowerCase();
      return (
        t.startsWith(`${num} `) ||
        t.startsWith(`${num}—`) ||
        t.startsWith(`${num}–`) ||
        t.startsWith(`${num} -`) ||
        t.includes(` ${num} `) ||
        t.startsWith(num)
      );
    });
    if (hit) return hit;
  }

  return sections.find((s) => s.id === fallbackId) ?? sections[0] ?? null;
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

      // Revise content / Improve full section pins the excerpt target.
      // Otherwise: parse any section the user names; else the section they're viewing.
      const targetSection = reference?.sectionId
        ? sections.find((s) => s.id === reference.sectionId) ??
          resolveSectionFromMention(sections, trimmed, viewingSectionId)
        : resolveSectionFromMention(sections, trimmed, viewingSectionId);

      if (!targetSection) return;

      const userMsg: SectionChatMessage = {
        id: `u-${Date.now()}`,
        role: "user",
        content: trimmed,
      };
      const nextMessages = [...messages, userMsg];
      onMessagesChange(nextMessages);
      setInput("");
      setIsRunning(true);
      setError(null);
      setStatusLine(
        reference?.mode === "selection"
          ? `Editing excerpt in ${targetSection.title}…`
          : reference?.mode === "section" &&
              reference.sectionId === targetSection.id
            ? `Improving ${targetSection.title}…`
            : `Reading full proposal · focusing ${targetSection.title}…`
      );
      onBusyChange?.(true);

      const contentBefore = targetSection.content;
      const selectionForRequest =
        reference?.mode === "selection" && reference.sectionId === targetSection.id
          ? reference.selection
          : undefined;

      try {
        const history = nextMessages.slice(0, -1).map((m) => ({
          role: m.role,
          content: m.content,
        }));
        const result = await improveProposalSection(rfpId, targetSection.id, trimmed, {
          selection: selectionForRequest,
          conversationHistory: history,
          proposalWide: !selectionForRequest,
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
          onSectionUpdated(result.draft, result.research);
          const contentAfter = result.section.content ?? contentBefore;
          onFocusSection?.(targetSection.id);
          onRevisionRecorded?.(targetSection.id, {
            before: contentBefore,
            after: contentAfter,
            summary: result.assistantMessage,
            instruction: trimmed,
            updatedAt: Date.now(),
          });
          onRevisionDrawerOpenChange?.(targetSection.id, true);
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
            Ask about the proposal or request an edit by section name.
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
