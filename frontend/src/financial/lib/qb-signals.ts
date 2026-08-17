/**
 * Turns the QuickBooks overview payload into a short list of things a human
 * should actually do something about.
 *
 * The old dashboard rendered every number it had and left the reader to work
 * out which ones were bad. Everything here answers that question instead: a
 * signal only exists once a threshold is crossed, and it says so in a sentence.
 *
 * Pure functions, no React. Self-check at the bottom: `npx tsx src/financial/lib/qb-signals.ts`
 */

import type { QuickBooksOverview } from "../types/quickbooks";

export type Severity = "critical" | "warn" | "info";

export interface Signal {
  id: string;
  severity: Severity;
  /** Plain-English statement of the problem. No jargon, no metric names. */
  headline: string;
  /** The one number that sizes the problem. Pre-formatted. */
  figure?: string;
  /** Why it matters or what to do. One clause. */
  detail?: string;
  /** Tab id to jump to for the underlying rows. */
  goTo?: string;
}

const SEVERITY_RANK: Record<Severity, number> = { critical: 0, warn: 1, info: 2 };

const usd = (n: number) =>
  n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });

const plural = (n: number, one: string, many = `${one}s`) => (n === 1 ? one : many);

/** Buckets QuickBooks reports as genuinely late, not merely outstanding. */
const LATE_BUCKETS = new Set(["61-90 days", "90+ days"]);

export function deriveSignals(data: QuickBooksOverview): Signal[] {
  const out: Signal[] = [];

  // 1. Receivables that have aged past the point of "they'll get to it".
  const ar = data.ar;
  if (ar && ar.total > 0) {
    const late = ar.buckets
      .filter((b) => LATE_BUCKETS.has(b.label))
      .reduce((sum, b) => sum + b.amount, 0);
    if (late > 0) {
      const share = late / ar.total;
      const worst = [...ar.clients].sort(
        (a, b) => (b.oldest_days ?? 0) - (a.oldest_days ?? 0),
      )[0];
      out.push({
        id: "ar-late",
        severity: share >= 0.1 ? "critical" : "warn",
        headline: "Receivables have aged past 60 days",
        figure: usd(late),
        detail: worst
          ? `${Math.round(share * 100)}% of what's owed. Oldest is ${worst.client} at ${worst.oldest_days} days.`
          : `${Math.round(share * 100)}% of what's owed.`,
        goTo: "open",
      });
    }
  }

  // 2. Payables exceed the cash on hand to cover them.
  const cash = data.liquidity?.cash;
  const ap = data.ap?.total ?? 0;
  if (cash != null && cash > 0 && ap > cash) {
    out.push({
      id: "ap-over-cash",
      severity: "critical",
      headline: "You owe more than you're holding",
      figure: usd(ap - cash),
      detail: `${usd(ap)} in bills against ${usd(cash)} cash. Collections need to land before these do.`,
      goTo: "open",
    });
  }

  // 3. Clients who take far longer than everyone else to pay.
  const dso = data.dso;
  if (dso?.dso_days != null && dso.slowest_clients.length) {
    const threshold = Math.max(dso.dso_days * 1.75, 40);
    const slow = dso.slowest_clients.filter((c) => c.avg_days >= threshold);
    if (slow.length) {
      const tied = slow.reduce((sum, c) => sum + c.amount, 0);
      out.push({
        id: "slow-payers",
        severity: "warn",
        headline: `${slow.length} ${plural(slow.length, "client")} pay well after everyone else`,
        figure: usd(tied),
        detail: `${Math.round(threshold)}+ days versus a ${dso.dso_days}-day average.`,
        goTo: "clients",
      });
    }
  }

  // 4. Cost that can't be attributed to a client — the reason margins read high.
  const unattached = data.unattached_cost;
  if (unattached && unattached.unattached_pct >= 25) {
    out.push({
      id: "cost-untagged",
      severity: unattached.unattached_pct >= 50 ? "warn" : "info",
      headline: `${Math.round(unattached.unattached_pct)}% of purchases aren't tied to a client`,
      figure: usd(unattached.cost_of_service_unattached),
      detail:
        "Billable cost with nowhere to land, so per-client margin reads higher than it is.",
      goTo: "costs",
    });
  }

  // 5. Income landing outside any revenue segment.
  const coverage =
    data.revenue_by_class?.coverage_pct ?? data.class_coverage?.coverage_pct;
  const unclassified =
    data.revenue_by_class?.unclassified ?? data.class_coverage?.unclassified ?? 0;
  if (coverage != null && coverage < 90 && unclassified > 0) {
    out.push({
      id: "segment-gap",
      severity: coverage < 70 ? "warn" : "info",
      headline: "Some income isn't assigned to a segment",
      figure: usd(unclassified),
      detail: `${Math.round(coverage)}% of income is classified — the rest can't be split by line of business.`,
      goTo: "revenue",
    });
  }

  // 6. Spend concentrated in a handful of vendors.
  const ev = data.expenses_by_vendor;
  if (ev && ev.top3_concentration_pct >= 50 && ev.vendor_count > 3) {
    out.push({
      id: "vendor-concentration",
      severity: "info",
      headline: `Top 3 vendors carry ${Math.round(ev.top3_concentration_pct)}% of spend`,
      figure: usd(ev.total),
      detail: `Across ${ev.vendor_count} vendors total.`,
      goTo: "costs",
    });
  }

  // 7. Collections falling behind billing.
  const bvc = data.billing_vs_cash;
  if (bvc && bvc.invoiced_total > 0 && bvc.collection_rate_pct < 85) {
    out.push({
      id: "collection-rate",
      severity: bvc.collection_rate_pct < 70 ? "warn" : "info",
      headline: "Collections are trailing what you billed",
      figure: `${Math.round(bvc.collection_rate_pct)}%`,
      detail: `${usd(bvc.open_ar)} of this year's invoicing is still outstanding.`,
      goTo: "today",
    });
  }

  // 8. The data itself is stale or incomplete — say so rather than showing gaps.
  const failed = Object.keys(data.errors ?? {});
  if (data.sync_status === "failed" || failed.length) {
    out.push({
      id: "sync",
      severity: data.sync_status === "failed" ? "warn" : "info",
      headline:
        data.sync_status === "failed"
          ? "The last QuickBooks sync failed"
          : `${failed.length} ${plural(failed.length, "panel")} couldn't load`,
      detail: failed.length ? failed.join(", ") : "Figures below may be out of date.",
    });
  }

  return out.sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]);
}

/* ── self-check ─────────────────────────────────────────────────────────── */

function demo() {
  const assert = (cond: boolean, msg: string) => {
    if (!cond) throw new Error(`FAIL: ${msg}`);
  };
  const base = { errors: {}, sync_status: "ok" } as unknown as QuickBooksOverview;

  assert(deriveSignals(base).length === 0, "clean payload produces no signals");

  const late = deriveSignals({
    ...base,
    ar: {
      total: 100_000,
      invoice_count: 10,
      overdue_total: 30_000,
      buckets: [
        { label: "Not yet due", amount: 70_000 },
        { label: "90+ days", amount: 30_000 },
      ],
      clients: [{ client: "Acme", amount: 30_000, invoices: 2, oldest_days: 120 }],
    },
  } as QuickBooksOverview);
  assert(late[0].id === "ar-late", "aged AR surfaces");
  assert(late[0].severity === "critical", "30% aged is critical, not a warning");
  assert(late[0].detail!.includes("Acme"), "names the oldest debtor");

  const ordered = deriveSignals({
    ...base,
    liquidity: { as_of: "", cash: 10_000, net_cash_change: null },
    ap: { total: 50_000, bill_count: 5, buckets: [], vendors: [] },
    expenses_by_vendor: {
      total: 200_000,
      vendor_count: 20,
      top3_concentration_pct: 60,
      vendors: [],
    },
  } as unknown as QuickBooksOverview);
  assert(ordered[0].severity === "critical", "critical sorts above info");
  assert(ordered[0].id === "ap-over-cash", "payables over cash is the critical one");

  // Thresholds are exclusive at the boundary — 24% untagged is noise, 25% is not.
  const quiet = deriveSignals({
    ...base,
    unattached_cost: {
      purchase_count: 100,
      purchase_total: 0,
      unattached_count: 24,
      unattached_pct: 24,
      cost_of_service_unattached: 5_000,
      accounts: [],
    },
  } as unknown as QuickBooksOverview);
  assert(quiet.length === 0, "below-threshold untagged cost stays quiet");

  console.log("qb-signals: all checks passed");
}

if (process.argv[1]?.endsWith("qb-signals.ts")) demo();
