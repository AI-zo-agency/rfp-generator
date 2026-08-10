"use client";

import { useEffect, useId, useState } from "react";
import type {
  ScanRfpSummary,
  ScanRfpSummaryGroup,
  ScanRfpSummaryTone,
} from "@/lib/proposal-scan-report";
import "./ScanRfpSummaryBanner.css";

type Props = {
  summary: ScanRfpSummary;
  onDismiss?: () => void;
  /** When true, open details immediately (fresh scan). Default: compact strip. */
  defaultExpanded?: boolean;
};

type TextScale = "sm" | "md" | "lg";

const SCALE_KEY = "zo-scan-summary-type";

const TONE_LABEL: Record<ScanRfpSummaryTone, string> = {
  action: "Do next",
  done: "Already fixed",
  fyi: "Good to know",
};

function loadScale(): TextScale {
  if (typeof window === "undefined") return "md";
  try {
    const raw = window.localStorage.getItem(SCALE_KEY);
    if (raw === "sm" || raw === "md" || raw === "lg") return raw;
  } catch {
    /* ignore */
  }
  return "md";
}

function RowList({ group }: { group: ScanRfpSummaryGroup }) {
  return (
    <ul className="proposal-scan-v2__rows">
      {group.rows.map((row) => (
        <li key={row.label} className="proposal-scan-v2__row">
          <p className="proposal-scan-v2__row-title">{row.label}</p>
          {row.detail ? <p className="proposal-scan-v2__row-note">{row.detail}</p> : null}
          {row.items && row.items.length > 0 ? (
            <ul className="proposal-scan-v2__bullets">
              {row.items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

/** Compact-by-default Scan results — keeps the manuscript review area tall. */
export function ScanRfpSummaryBanner({
  summary,
  onDismiss,
  defaultExpanded = false,
}: Props) {
  const tabsId = useId();
  const groups = summary.groups;
  const preferred =
    groups.find((g) => g.tone === "action")?.tone ??
    groups.find((g) => g.tone === "done")?.tone ??
    groups[0]?.tone ??
    null;

  const [expanded, setExpanded] = useState(defaultExpanded);
  const [active, setActive] = useState<ScanRfpSummaryTone | null>(preferred);
  const [scale, setScale] = useState<TextScale>("md");

  useEffect(() => {
    setScale(loadScale());
  }, []);

  useEffect(() => {
    setActive(preferred);
    setExpanded(defaultExpanded);
  }, [preferred, summary.headline, defaultExpanded]);

  const activeGroup = groups.find((g) => g.tone === active) ?? null;
  const actionCount = groups.find((g) => g.tone === "action")?.rows.length ?? 0;
  const doneCount = groups.find((g) => g.tone === "done")?.rows.length ?? 0;
  const fyiCount = groups.find((g) => g.tone === "fyi")?.rows.length ?? 0;

  const title =
    actionCount > 0
      ? actionCount === 1
        ? "1 thing to finish"
        : `${actionCount} things to finish`
      : summary.empty
        ? "Scan finished — nothing urgent"
        : "Scan finished";

  const setScalePersist = (next: TextScale) => {
    setScale(next);
    try {
      window.localStorage.setItem(SCALE_KEY, next);
    } catch {
      /* ignore */
    }
  };

  return (
    <div
      className={`proposal-scan-v2 proposal-scan-v2--${scale}${
        expanded ? " is-expanded" : " is-collapsed"
      }`}
      role="status"
      aria-labelledby={`${tabsId}-title`}
    >
      <div className="proposal-scan-v2__bar">
        <button
          type="button"
          className="proposal-scan-v2__bar-main"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          <span
            className={`proposal-scan-v2__bar-mark${
              actionCount > 0 ? " proposal-scan-v2__bar-mark--action" : ""
            }`}
            aria-hidden
          />
          <span className="proposal-scan-v2__bar-copy">
            <span id={`${tabsId}-title`} className="proposal-scan-v2__bar-title">
              {title}
            </span>
            <span className="proposal-scan-v2__bar-meta">
              {actionCount > 0 ? `${actionCount} to do` : "Clear"}
              {doneCount > 0 ? ` · ${doneCount} fixed` : ""}
              {fyiCount > 0 ? ` · ${fyiCount} notes` : ""}
            </span>
          </span>
          <span className="proposal-scan-v2__bar-toggle">
            {expanded ? "Hide" : "Details"}
          </span>
        </button>

        <div className="proposal-scan-v2__tools">
          {expanded ? (
            <div className="proposal-scan-v2__type" role="group" aria-label="Text size">
              {(
                [
                  ["sm", "A"],
                  ["md", "A"],
                  ["lg", "A"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className={`proposal-scan-v2__type-btn proposal-scan-v2__type-btn--${key}${
                    scale === key ? " is-active" : ""
                  }`}
                  aria-pressed={scale === key}
                  aria-label={
                    key === "sm"
                      ? "Small text"
                      : key === "md"
                        ? "Medium text"
                        : "Large text"
                  }
                  onClick={() => setScalePersist(key)}
                >
                  {label}
                </button>
              ))}
            </div>
          ) : null}
          {onDismiss ? (
            <button type="button" className="proposal-scan-v2__close" onClick={onDismiss}>
              Close
            </button>
          ) : null}
        </div>
      </div>

      {expanded && groups.length > 0 ? (
        <div className="proposal-scan-v2__body">
          <div className="proposal-scan-v2__tabs" role="tablist" aria-label="Scan result topics">
            {groups.map((group) => {
              const selected = group.tone === active;
              return (
                <button
                  key={group.tone}
                  type="button"
                  role="tab"
                  id={`${tabsId}-${group.tone}`}
                  aria-selected={selected}
                  aria-controls={`${tabsId}-panel`}
                  className={`proposal-scan-v2__tab proposal-scan-v2__tab--${group.tone}${
                    selected ? " is-active" : ""
                  }`}
                  onClick={() => setActive(group.tone)}
                >
                  <span className="proposal-scan-v2__tab-label">
                    {TONE_LABEL[group.tone]}
                  </span>
                  <span className="proposal-scan-v2__tab-count">{group.rows.length}</span>
                </button>
              );
            })}
          </div>

          {activeGroup ? (
            <div
              className={`proposal-scan-v2__panel proposal-scan-v2__panel--${activeGroup.tone}`}
              role="tabpanel"
              id={`${tabsId}-panel`}
              aria-labelledby={`${tabsId}-${activeGroup.tone}`}
            >
              <p className="proposal-scan-v2__panel-hint">{activeGroup.hint}</p>
              <RowList group={activeGroup} />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
