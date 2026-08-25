import { describe, expect, it } from "vitest";

import { BADGE_ORDER, buildNoteCards, countByBadge, type InsightsData } from "./use-qb-insights";
import type { Signal } from "../types/quickbooks";

function data(over: Partial<InsightsData> = {}): InsightsData {
  return {
    status: "ok",
    brief: "",
    notes: {},
    chase: [],
    margin: [],
    hygiene: [],
    as_of: "2026-08-24",
    generated_at: null,
    provider: null,
    stale: false,
    ...over,
  };
}

const chaseRow = {
  id: "chase:ocf",
  client: "Oregon-Canadian Forest Products, Inc.",
  overdue_figure: "$11,966",
  overdue_days: 73,
  avg_overdue_days: 47,
  balance_figure: "$11,966",
  invoice_count: 8,
  slow_payer: false,
};

describe("buildNoteCards", () => {
  it("puts cash before margin before bookkeeping", () => {
    const cards = buildNoteCards(
      data({
        chase: [chaseRow],
        margin: [{ id: "m1", label: "Revenue", figure: "+42%", detail: "d", kind: "k" }],
        hygiene: [{ id: "h1", label: "Unclassified", amount: 1, figure: "$1", kind: "k" }],
      }),
      [],
    );
    expect(cards.map((c) => c.id)).toEqual(["chase:ocf", "m1", "h1"]);
  });

  it("shows the oldest invoice age only when it differs from the weighted mean", () => {
    const [both] = buildNoteCards(data({ chase: [chaseRow] }), []);
    expect(both.detail).toContain("47d overdue");
    expect(both.detail).toContain("oldest 73d");

    const [same] = buildNoteCards(
      data({ chase: [{ ...chaseRow, overdue_days: 47 }] }),
      [],
    );
    expect(same.detail).toContain("47d overdue");
    expect(same.detail).not.toContain("oldest");
  });

  it("marks a row as AI-enhanced only when a note was written for it", () => {
    const [without] = buildNoteCards(data({ chase: [chaseRow] }), []);
    expect(without.aiEnhanced).toBe(false);

    const [withNote] = buildNoteCards(
      data({ chase: [chaseRow], notes: { "chase:ocf": "Call them today." } }),
      [],
    );
    expect(withNote.aiEnhanced).toBe(true);
    expect(withNote.detail).toContain("Call them today.");
  });

  it("drops a signal that duplicates a factual row it already listed", () => {
    const signals: Signal[] = [
      { id: "chase:ocf", severity: "critical", headline: "dupe" },
      { id: "ar-late", severity: "critical", headline: "Receivables have aged", go_to: "open" },
    ];
    const cards = buildNoteCards(data({ chase: [chaseRow] }), signals);
    expect(cards.map((c) => c.id)).toEqual(["chase:ocf", "ar-late"]);
    expect(cards[1].goTo).toBe("open");
  });

  it("pluralizes the invoice count", () => {
    const [one] = buildNoteCards(
      data({ chase: [{ ...chaseRow, invoice_count: 1 }] }),
      [],
    );
    expect(one.detail).toContain("1 invoice");
    expect(one.detail).not.toContain("1 invoices");
  });

  it("names the balance only when it is more than what is overdue", () => {
    const [partial] = buildNoteCards(
      data({ chase: [{ ...chaseRow, balance_figure: "$20,000" }] }),
      [],
    );
    expect(partial.detail).toContain("$20,000 owed");
    expect(partial.figure).toBe("$11,966");
  });

  it("returns nothing when there is neither data nor a signal", () => {
    expect(buildNoteCards(null, [])).toEqual([]);
  });
});

describe("countByBadge", () => {
  it("counts in urgency order and omits badges nothing landed on", () => {
    const cards = buildNoteCards(
      data({
        chase: [chaseRow, { ...chaseRow, id: "chase:b", slow_payer: true }],
        margin: [{ id: "m1", label: "Revenue", figure: "+42%", detail: "d", kind: "k" }],
      }),
      [],
    );
    expect(countByBadge(cards)).toEqual([
      { badge: "High impact", count: 1 },
      { badge: "Watch", count: 1 },
      { badge: "Opportunity", count: 1 },
    ]);
  });

  it("leads with the badge the trigger button counts", () => {
    expect(BADGE_ORDER[0]).toBe("High impact");
  });

  it("counts nothing without cards", () => {
    expect(countByBadge([])).toEqual([]);
  });
});

/**
 * Teamwork reuses these cards, but its evidence arrives differently: the
 * signals ride on the insight response rather than the overview, and the
 * model's notes key to signal ids because that is all Teamwork lets it
 * annotate. QuickBooks keys notes to row ids, so the two must not collide.
 */
describe("teamwork source", () => {
  const twSignal = (over: Partial<Signal> = {}): Signal =>
    ({
      id: "late-milestones",
      severity: "critical",
      headline: "Milestones are late",
      figure: "10 milestones",
      detail: "Confirm the recovery plan.",
      go_to: "projects",
      ...over,
    }) as Signal;

  it("reads signals off the response, not the passed list", () => {
    const cards = buildNoteCards(
      data({ chase: undefined, signals: [twSignal()] }),
      [],
      "teamwork",
    );
    expect(cards.map((c) => c.id)).toEqual(["late-milestones"]);
    expect(cards[0].goTo).toBe("projects");
  });

  it("attaches the model note to its signal and flags it as AI", () => {
    const [card] = buildNoteCards(
      data({
        signals: [twSignal()],
        notes: { "late-milestones": "Delivery dates slip next." },
      }),
      [],
      "teamwork",
    );
    expect(card.aiEnhanced).toBe(true);
    expect(card.detail).toBe("Confirm the recovery plan. — Delivery dates slip next.");
  });

  it("treats a warn as a Risk, not a Watch", () => {
    const [card] = buildNoteCards(
      data({ signals: [twSignal({ id: "deadline-pressure", severity: "warn" })] }),
      [],
      "teamwork",
    );
    expect(card.badge).toBe("Risk");
  });

  it("leaves an unannotated signal plain", () => {
    const [card] = buildNoteCards(data({ signals: [twSignal()] }), [], "teamwork");
    expect(card.aiEnhanced).toBe(false);
    expect(card.detail).toBe("Confirm the recovery plan.");
  });
});

/**
 * Teamwork derives some cards in the browser and receives others on the
 * insight response. Both used to render — the drawer's list beside an inline
 * "Needs attention" panel — disagreeing on how many items existed and, for
 * late-milestones, on severity. They are merged into one list now.
 */
describe("teamwork signal merge", () => {
  const server: Signal = {
    id: "late-milestones",
    severity: "critical",
    headline: "Milestones are late",
    figure: "10 milestones",
    go_to: "projects",
  } as Signal;
  const local: Signal = {
    id: "late-milestones",
    severity: "warn",
    headline: "10 late milestones",
    figure: "10",
    go_to: "work",
  } as Signal;
  const localOnly: Signal = {
    id: "unbudgeted-projects",
    severity: "warn",
    headline: "8 projects have no financial budget",
    figure: "8",
    go_to: "projects",
  } as Signal;

  it("shows a shared id once, not twice", () => {
    const cards = buildNoteCards(data({ signals: [server] }), [local], "teamwork");
    expect(cards.filter((c) => c.id === "late-milestones")).toHaveLength(1);
  });

  it("lets the server win the shared id — it is what the model annotated", () => {
    const [card] = buildNoteCards(
      data({ signals: [server], notes: { "late-milestones": "Dates slip." } }),
      [local],
      "teamwork",
    );
    expect(card.headline).toBe("Milestones are late");
    expect(card.badge).toBe("High impact");
    expect(card.aiEnhanced).toBe(true);
  });

  it("keeps cards only the browser derived", () => {
    const cards = buildNoteCards(data({ signals: [server] }), [local, localOnly], "teamwork");
    expect(cards.map((c) => c.id)).toEqual(["late-milestones", "unbudgeted-projects"]);
  });

  it("leaves quickbooks on the overview list alone", () => {
    const cards = buildNoteCards(data({ signals: undefined }), [localOnly]);
    expect(cards.map((c) => c.id)).toEqual(["unbudgeted-projects"]);
  });
});
