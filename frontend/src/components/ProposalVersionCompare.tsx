"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { OutlineSection, ProposalDraftSnapshot } from "@/types/proposal";
import { fetchProposalSnapshot } from "@/lib/proposal-api";
import {
  diffProposalSections,
  formatScanSummaryLines,
  type FulfillScanSummary,
} from "@/lib/proposal-snapshot-diff";
import { ProposalSnapshotCompareModal } from "@/components/ProposalSnapshotCompareModal";

type Props = {
  rfpId: string;
  selectedSnapshot: ProposalDraftSnapshot | null;
  currentSections: OutlineSection[];
  onJumpToSection?: (sectionId: string) => void;
};

export function ProposalVersionCompare({
  rfpId,
  selectedSnapshot,
  currentSections,
  onJumpToSection,
}: Props) {
  const [loadedSnapshot, setLoadedSnapshot] =
    useState<ProposalDraftSnapshot | null>(null);
  const [loadingSnapshot, setLoadingSnapshot] = useState(false);
  const [compareModal, setCompareModal] = useState<{
    sectionTitle: string;
    before: string;
    after: string;
  } | null>(null);

  const snapshotLabel = selectedSnapshot?.label ?? "Saved version";

  const openSectionCompare = useCallback(
    (sectionId: string, sectionTitle: string) => {
      const base = loadedSnapshot ?? selectedSnapshot;
      if (!base) return;
      const beforeSec = (base.sections ?? []).find((s) => s.id === sectionId);
      const afterSec = currentSections.find((s) => s.id === sectionId);
      setCompareModal({
        sectionTitle,
        before: beforeSec?.content ?? "",
        after: afterSec?.content ?? "",
      });
    },
    [loadedSnapshot, selectedSnapshot, currentSections]
  );

  useEffect(() => {
    if (!selectedSnapshot) {
      setLoadedSnapshot(null);
      return;
    }
    const hasBodies = (selectedSnapshot.sections?.length ?? 0) > 0;
    if (hasBodies) {
      setLoadedSnapshot(selectedSnapshot);
      return;
    }
    let cancelled = false;
    setLoadingSnapshot(true);
    void fetchProposalSnapshot(rfpId, selectedSnapshot.savedAt).then((full) => {
      if (cancelled) return;
      setLoadedSnapshot(full ?? selectedSnapshot);
      setLoadingSnapshot(false);
    });
    return () => {
      cancelled = true;
    };
  }, [rfpId, selectedSnapshot]);

  const diff = useMemo(() => {
    const base = loadedSnapshot ?? selectedSnapshot;
    if (!base) return null;
    return diffProposalSections(base.sections ?? [], currentSections);
  }, [loadedSnapshot, selectedSnapshot, currentSections]);

  const scanLines = useMemo(
    () =>
      formatScanSummaryLines(
        (loadedSnapshot ?? selectedSnapshot)?.scanSummary as
          | FulfillScanSummary
          | undefined
      ),
    [loadedSnapshot, selectedSnapshot?.scanSummary, selectedSnapshot]
  );

  if (!selectedSnapshot || !diff) {
    return null;
  }

  if (loadingSnapshot) {
    return (
      <p className="text-[11px] text-zo-text-muted">Loading saved version…</p>
    );
  }

  const hasChanges =
    diff.added.length > 0 ||
    diff.removed.length > 0 ||
    diff.modified.length > 0 ||
    scanLines.length > 0;

  if (!hasChanges) {
    return (
      <p className="text-[11px] leading-relaxed text-zo-text-muted">
        No differences between this saved version and the current proposal.
      </p>
    );
  }

  return (
    <>
      <div className="space-y-3 rounded-lg border border-zo-border/80 bg-white p-3 text-[11px] leading-relaxed text-foreground">
        <p className="font-semibold text-xs">
          Compare: {selectedSnapshot.label} → now
        </p>
        <p className="text-zo-text-muted">
          Click a section to open a side-by-side diff with highlights.
        </p>

        {scanLines.length > 0 ? (
          <div>
            <p className="mb-1 font-medium text-zo-text-muted">Last scan from this save</p>
            <ul className="list-inside list-disc space-y-0.5 text-zo-text-muted">
              {scanLines.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {diff.added.length > 0 ? (
          <SectionGroup
            title={`Added (${diff.added.length})`}
            tone="emerald"
            items={diff.added.map((s) => ({ id: s.id, label: s.title }))}
            onCompare={openSectionCompare}
            onJump={onJumpToSection}
          />
        ) : null}

        {diff.removed.length > 0 ? (
          <SectionGroup
            title={`Removed (${diff.removed.length})`}
            tone="rose"
            items={diff.removed.map((s) => ({ id: s.id, label: s.title }))}
            onCompare={openSectionCompare}
          />
        ) : null}

        {diff.modified.length > 0 ? (
          <div>
            <p className="mb-1 font-medium text-amber-800">
              Changed ({diff.modified.length})
            </p>
            <ul className="max-h-48 space-y-2 overflow-y-auto">
              {diff.modified.map((m) => (
                <li
                  key={m.id}
                  className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-zo-border/60 bg-zo-surface/40 px-2 py-1.5"
                >
                  <button
                    type="button"
                    className="text-left font-medium text-[#0b2f6b] underline-offset-2 hover:underline"
                    onClick={() => openSectionCompare(m.id, m.title)}
                  >
                    {m.title}
                  </button>
                  <span className="text-zo-text-muted">
                    {m.charsBefore.toLocaleString()} → {m.charsAfter.toLocaleString()} chars
                  </span>
                  {onJumpToSection ? (
                    <button
                      type="button"
                      className="ml-auto text-[10px] font-semibold uppercase tracking-wide text-zo-text-muted hover:text-foreground"
                      onClick={() => onJumpToSection(m.id)}
                    >
                      Open in draft
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {diff.unchangedCount > 0 ? (
          <p className="text-zo-text-muted">
            {diff.unchangedCount} section(s) unchanged.
          </p>
        ) : null}
      </div>

      <ProposalSnapshotCompareModal
        open={compareModal !== null}
        onClose={() => setCompareModal(null)}
        sectionTitle={compareModal?.sectionTitle ?? ""}
        snapshotLabel={snapshotLabel}
        before={compareModal?.before ?? ""}
        after={compareModal?.after ?? ""}
      />
    </>
  );
}

function SectionGroup({
  title,
  tone,
  items,
  onCompare,
  onJump,
}: {
  title: string;
  tone: "emerald" | "rose";
  items: { id: string; label: string }[];
  onCompare: (id: string, title: string) => void;
  onJump?: (id: string) => void;
}) {
  const titleClass =
    tone === "emerald" ? "text-emerald-800" : "text-rose-800";
  return (
    <div>
      <p className={`mb-1 font-medium ${titleClass}`}>{title}</p>
      <ul className="space-y-1">
        {items.map((item) => (
          <li
            key={item.id}
            className="flex flex-wrap items-center gap-x-2 rounded-md border border-zo-border/60 bg-zo-surface/40 px-2 py-1"
          >
            <button
              type="button"
              className="text-left font-medium text-[#0b2f6b] underline-offset-2 hover:underline"
              onClick={() => onCompare(item.id, item.label)}
            >
              {item.label}
            </button>
            {onJump ? (
              <button
                type="button"
                className="ml-auto text-[10px] font-semibold uppercase tracking-wide text-zo-text-muted hover:text-foreground"
                onClick={() => onJump(item.id)}
              >
                Open in draft
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
