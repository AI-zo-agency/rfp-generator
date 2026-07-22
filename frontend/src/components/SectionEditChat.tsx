"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { OutlineSection, ProposalOutline, ProposalResearch } from "@/types/proposal";
import { improveProposalSection } from "@/lib/proposal-api";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
}

interface SectionEditChatProps {
  rfpId: string;
  section: OutlineSection;
  disabled?: boolean;
  docked?: boolean;
  onSectionUpdated: (draft: ProposalOutline, research: ProposalResearch | null) => void;
}

const QUICK_PROMPTS = [
  "Check duplicates thoroughly.",
  "Remove fabricated content (content → RFP → KB).",
  "Fill [VERIFY] tags from KB only.",
  "Make this more specific to the client and less generic.",
];

export function SectionEditChat({
  rfpId,
  section,
  disabled,
  docked = false,
  onSectionUpdated,
}: SectionEditChatProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMessages([]);
    setInput("");
    setError(null);
  }, [section.id]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, isRunning]);

  const runImprove = useCallback(
    async (message: string) => {
      const trimmed = message.trim();
      if (!trimmed || isRunning) return;

      const userMsg: ChatMessage = {
        id: `u-${Date.now()}`,
        role: "user",
        content: trimmed,
      };
      setMessages((prev) => [...prev, userMsg]);
      setInput("");
      setIsRunning(true);
      setError(null);

      try {
        const result = await improveProposalSection(rfpId, section.id, trimmed);
        onSectionUpdated(result.draft, result.research);
        setMessages((prev) => [
          ...prev,
          {
            id: `a-${Date.now()}`,
            role: "assistant",
            content: result.assistantMessage,
          },
        ]);
      } catch (err) {
        const detail =
          err instanceof Error ? err.message : "Section improve failed";
        setError(detail);
        setMessages((prev) => [
          ...prev,
          { id: `e-${Date.now()}`, role: "assistant", content: `Error: ${detail}` },
        ]);
      } finally {
        setIsRunning(false);
      }
    },
    [rfpId, section.id, isRunning, onSectionUpdated],
  );

  return (
    <div
      className={
        docked
          ? "flex h-full min-h-0 flex-col"
          : "mt-8 rounded-xl border border-zo-border bg-[#fafbfc]"
      }
    >
      <div
        className={
          docked
            ? "shrink-0 border-b border-zo-border px-3 py-2"
            : "border-b border-zo-border px-4 py-3"
        }
      >
        <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-zo-orange">
          Section agent
        </p>
        {!docked ? (
          <p className="mt-1 text-xs text-zo-text-muted">
            Re-queries Supermemory with new detailed searches (never repeats prior
            queries), then rewrites only this section.
          </p>
        ) : null}
      </div>

      <div
        ref={scrollRef}
        className={`custom-scrollbar min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-2 ${
          docked ? "" : "max-h-48"
        }`}
      >
        {messages.length === 0 && (
          <p className="text-xs text-zo-text-muted">
            Ask the agent to improve this section, or use a quick prompt below.
          </p>
        )}
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`rounded-lg px-3 py-2 text-sm leading-relaxed ${
              msg.role === "user"
                ? "ml-4 bg-[#ef5018]/10 text-foreground"
                : "mr-4 bg-white text-zo-text-secondary"
            }`}
          >
            {msg.content}
          </div>
        ))}
        {isRunning ? (
          <p className="text-xs font-medium text-zo-orange">
            Searching KB with new queries and rewriting section…
          </p>
        ) : null}
      </div>

      {error ? (
        <p className="shrink-0 px-3 pb-1 text-xs text-zo-error">{error}</p>
      ) : null}

      <div className="custom-scrollbar shrink-0 overflow-x-auto border-t border-zo-border px-3 py-1.5">
        <div className="flex w-max min-w-full gap-1.5">
          {QUICK_PROMPTS.map((prompt) => (
            <button
              key={prompt}
              type="button"
              disabled={disabled || isRunning}
              onClick={() => void runImprove(prompt)}
              className="shrink-0 rounded-full border border-zo-border bg-white px-2.5 py-1 text-[10px] text-zo-text-secondary transition-smooth hover:border-zo-orange hover:text-zo-orange disabled:opacity-50"
            >
              {prompt.length > 42 ? `${prompt.slice(0, 42)}…` : prompt}
            </button>
          ))}
        </div>
      </div>

      <form
        className="flex shrink-0 gap-2 border-t border-zo-border p-2.5"
        onSubmit={(e) => {
          e.preventDefault();
          void runImprove(input);
        }}
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={disabled || isRunning}
          placeholder="Ask to improve this section…"
          className="min-w-0 flex-1 zo-input px-3 py-2 text-sm outline-none focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10"
        />
        <button
          type="submit"
          disabled={disabled || isRunning || !input.trim()}
          className="zo-btn shrink-0 !px-3 !py-2 disabled:opacity-50"
        >
          {isRunning ? "…" : "Send"}
        </button>
      </form>
    </div>
  );
}
