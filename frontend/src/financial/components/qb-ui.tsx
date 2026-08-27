"use client";

/**
 * Shared furniture for the QuickBooks ledger.
 *
 * The rule this file exists to enforce: a number is only decorated when the
 * decoration says something the number can't. Everything else is alignment,
 * weight, and whitespace.
 */

import { Fragment, useState, type ReactNode } from "react";
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type RowData,
  type SortingState,
} from "@tanstack/react-table";
import {
  CalendarClock,
  ChevronDown,
  ChevronUp,
  ChevronsUpDown,
  CircleDollarSign,
  CircleHelp,
  Clock,
  Flag,
  FolderKanban,
  HandCoins,
  ListTodo,
  Percent,
  Receipt,
  Scale,
  ShieldAlert,
  TrendingUp,
  Users,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/* ── money ─────────────────────────────────────────────────────────────── */

export const usd = (n: number, digits = 0) =>
  n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });

/** Compact for axes and dense cells, where the exact dollar isn't the point. */
export const compact = (n: number) => {
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(abs >= 10_000 ? 0 : 1)}k`;
  return usd(n);
};

/* ── page structure ────────────────────────────────────────────────────── */

/**
 * A titled region. Deliberately not a card: a hairline and a heading separate
 * these, so nesting one inside another still reads as one plane.
 */
export function Panel({
  title,
  meta,
  action,
  hint,
  children,
  className,
}: {
  title?: string;
  meta?: ReactNode;
  action?: ReactNode;
  /** Hover tip next to the title — use for panels whose purpose isn’t obvious. */
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("qb-panel", className)}>
      {title || meta || action ? (
        <div className="qb-panel-head">
          {title ? (
            <h3>
              {title}
              {hint ? (
                <TooltipProvider delayDuration={200}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button type="button" className="qb-panel-hint" aria-label={`About ${title}`}>
                        <CircleHelp size={13} aria-hidden />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom" className="max-w-[280px]">
                      {hint}
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              ) : null}
            </h3>
          ) : null}
          {meta ? <span className="qb-panel-meta">{meta}</span> : null}
          {action ? <div className="qb-panel-action">{action}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="qb-empty">{children}</p>;
}

export function Note({ children }: { children: ReactNode }) {
  return <p className="qb-note">{children}</p>;
}

type FigureMetric =
  | "cash"
  | "ar"
  | "ap"
  | "net"
  | "booked"
  | "margin"
  | "income"
  | "projects"
  | "overdue"
  | "soon"
  | "hours"
  | "risk"
  | "people"
  | "flag";

const METRIC_ICON: Record<FigureMetric, LucideIcon> = {
  cash: Wallet,
  ar: HandCoins,
  ap: Receipt,
  net: Scale,
  booked: TrendingUp,
  margin: Percent,
  income: CircleDollarSign,
  projects: FolderKanban,
  overdue: ListTodo,
  soon: CalendarClock,
  hours: Clock,
  risk: ShieldAlert,
  people: Users,
  flag: Flag,
};

/**
 * A single figure. `metric` colors Position totals by what the number is
 * and adds a Lucide mark so the row scans like a Stripe/Mercury KPI strip.
 * `tone` still overrides when the number is moving the wrong way.
 */
export function Figure({
  label,
  value,
  sub,
  tone,
  metric,
  size = "md",
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "in" | "out" | "warn";
  metric?: FigureMetric;
  size?: "sm" | "md" | "lg";
}) {
  const Icon = metric ? METRIC_ICON[metric] : null;
  return (
    <div className="qb-figure" data-size={size} data-metric={metric}>
      <span className="qb-figure-head">
        <span className="qb-figure-label">{label}</span>
        {Icon ? (
          <span className="qb-figure-icon" aria-hidden>
            <Icon size={14} strokeWidth={2.25} />
          </span>
        ) : null}
      </span>
      <span className="qb-figure-value" data-tone={tone}>
        {value}
      </span>
      {sub ? <span className="qb-figure-sub">{sub}</span> : null}
    </div>
  );
}

/* ── status marks ──────────────────────────────────────────────────────── */

/**
 * A status word that reads as a status. Replaces plain-text cells like
 * "At risk", which scan identically to every other word in the table.
 */
export function Pill({
  label,
  tone = "neutral",
}: {
  label: string;
  tone?: "good" | "warn" | "bad" | "muted" | "neutral";
}) {
  return (
    <span className="qb-pill" data-tone={tone === "neutral" ? undefined : tone}>
      {label}
    </span>
  );
}

/** A proportion inside a table cell, so a column of them scans as a shape. */
export function MiniBar({
  value,
  max,
  label,
  tone,
}: {
  value: number;
  max: number;
  label: string;
  tone?: "warn" | "bad";
}) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  return (
    <span className="qb-minibar" data-tone={tone}>
      <span className="qb-bar-track" aria-hidden>
        <span style={{ width: `${pct}%` }} />
      </span>
      <span className="qb-minibar-value">{label}</span>
    </span>
  );
}

/** A due date as urgency rather than as a calendar date. */
export function DueChip({
  label,
  tone,
}: {
  label: string;
  tone: "late" | "soon" | "later" | "none";
}) {
  return (
    <span className="qb-due" data-tone={tone}>
      {label}
    </span>
  );
}

/** Segmented filter row. Replaces "Show N more" as a table's primary navigation. */
export function FilterChips<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { id: T; label: string; count?: number }[];
  value: T;
  onChange: (id: T) => void;
  label: string;
}) {
  return (
    <div className="qb-chips" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          data-state={option.id === value ? "on" : undefined}
          aria-pressed={option.id === value}
          onClick={() => onChange(option.id)}
        >
          {option.label}
          {option.count === undefined ? null : (
            <span className="qb-chips-count">{option.count}</span>
          )}
        </button>
      ))}
    </div>
  );
}

/* ── aging ─────────────────────────────────────────────────────────────── */

/**
 * Age buckets are ordered, so they get a sequential ramp rather than five
 * unrelated hues. The alarm colour is spent only on the bucket that is an alarm.
 */
const AGE_RAMP: Record<string, string> = {
  // Not-due is the calm end of the ramp, so it stays light — weight belongs on
  // the buckets that are actually ageing.
  "Not yet due": "color-mix(in srgb, var(--zo-teal) 30%, var(--zo-card-bg))",
  "1-30 days": "color-mix(in srgb, var(--zo-orange) 34%, var(--zo-card-bg))",
  "31-60 days": "color-mix(in srgb, var(--zo-orange) 66%, var(--zo-card-bg))",
  "61-90 days": "var(--zo-orange)",
  "90+ days": "var(--zo-danger)",
};

export function AgingBar({ buckets }: { buckets: { label: string; amount: number }[] }) {
  const shown = buckets.filter((b) => b.amount > 0);
  const total = shown.reduce((s, b) => s + b.amount, 0);
  if (!shown.length || !total) return <Empty>Nothing outstanding.</Empty>;

  return (
    <div className="qb-aging">
      <div className="qb-aging-track">
        {shown.map((b) => (
          <Tooltip key={b.label}>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="qb-aging-seg"
                aria-label={`${b.label}: ${usd(b.amount)}`}
                style={{
                  flex: `${Math.max(b.amount / total, 0.015)} 1 0`,
                  background: AGE_RAMP[b.label] ?? "var(--zo-surface)",
                }}
              />
            </TooltipTrigger>
            <TooltipContent>
              {b.label} · {usd(b.amount)} · {Math.round((b.amount / total) * 100)}%
            </TooltipContent>
          </Tooltip>
        ))}
      </div>
      <ul className="qb-aging-key">
        {shown.map((b) => (
          <li key={b.label}>
            <span style={{ background: AGE_RAMP[b.label] ?? "var(--zo-surface)" }} aria-hidden />
            <span className="qb-aging-key-label">{b.label}</span>
            <span className="qb-aging-key-amt">{compact(b.amount)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ── table ─────────────────────────────────────────────────────────────── */

/** Clicks and keystrokes that land on a link or control belong to that control. */
function isInteractiveTarget(target: EventTarget | null): boolean {
  return (
    target instanceof Element &&
    target.closest("a, button, input, select, textarea, [role='button']") !== null
  );
}

/**
 * One sortable table, used everywhere a ranked list used to be. Sorting is the
 * affordance that replaced nine near-identical bar charts: the reader picks the
 * comparison instead of being handed share-of-largest every time.
 */
export function DataTable<T>({
  data,
  columns,
  initialSort,
  pageSize,
  empty = "Nothing here yet.",
  renderSubRow,
  rowId,
}: {
  data: T[];
  columns: ColumnDef<T, unknown>[];
  /** Column id to sort by on first paint. Descending. */
  initialSort?: string;
  /** Rows shown before "Show all". Omit to show everything. */
  pageSize?: number;
  empty?: string;
  /** When provided, rows expand on click to show this content. One at a time. */
  renderSubRow?: (row: T) => ReactNode;
  /** Stable per-row identity. Pass this whenever renderSubRow is used and the
   *  data array can be filtered or reordered, or the open row is keyed by
   *  array position and will latch onto the wrong entity. */
  rowId?: (row: T) => string;
}) {
  const [sorting, setSorting] = useState<SortingState>(
    initialSort ? [{ id: initialSort, desc: true }] : [],
  );
  const [expanded, setExpanded] = useState(false);
  const [openRow, setOpenRow] = useState<string | null>(null);

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    ...(rowId ? { getRowId: (row: T) => rowId(row) } : {}),
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  if (!data.length) return <Empty>{empty}</Empty>;

  const rows = table.getRowModel().rows;
  const visible = pageSize && !expanded ? rows.slice(0, pageSize) : rows;
  const hidden = rows.length - visible.length;

  return (
    <div className="qb-tablewrap">
      <Table className="qb-table">
        <TableHeader>
          {table.getHeaderGroups().map((group) => (
            <TableRow key={group.id}>
              {group.headers.map((header) => {
                const sortable = header.column.getCanSort();
                const dir = header.column.getIsSorted();
                const numeric = header.column.columnDef.meta?.numeric;
                return (
                  <TableHead key={header.id} data-numeric={numeric ? "true" : undefined}>
                    {sortable ? (
                      <button
                        type="button"
                        className="qb-sort"
                        onClick={header.column.getToggleSortingHandler()}
                        aria-label={`Sort by ${String(header.column.columnDef.header)}`}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {dir === "asc" ? (
                          <ChevronUp size={12} strokeWidth={2.5} aria-hidden />
                        ) : dir === "desc" ? (
                          <ChevronDown size={12} strokeWidth={2.5} aria-hidden />
                        ) : (
                          <ChevronsUpDown size={12} strokeWidth={2} aria-hidden className="qb-sort-idle" />
                        )}
                      </button>
                    ) : (
                      flexRender(header.column.columnDef.header, header.getContext())
                    )}
                  </TableHead>
                );
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {visible.map((row) => {
            const isOpen = openRow === row.id;
            const toggle = () => setOpenRow((current) => (current === row.id ? null : row.id));
            return (
              <Fragment key={row.id}>
                <TableRow
                  data-expandable={renderSubRow ? "true" : undefined}
                  data-open={isOpen ? "true" : undefined}
                  aria-expanded={renderSubRow ? isOpen : undefined}
                  tabIndex={renderSubRow ? 0 : undefined}
                  onClick={
                    renderSubRow
                      ? (event) => {
                          if (isInteractiveTarget(event.target)) return;
                          toggle();
                        }
                      : undefined
                  }
                  onKeyDown={
                    renderSubRow
                      ? (event) => {
                          if (event.key !== "Enter" && event.key !== " ") return;
                          if (isInteractiveTarget(event.target)) return;
                          event.preventDefault();
                          toggle();
                        }
                      : undefined
                  }
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell
                      key={cell.id}
                      data-numeric={cell.column.columnDef.meta?.numeric ? "true" : undefined}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
                {renderSubRow && isOpen ? (
                  <TableRow className="qb-drillrow">
                    <TableCell colSpan={row.getVisibleCells().length}>
                      {renderSubRow(row.original)}
                    </TableCell>
                  </TableRow>
                ) : null}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
      {hidden > 0 || expanded ? (
        <button type="button" className="qb-more" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Show fewer" : `Show ${hidden} more`}
        </button>
      ) : null}
    </div>
  );
}

declare module "@tanstack/react-table" {
  interface ColumnMeta<TData extends RowData, TValue> {
    /** Right-align the column and render it with tabular figures. */
    numeric?: boolean;
  }
}
