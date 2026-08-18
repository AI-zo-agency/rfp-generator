"use client";

import { ArrowRight } from "lucide-react";
import { Panel } from "../qb-ui";
import type { SectionId, TeamworkSignal } from "../../lib/teamwork-derive";

const SECTION_LABEL: Record<SectionId, string> = {
  projects: "Projects",
  work: "Tasks",
  time: "Time",
};

export function TeamworkAttention({
  signals,
  onGo,
}: {
  signals: TeamworkSignal[];
  onGo: (id: SectionId) => void;
}) {
  if (!signals.length) {
    return (
      <Panel title="Needs attention">
        <p className="qb-allclear">
          Nothing needs attention. No project is flagged at risk, every overdue task has an
          owner, and no milestone is late.
        </p>
      </Panel>
    );
  }

  return (
    <Panel
      title="Needs attention"
      meta={`${signals.length} ${signals.length === 1 ? "item" : "items"}`}
    >
      <ul className="qb-signals">
        {signals.map((signal) => (
          <li key={signal.id} data-severity={signal.severity}>
            <span className="qb-signal-dot" aria-hidden />
            <div className="qb-signal-body">
              <p className="qb-signal-head">
                {signal.headline}
                <span className="qb-sr">, severity {signal.severity}</span>
              </p>
              {signal.detail ? <p className="qb-signal-detail">{signal.detail}</p> : null}
            </div>
            {signal.figure ? <span className="qb-signal-figure">{signal.figure}</span> : null}
            {signal.goTo ? (
              <button
                type="button"
                className="qb-signal-go"
                onClick={() => onGo(signal.goTo as SectionId)}
              >
                <span>{SECTION_LABEL[signal.goTo]}</span>
                <ArrowRight size={13} strokeWidth={2.25} aria-hidden />
              </button>
            ) : (
              <span />
            )}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
