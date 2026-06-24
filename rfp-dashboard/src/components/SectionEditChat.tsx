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
  onSectionUpdated: (draft: ProposalOutline, research: ProposalResearch | null) => void;
}

const QUICK_PROMPTS = [
  "This section is not well — re-search with more detailed queries and rewrite.",
  "Find firm history, employee count, and organizational structure in the KB.",
  "Add more case studies and verified outcomes relevant to this RFP.",
  "Make this more specific to the client and less generic.",
];

export function SectionEditChat({
  rfpId,
  section,
  disabled,
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
    [rfpId, section.id, isRunning, onSectionUpdated]
  );

  return (
    <div className="mt-8 rounded-xl border border-zo-border bg-[#fafbfc]">
      <div className="border-b border-zo-border px-4 py-3">
        <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-zo-orange">
          Section agent
        </p>
        <p className="mt-1 text-xs text-zo-text-muted">
          Re-queries Supermemory with new detailed searches (never repeats prior
          queries), then rewrites only this section.
        </p>
      </div>

      <div
        ref={scrollRef}
        className="custom-scrollbar max-h-48 space-y-3 overflow-y-auto px-4 py-3"
      >
        {messages.length === 0 && (
          <p className="text-xs text-zo-text-muted">
            Tell the agent what is wrong — e.g. &quot;not well, need firm history
            and org chart&quot; — or use a quick prompt below.
          </p>
        )}
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`rounded-lg px-3 py-2 text-sm leading-relaxed ${
              msg.role === "user"
                ? "ml-6 bg-[#ef5018]/10 text-foreground"
                : "mr-6 bg-white text-zo-text-secondary"
            }`}
          >
            {msg.content}
          </div>
        ))}
        {isRunning && (
          <p className="text-xs font-medium text-zo-orange">
            Searching KB with new queries and rewriting section…
          </p>
        )}
      </div>

      {error && (
        <p className="px-4 pb-2 text-xs text-zo-error">{error}</p>
      )}

      <div className="flex flex-wrap gap-2 border-t border-zo-border px-4 py-2">
        {QUICK_PROMPTS.map((prompt) => (
          <button
            key={prompt}
            type="button"
            disabled={disabled || isRunning}
            onClick={() => void runImprove(prompt)}
            className="rounded-full border border-zo-border bg-white px-3 py-1 text-[11px] text-zo-text-secondary transition-smooth hover:border-zo-orange hover:text-zo-orange disabled:opacity-50"
          >
            {prompt.length > 48 ? `${prompt.slice(0, 48)}…` : prompt}
          </button>
        ))}
      </div>

      <form
        className="flex gap-2 border-t border-zo-border p-3"
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
          className="min-w-0 flex-1 zo-input px-3 py-2.5 text-sm outline-none focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10"
        />
        <button
          type="submit"
          disabled={disabled || isRunning || !input.trim()}
          className="zo-btn shrink-0 !px-4 !py-2.5 disabled:opacity-50"
        >
          {isRunning ? "…" : "Send"}
        </button>
      </form>
    </div>
  );
}
