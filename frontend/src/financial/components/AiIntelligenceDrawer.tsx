"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ArrowRight,
  ArrowUp,
  RotateCcw,
  Sparkles,
  Wand2,
  X,
} from "lucide-react";

import type { NoteBadge } from "../lib/qb-note-badges";
import type { QbChat } from "../lib/use-qb-chat";
import type { NoteCard, QbInsights } from "../lib/use-qb-insights";
import "./AiIntelligenceDrawer.css";

/**
 * The per-source wording. Everything else in this drawer — filters, cards,
 * pinning, the composer — is identical for QuickBooks and Teamwork, so the
 * component is shared and only these strings differ.
 */
export interface DrawerChrome {
  /** Shown under the title: which system the evidence came from. */
  source: string;
  /** Starter questions, offered until the reader asks their own. */
  seeds: string[];
  /** goTo id -> the label on the card's jump link. */
  viewLabel: Record<string, string>;
  placeholder: string;
  /** Copy for "nothing is wrong", which reads differently per source. */
  empty: string;
  /** Standing caveat above the cards, e.g. Teamwork capacity still building. */
  notice?: string | null;
}

export const QUICKBOOKS_CHROME: DrawerChrome = {
  source: "QuickBooks",
  seeds: [
    "What should I chase first?",
    "Why is margin unmeasurable?",
    "Summarize this for a partner call",
  ],
  viewLabel: {
    today: "Position",
    open: "Open",
    revenue: "Revenue",
    clients: "Clients",
    costs: "Costs",
  },
  placeholder: "Ask about the position…",
  empty: "Nothing flagged. Receivables are current and the books look clean.",
};

interface Props {
  open: boolean;
  onClose: () => void;
  insights: QbInsights;
  chat: QbChat;
  /** Navigate the ledger underneath, then close. */
  onGo: (view: string) => void;
  chrome?: DrawerChrome;
  /** Hide the chat composer (iWorker has no chat endpoint yet). */
  showChat?: boolean;
  /** Extra content below cards — e.g. audit queue with resolve actions. */
  feedExtra?: ReactNode;
}

export function AiIntelligenceDrawer({
  open,
  onClose,
  insights,
  chat,
  onGo,
  chrome = QUICKBOOKS_CHROME,
  showChat = true,
  feedExtra,
}: Props) {
  const { data, cards, counts, loaded, busy, error, regenerate } = insights;
  const [filter, setFilter] = useState<NoteBadge | null>(null);
  const [pinned, setPinned] = useState<NoteCard | null>(null);
  const [draft, setDraft] = useState("");
  const composer = useRef<HTMLTextAreaElement>(null);
  const feedEnd = useRef<HTMLDivElement>(null);

  // Escape closes from anywhere in the drawer, including the composer.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (open) composer.current?.focus();
  }, [open]);

  useEffect(() => {
    if (chat.turns.length) feedEnd.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat.turns.length, chat.busy]);

  const shown = useMemo(
    () => (filter ? cards.filter((c) => c.badge === filter) : cards),
    [cards, filter],
  );

  if (!open) return null;

  const ask = (text: string) => {
    void chat.send(text, pinned?.id ?? null);
    setDraft("");
    setPinned(null);
  };

  const submit = () => {
    if (draft.trim() && !chat.busy) ask(draft);
  };

  return (
    <>
      <button
        type="button"
        className="qb-ai-scrim"
        aria-label="Close AI Intelligence"
        onClick={onClose}
      />
      <aside
        className="qb-ai-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="AI Intelligence"
      >
        <header className="qb-ai-head">
          <span className="qb-ai-mark" aria-hidden>
            <Sparkles size={15} strokeWidth={2.25} />
          </span>
          <div className="qb-ai-title">
            <h2>AI Intelligence</h2>
            {data?.cadence === "weekly" && data.period_label ? (
              <p className="qb-ai-week-title">Weekly insights for {data.period_label}</p>
            ) : null}
            <p className="qb-ai-sub">
              <span>{chrome.source}</span>
              {data?.model ? <span>{data.model}</span> : null}
              {data?.cadence === "weekly" && data.current_week_label ? (
                <span>{data.current_week_label}</span>
              ) : null}
              {data?.as_of && (data.cadence !== "weekly" || data.stale) ? (
                <span data-stale={data.stale || undefined}>
                  {data.cadence === "weekly"
                    ? `Brief stale · ${data.as_of}`
                    : data.stale
                      ? `As of ${data.as_of}`
                      : `Today, ${data.as_of}`}
                </span>
              ) : null}
              {chat.costUsd > 0 ? <span>${chat.costUsd.toFixed(3)} this thread</span> : null}
            </p>
          </div>
          <div className="qb-ai-head-actions">
            <button
              type="button"
              className="qb-ai-icon-btn"
              onClick={() => void regenerate()}
              disabled={busy}
            >
              <RotateCcw size={13} strokeWidth={2.25} aria-hidden />
              {busy ? "Working…" : "Regenerate"}
            </button>
            <button
              type="button"
              className="qb-ai-icon-btn"
              onClick={onClose}
              aria-label="Close"
            >
              <X size={15} strokeWidth={2.25} aria-hidden />
            </button>
          </div>
        </header>

        {counts.length ? (
          <div className="qb-ai-filters">
            <button
              type="button"
              className="qb-ai-chip"
              aria-pressed={filter === null}
              onClick={() => setFilter(null)}
            >
              All <b>{cards.length}</b>
            </button>
            {counts.map(({ badge, count }) => (
              <button
                key={badge}
                type="button"
                className="qb-ai-chip"
                data-badge={badge}
                aria-pressed={filter === badge}
                onClick={() => setFilter(filter === badge ? null : badge)}
              >
                {badge} <b>{count}</b>
              </button>
            ))}
          </div>
        ) : null}

        <div className="qb-ai-feed">
          {error ? <p className="qb-ai-error">{error}</p> : null}

          {data?.brief ? (
            <p className="qb-ai-brief">{data.brief}</p>
          ) : loaded ? (
            <p className="qb-ai-empty">
              {data?.cadence === "weekly"
                ? "No weekly brief yet. Monday's job writes one, or generate it now."
                : "No brief yet. The nightly sync writes one, or generate it now."}
            </p>
          ) : null}

          {chrome.notice ? <p className="qb-ai-empty">{chrome.notice}</p> : null}

          {shown.length ? (
            <ol className="qb-ai-list">
              {shown.map((card) => (
                <li key={card.id}>
                  <button
                    type="button"
                    className="qb-note-card"
                    data-badge={card.badge}
                    onClick={() => {
                      setPinned(card);
                      composer.current?.focus();
                    }}
                  >
                    <span className="qb-note-meta">
                      <span className="qb-note-badge">{card.badge}</span>
                      {card.aiEnhanced ? (
                        <span className="qb-note-ai">
                          <Wand2 size={10} strokeWidth={2.25} aria-hidden />
                          AI
                        </span>
                      ) : null}
                      {card.figure ? (
                        <span className="qb-note-figure">{card.figure}</span>
                      ) : null}
                    </span>
                    <p className="qb-note-headline">{card.headline}</p>
                    {card.detail ? <p className="qb-note-detail">{card.detail}</p> : null}
                    {card.goTo ? (
                      <span
                        className="qb-note-go"
                        role="link"
                        tabIndex={0}
                        onClick={(e) => {
                          e.stopPropagation();
                          onGo(card.goTo!);
                          onClose();
                        }}
                        onKeyDown={(e) => {
                          if (e.key !== "Enter" && e.key !== " ") return;
                          e.stopPropagation();
                          e.preventDefault();
                          onGo(card.goTo!);
                          onClose();
                        }}
                      >
                        {chrome.viewLabel[card.goTo] ?? "Detail"}
                        <ArrowRight size={12} strokeWidth={2.25} aria-hidden />
                      </span>
                    ) : null}
                  </button>
                </li>
              ))}
            </ol>
          ) : loaded && !error ? (
            <p className="qb-ai-empty">
              {filter
                ? `Nothing tagged ${filter}.`
                : chrome.empty}
            </p>
          ) : null}

          {feedExtra}

          {chat.turns.length || chat.busy ? (
            <div className="qb-ai-turns">
              {chat.turns.map((turn) => (
                <p
                  key={turn.id}
                  className="qb-ai-msg"
                  data-role={turn.role}
                  data-guarded={turn.guarded || undefined}
                >
                  {turn.content}
                </p>
              ))}
              {chat.busy ? (
                <span className="qb-ai-thinking" role="status">
                  <i /><i /><i /> Reading the ledger
                </span>
              ) : null}
            </div>
          ) : null}
          <div ref={feedEnd} />
        </div>

        {showChat ? (
        <div className="qb-ai-composer">
          {!chat.turns.length && !chat.busy ? (
            <div className="qb-ai-seeds">
              {chrome.seeds.map((seed) => (
                <button
                  key={seed}
                  type="button"
                  className="qb-ai-seed"
                  onClick={() => ask(seed)}
                >
                  {seed}
                </button>
              ))}
            </div>
          ) : null}

          {pinned ? (
            <div className="qb-ai-pin">
              <span>Asking about: {pinned.headline}</span>
              <button type="button" onClick={() => setPinned(null)} aria-label="Unpin">
                <X size={13} strokeWidth={2.25} aria-hidden />
              </button>
            </div>
          ) : null}

          <div className="qb-ai-input">
            <textarea
              ref={composer}
              rows={1}
              value={draft}
              placeholder={chrome.placeholder}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
            />
            <button
              type="button"
              className="qb-ai-send"
              onClick={submit}
              disabled={!draft.trim() || chat.busy}
              aria-label="Send"
            >
              <ArrowUp size={15} strokeWidth={2.5} aria-hidden />
            </button>
          </div>
          <p className="qb-ai-foot">
            Answers come only from the figures on these cards. This conversation
            is not saved.
          </p>
        </div>
        ) : null}
      </aside>
    </>
  );
}
