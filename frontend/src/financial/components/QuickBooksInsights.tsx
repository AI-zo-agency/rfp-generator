"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowRight, Info, RotateCcw } from "lucide-react";

import {
  badgeForRow,
  badgeForSignal,
  type NoteBadge,
  type NoteRowKind,
} from "../lib/qb-note-badges";
import type { Signal } from "../types/quickbooks";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";

const VIEW_LABEL: Record<string, string> = {
  today: "Position",
  open: "Open",
  revenue: "Revenue",
  clients: "Clients",
  costs: "Costs",
};

interface ChaseRow {
  id: string;
  client: string;
  /** The past-due portion only — not the whole balance. */
  overdue_figure: string;
  /** Age of the oldest overdue invoice. */
  overdue_days: number;
  /** Dollar-weighted mean age. This is the one that pairs truthfully with the
   *  amount: OCF's invoices run 24–73 days, so "$11,966, 73d late" is false. */
  avg_overdue_days: number;
  balance_figure: string;
  invoice_count: number;
  slow_payer: boolean;
}

interface MarginRow {
  id: string;
  label: string;
  figure: string;
  detail: string;
  kind: string;
}

interface HygieneRow {
  id: string;
  label: string;
  amount: number;
  figure: string;
  kind: string;
}

interface InsightsData {
  status: "ok" | "empty";
  brief: string;
  notes: Record<string, string>;
  chase: ChaseRow[];
  margin: MarginRow[];
  hygiene: HygieneRow[];
  as_of: string | null;
  generated_at: string | null;
  provider: string | null;
  stale: boolean;
  /** Only present on the regenerate response: "ok" | "failed". */
  generated?: string;
}

interface NoteCard {
  id: string;
  headline: string;
  detail: string;
  figure?: string;
  badge: NoteBadge;
  /** True when the model wrote a note for this row. */
  aiEnhanced: boolean;
  goTo?: string;
}

/**
 * Cash first, then what shapes profit, then the bookkeeping that makes both
 * measurable. Rule-based signals follow — they size the same problems without
 * duplicating a factual row id (chase:… vs ar-late).
 */
function buildNoteCards(data: InsightsData | null, signals: Signal[]): NoteCard[] {
  const cards: NoteCard[] = [];
  const notes = data?.notes ?? {};

  for (const row of data?.chase ?? []) {
    const kind: NoteRowKind = "collect";
    const meta = [
      `${row.avg_overdue_days}d overdue`,
      row.overdue_days !== row.avg_overdue_days ? `oldest ${row.overdue_days}d` : null,
      `${row.invoice_count} ${row.invoice_count === 1 ? "invoice" : "invoices"}`,
      row.balance_figure !== row.overdue_figure ? `${row.balance_figure} owed` : null,
    ]
      .filter(Boolean)
      .join(" · ");
    const note = notes[row.id];
    cards.push({
      id: row.id,
      headline: row.client,
      detail: [meta, note].filter(Boolean).join(" — "),
      figure: row.overdue_figure,
      badge: badgeForRow(kind, row.slow_payer),
      aiEnhanced: Boolean(note),
    });
  }

  for (const row of data?.margin ?? []) {
    const kind: NoteRowKind = "margin";
    const note = notes[row.id];
    cards.push({
      id: row.id,
      headline: row.label,
      detail: [row.detail, note].filter(Boolean).join(" — "),
      figure: row.figure,
      badge: badgeForRow(kind),
      aiEnhanced: Boolean(note),
    });
  }

  for (const row of data?.hygiene ?? []) {
    const kind: NoteRowKind = "fix";
    const note = notes[row.id];
    cards.push({
      id: row.id,
      headline: row.label,
      detail: note ?? "",
      figure: row.figure,
      badge: badgeForRow(kind),
      aiEnhanced: Boolean(note),
    });
  }

  const rowIds = new Set(cards.map((c) => c.id));
  for (const s of signals) {
    if (rowIds.has(s.id)) continue;
    cards.push({
      id: s.id,
      headline: s.headline,
      detail: s.detail ?? "",
      figure: s.figure,
      badge: badgeForSignal(s.id, s.severity),
      aiEnhanced: false,
      goTo: s.go_to,
    });
  }

  return cards;
}

export function QuickBooksInsights({
  signals,
  onGo,
}: Readonly<{
  signals: Signal[];
  onGo: (view: string) => void;
}>) {
  const [data, setData] = useState<InsightsData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/financials/quickbooks/ai-insights`);
      if (!res.ok) throw new Error(`${res.status}`);
      setData(await res.json());
      setError(null);
    } catch {
      setError("Couldn't load insights.");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const regenerate = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/financials/quickbooks/ai-insights/regenerate`,
        { method: "POST" },
      );
      if (!res.ok) throw new Error(`${res.status}`);
      const next: InsightsData = await res.json();
      setData(next);
      if (next.generated === "failed") {
        setError("Couldn't generate a new brief. The notes below are current.");
      }
    } catch {
      setError("Couldn't generate a new brief. The notes below are current.");
    } finally {
      setBusy(false);
    }
  };

  const cards = buildNoteCards(data, signals);

  // Wait for the AI fetch unless rule cards already give us something to show.
  if (!loaded && !signals.length) {
    return error ? <p className="qb-insights-error" role="status">{error}</p> : null;
  }

  return (
    <section className="qb-insights" aria-busy={busy || undefined} aria-live="polite">
      <header className="qb-insights-head">
        <h3>
          Notes
          <span
            className="qb-insights-info"
            title="Notes combine threshold alerts and ledger rows. Cards tagged Enhanced by AI include a model-written take."
          >
            <Info size={13} strokeWidth={2.25} aria-hidden />
            <span className="qb-sr">About notes</span>
          </span>
        </h3>
        <div className="qb-insights-meta">
          {data?.as_of ? (
            <span data-stale={data.stale || undefined}>
              {data.stale ? `As of ${data.as_of}` : `Today, ${data.as_of}`}
            </span>
          ) : null}
          <button type="button" onClick={regenerate} disabled={busy}>
            <RotateCcw size={13} strokeWidth={2.25} aria-hidden />
            {busy ? "Working…" : "Regenerate"}
          </button>
        </div>
      </header>

      {error ? <p className="qb-insights-error">{error}</p> : null}

      {data?.brief ? (
        <p className="qb-insights-brief">{data.brief}</p>
      ) : loaded ? (
        <p className="qb-insights-brief qb-insights-empty">
          No brief yet. The nightly sync writes one, or generate it now.
        </p>
      ) : null}

      {cards.length ? (
        <ol className="qb-insights-list">
          {cards.map((card) => (
            <li
              key={card.id}
              className="qb-note-card"
              data-badge={card.badge}
              data-ai={card.aiEnhanced || undefined}
            >
              <div className="qb-note-card-top">
                <span className="qb-note-badge">{card.badge}</span>
                {card.aiEnhanced ? (
                  <span className="qb-note-ai">Enhanced by AI</span>
                ) : null}
              </div>
              <div className="qb-note-card-body">
                <div className="qb-note-copy">
                  <p className="qb-note-headline">{card.headline}</p>
                  {card.detail ? <p className="qb-note-detail">{card.detail}</p> : null}
                </div>
                {card.figure ? <span className="qb-note-figure">{card.figure}</span> : null}
              </div>
              {card.goTo ? (
                <button
                  type="button"
                  className="qb-note-go"
                  onClick={() => onGo(card.goTo!)}
                >
                  <span>{VIEW_LABEL[card.goTo] ?? "Detail"}</span>
                  <ArrowRight size={13} strokeWidth={2.25} aria-hidden />
                </button>
              ) : null}
            </li>
          ))}
        </ol>
      ) : loaded && !error ? (
        <p className="qb-insights-brief qb-insights-empty">
          Nothing flagged. Receivables are current and the books look clean.
        </p>
      ) : null}
    </section>
  );
}
