"use client";

import { useCallback, useRef, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";

export interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** The answer was withheld because it could not be backed by the ledger. */
  guarded?: boolean;
  /** The thread hit its spending limit. */
  capped?: boolean;
}

interface ChatResponse {
  reply: string;
  thread_id: string;
  guarded: boolean;
  truncated: boolean;
  capped: boolean;
  cost_usd: number;
}

export interface QbChat {
  turns: ChatTurn[];
  busy: boolean;
  costUsd: number;
  send: (message: string, focusId?: string | null) => Promise<void>;
  reset: () => void;
}

let seq = 0;
const nextId = () => `t${++seq}`;

/**
 * The conversation, held for the life of the page.
 *
 * Kept here rather than inside the drawer so closing the drawer does not throw
 * the thread away. It still does not survive a reload — that arrives with the
 * `qb_chat_threads` table, and this hook is where the source of `turns` swaps
 * from local state to the server's stored history.
 */
export function useQbChat(): QbChat {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [busy, setBusy] = useState(false);
  const [costUsd, setCostUsd] = useState(0);
  const threadId = useRef<string | null>(null);

  const send = useCallback(async (message: string, focusId?: string | null) => {
    const question = message.trim();
    if (!question || busy) return;

    // The history the server sees is the thread before this question — the
    // question itself travels in `message`, so sending both would duplicate it.
    const history = turns.map((t) => ({ role: t.role, content: t.content }));
    setTurns((prev) => [...prev, { id: nextId(), role: "user", content: question }]);
    setBusy(true);

    try {
      const res = await fetch(
        `${API_BASE}/api/v1/financials/quickbooks/ai-insights/chat`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: question,
            thread_id: threadId.current,
            focus_id: focusId ?? null,
            messages: history,
          }),
        },
      );
      if (!res.ok) throw new Error(`${res.status}`);
      const data: ChatResponse = await res.json();
      threadId.current = data.thread_id;
      setCostUsd(data.cost_usd);
      setTurns((prev) => [
        ...prev,
        {
          id: nextId(),
          role: "assistant",
          content: data.reply,
          guarded: data.guarded,
          capped: data.capped,
        },
      ]);
    } catch {
      setTurns((prev) => [
        ...prev,
        {
          id: nextId(),
          role: "assistant",
          content: "Something went wrong reaching the answer. Try again.",
          guarded: true,
        },
      ]);
    } finally {
      setBusy(false);
    }
  }, [busy, turns]);

  const reset = useCallback(() => {
    threadId.current = null;
    setTurns([]);
    setCostUsd(0);
  }, []);

  return { turns, busy, costUsd, send, reset };
}
