"use client";

import { useEffect, useState } from "react";
import { RfpLedgerDetail } from "@/components/RfpLedgerDetail";
import { formatRelativeTime } from "@/lib/format";
import {
  fetchLedger,
  fetchPeople,
  type DeletedFilter,
  type LedgerPerson,
  type LedgerRow,
} from "@/lib/rfp-ledger-api";
import { formatCost, formatWordDelta } from "@/lib/rfp-ledger";

interface Filters {
  person: string;
  from: string;
  to: string;
}

const EMPTY_FILTERS: Filters = { person: "", from: "", to: "" };
const TH = "py-2 pr-3 text-left text-[11px] font-bold uppercase tracking-wide text-zo-text-muted";
const TD = "py-3 pr-3 align-top text-sm";
const INPUT =
  "rounded-lg border border-zo-border bg-white px-3 py-2 text-sm outline-none focus:border-[#ef5018]";

function dayStart(day: string): string | undefined {
  return day ? `${day}T00:00:00+00:00` : undefined;
}

function dayEnd(day: string): string | undefined {
  return day ? `${day}T23:59:59.999999+00:00` : undefined;
}

export function RfpLedgerPanel({ className = "" }: { className?: string }) {
  const [tab, setTab] = useState<"rfps" | "people">("rfps");
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState<Filters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<Filters>(EMPTY_FILTERS);
  const [deletedFilter, setDeletedFilter] = useState<DeletedFilter>("all");
  const [rows, setRows] = useState<LedgerRow[]>([]);
  const [people, setPeople] = useState<LedgerPerson[]>([]);
  const [truncated, setTruncated] = useState(false);
  // Loading is derived: a result only counts for the request it was fetched for.
  const requestKey = `${tab}|${applied.person}|${applied.from}|${applied.to}`;
  const [settled, setSettled] = useState<{ key: string; error: string | null }>({
    key: "",
    error: null,
  });
  const loading = settled.key !== requestKey;
  const error = loading ? null : settled.error;

  useEffect(() => {
    if (selected) return;
    let cancelled = false;
    const range = { from: dayStart(applied.from), to: dayEnd(applied.to) };
    const request =
      tab === "rfps"
        ? fetchLedger({ person: applied.person, ...range }).then((data) => {
            if (cancelled) return;
            setRows(data.items);
            setTruncated(data.truncated);
          })
        : fetchPeople(range).then((data) => {
            if (!cancelled) setPeople(data.items);
          });
    request.then(
      () => {
        if (!cancelled) setSettled({ key: requestKey, error: null });
      },
      (err) => {
        if (!cancelled) {
          setSettled({
            key: requestKey,
            error: err instanceof Error ? err.message : "Failed to load the ledger",
          });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [tab, applied, selected, requestKey]);

  const visible = rows.filter((row) =>
    deletedFilter === "hide" ? !row.deleted : deletedFilter === "only" ? row.deleted : true,
  );

  return (
    <section
      className={`flex min-h-0 flex-col rounded-2xl border border-black/5 bg-white/80 p-5 sm:p-6 ${className}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-heading text-lg font-semibold text-foreground">
            RFP Ledger — who ran, opened, tweaked and deleted what
          </h2>
          <p className="mt-1 text-xs text-zo-text-muted">
            One row per RFP, including deleted ones. Open a row to see every run and every change.
          </p>
        </div>
        {selected ? null : (
          <div className="flex gap-1 rounded-lg border border-black/10 bg-white p-0.5">
            {(["rfps", "people"] as const).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setTab(value)}
                className={`rounded-md px-3 py-1.5 text-xs font-bold transition ${
                  tab === value ? "bg-[#ef5018] text-white" : "text-zo-text-secondary hover:bg-black/[0.03]"
                }`}
              >
                {value === "rfps" ? "RFPs" : "People"}
              </button>
            ))}
          </div>
        )}
      </div>

      {selected ? (
        <div className="mt-4">
          <RfpLedgerDetail key={selected} rfpId={selected} onBack={() => setSelected(null)} />
        </div>
      ) : (
        <>
          <form
            className="mt-4 flex flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              setApplied(draft);
            }}
          >
            {tab === "rfps" ? (
              <input
                type="search"
                value={draft.person}
                onChange={(e) => setDraft({ ...draft, person: e.target.value })}
                placeholder="Person's email"
                className={`min-w-[200px] flex-1 ${INPUT}`}
              />
            ) : null}
            <label className="text-[11px] font-semibold text-zo-text-muted">
              From
              <input
                type="date"
                value={draft.from}
                onChange={(e) => setDraft({ ...draft, from: e.target.value })}
                className={`mt-1 block ${INPUT}`}
              />
            </label>
            <label className="text-[11px] font-semibold text-zo-text-muted">
              To
              <input
                type="date"
                value={draft.to}
                onChange={(e) => setDraft({ ...draft, to: e.target.value })}
                className={`mt-1 block ${INPUT}`}
              />
            </label>
            {tab === "rfps" ? (
              <label className="text-[11px] font-semibold text-zo-text-muted">
                Deleted RFPs
                <select
                  value={deletedFilter}
                  onChange={(e) => setDeletedFilter(e.target.value as DeletedFilter)}
                  className={`mt-1 block ${INPUT}`}
                >
                  <option value="all">Show</option>
                  <option value="hide">Hide</option>
                  <option value="only">Only deleted</option>
                </select>
              </label>
            ) : null}
            <button
              type="submit"
              className="rounded-lg bg-[#ef5018] px-3 py-2 text-xs font-bold text-white transition hover:opacity-90"
            >
              Apply
            </button>
            <button
              type="button"
              onClick={() => {
                setDraft(EMPTY_FILTERS);
                setApplied(EMPTY_FILTERS);
                setDeletedFilter("all");
              }}
              className="rounded-lg border border-black/10 px-3 py-2 text-xs font-semibold text-zo-text-secondary hover:bg-black/[0.03]"
            >
              Clear
            </button>
          </form>

          {loading ? (
            <p className="mt-8 text-sm text-zo-text-muted animate-pulse">Loading ledger…</p>
          ) : error ? (
            <p className="mt-8 text-sm text-red-600">{error}</p>
          ) : tab === "rfps" ? (
            visible.length === 0 ? (
              <p className="mt-8 text-sm text-zo-text-muted">
                Nothing recorded yet. Runs, opens, tweaks and deletes show up here as people work.
              </p>
            ) : (
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[760px] border-collapse">
                  <thead>
                    <tr className="border-b border-black/10">
                      <th className={TH}>RFP</th>
                      <th className={TH}>Ran by</th>
                      <th className={TH}>Last opened</th>
                      <th className={TH}>Runs</th>
                      <th className={TH}>Tweaks</th>
                      <th className={TH}>Words</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visible.map((row) => (
                      <tr
                        key={row.rfp_id}
                        onClick={() => setSelected(row.rfp_id)}
                        className="cursor-pointer border-t border-black/5 hover:bg-black/[0.02]"
                      >
                        <td className={`${TD} max-w-[260px]`}>
                          <p className="font-semibold text-foreground">{row.title}</p>
                          {row.deleted ? (
                            <p className="mt-0.5 text-[11px] font-semibold text-red-700">
                              Deleted by {row.deleted_by} · {formatRelativeTime(row.deleted_at ?? "")}
                            </p>
                          ) : null}
                        </td>
                        <td className={TD}>
                          {row.ran_by.length ? row.ran_by.join(", ") : <span className="text-zo-text-muted">—</span>}
                        </td>
                        <td className={TD}>
                          {row.last_opened_by ? (
                            <>
                              <p>{row.last_opened_by}</p>
                              <p className="text-[11px] text-zo-text-muted">
                                {formatRelativeTime(row.last_opened_at ?? "")} · {row.opened_by_count}{" "}
                                {row.opened_by_count === 1 ? "person" : "people"}
                              </p>
                            </>
                          ) : (
                            <span className="text-zo-text-muted">—</span>
                          )}
                        </td>
                        <td className={`${TD} tabular-nums`}>
                          {row.run_count}
                          <span className="block text-[11px] text-zo-text-muted">{formatCost(row.cost_usd)}</span>
                        </td>
                        <td className={`${TD} tabular-nums`}>
                          {row.tweak_count}
                          <span className="block text-[11px] text-zo-text-muted">
                            {row.manual_count} manual · {row.ai_count} AI · {row.other_count} other
                            {row.restore_count ? ` · ${row.restore_count} restore/reset` : ""}
                          </span>
                        </td>
                        <td className={`${TD} tabular-nums`}>{formatWordDelta(row.words_added, row.words_removed)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {truncated ? (
                  <p className="mt-3 text-[11px] text-zo-text-muted">
                    Showing the most recent activity only. Narrow the dates to see older RFPs.
                  </p>
                ) : null}
              </div>
            )
          ) : people.length === 0 ? (
            <p className="mt-8 text-sm text-zo-text-muted">No people recorded yet.</p>
          ) : (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[640px] border-collapse">
                <thead>
                  <tr className="border-b border-black/10">
                    <th className={TH}>Person</th>
                    <th className={TH}>RFPs opened</th>
                    <th className={TH}>Ran</th>
                    <th className={TH}>Tweaked</th>
                    <th className={TH}>Deleted</th>
                    <th className={TH}>Spend</th>
                  </tr>
                </thead>
                <tbody>
                  {people.map((p) => (
                    <tr
                      key={p.actor_email}
                      onClick={() => {
                        const next = { ...EMPTY_FILTERS, ...applied, person: p.actor_email };
                        setDraft(next);
                        setApplied(next);
                        setTab("rfps");
                      }}
                      className="cursor-pointer border-t border-black/5 hover:bg-black/[0.02]"
                    >
                      <td className={`${TD} font-semibold text-foreground`}>{p.actor_email}</td>
                      <td className={`${TD} tabular-nums`}>{p.rfps_opened}</td>
                      <td className={`${TD} tabular-nums`}>
                        {p.runs} {p.runs === 1 ? "run" : "runs"} · {p.rfps_ran} RFPs
                      </td>
                      <td className={`${TD} tabular-nums`}>
                        {p.tweaks} {p.tweaks === 1 ? "tweak" : "tweaks"} · {p.rfps_tweaked} RFPs
                      </td>
                      <td className={`${TD} tabular-nums`}>{p.rfps_deleted}</td>
                      <td className={`${TD} tabular-nums`}>{formatCost(p.cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}
