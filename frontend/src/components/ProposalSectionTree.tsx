"use client";

import { useEffect, useMemo, useState } from "react";
import type { ManualFillFlag } from "@/lib/proposal-manual-flags";
import {
  buildOutlineSectionTree,
  groupContainsSection,
  type OutlineTreeGroup,
} from "@/lib/proposal-outline-tree";
import type { OutlineSection } from "@/types/proposal";
import type { SectionRevisionRecord } from "./DraftSectionEditor";
import { SectionStatusPill } from "./SectionStatusPill";

function sectionManualFillCount(
  sectionId: string,
  flags: ManualFillFlag[],
): number {
  return flags.filter((flag) => flag.sectionId === sectionId).length;
}

interface ProposalSectionTreeProps {
  sections: OutlineSection[];
  manuscriptIndexById: Map<string, number>;
  selectedSectionId: string | null;
  highlightedSectionId: string | null;
  manualFillFlags: ManualFillFlag[];
  sectionRevisions: Record<string, SectionRevisionRecord>;
  sectionButtonRefs: React.MutableRefObject<Map<string, HTMLButtonElement>>;
  onSelectSection: (sectionId: string) => void;
  onOpenRevision: (sectionId: string) => void;
}

function SectionRow({
  section,
  depth,
  active,
  highlighted,
  flagCount,
  hasRevision,
  sectionButtonRefs,
  onSelectSection,
  onOpenRevision,
  indexLabel,
}: {
  section: OutlineSection;
  depth: number;
  active: boolean;
  highlighted: boolean;
  flagCount: number;
  hasRevision: boolean;
  sectionButtonRefs: React.MutableRefObject<Map<string, HTMLButtonElement>>;
  onSelectSection: (sectionId: string) => void;
  onOpenRevision: (sectionId: string) => void;
  indexLabel: string;
}) {
  const hasContent = Boolean(section.content.trim());

  return (
    <li>
      <button
        type="button"
        ref={(node) => {
          if (node) sectionButtonRefs.current.set(section.id, node);
          else sectionButtonRefs.current.delete(section.id);
        }}
        onClick={() => onSelectSection(section.id)}
        className={`proposal-section-list-item ${
          depth > 0 ? "proposal-section-list-item--child" : ""
        } ${active ? "is-active" : ""} ${highlighted ? "is-flag-target" : ""}`}
        style={depth > 0 ? { paddingLeft: `${12 + depth * 14}px` } : undefined}
      >
        <span
          className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[9px] font-bold ${
            hasContent
              ? "bg-[#ef5018] text-white"
              : "border border-zo-border bg-[var(--zo-input-bg)] text-zo-text-muted"
          }`}
          aria-hidden
        >
          {indexLabel}
        </span>
        <div className="min-w-0 flex-1">
          <p
            className={`line-clamp-2 text-[13px] font-semibold leading-snug ${
              active ? "text-zo-orange" : "text-foreground"
            }`}
          >
            {section.title}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <SectionStatusPill status={section.status} />
            {flagCount > 0 ? (
              <span
                className="rounded bg-amber-100 px-1.5 py-0.5 text-[9px] font-bold uppercase text-amber-900"
                title={`${flagCount} manual fill-in tag(s)`}
              >
                {flagCount} fill-in{flagCount === 1 ? "" : "s"}
              </span>
            ) : null}
            {hasRevision ? (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onOpenRevision(section.id);
                }}
                className="rounded bg-teal-100 px-1.5 py-0.5 text-[9px] font-bold uppercase text-teal-900 hover:bg-teal-200"
                title="View what changed in this section"
              >
                Updated
              </button>
            ) : null}
            {section.custom ? (
              <span className="text-[9px] font-bold uppercase text-zo-orange">
                Custom
              </span>
            ) : null}
          </div>
        </div>
      </button>
    </li>
  );
}

function SectionGroup({
  group,
  selectedSectionId,
  highlightedSectionId,
  manualFillFlags,
  sectionRevisions,
  sectionButtonRefs,
  collapsed,
  onToggle,
  onSelectSection,
  onOpenRevision,
  manuscriptIndexById,
}: {
  group: OutlineTreeGroup;
  selectedSectionId: string | null;
  highlightedSectionId: string | null;
  manualFillFlags: ManualFillFlag[];
  sectionRevisions: Record<string, SectionRevisionRecord>;
  sectionButtonRefs: React.MutableRefObject<Map<string, HTMLButtonElement>>;
  collapsed: boolean;
  onToggle: () => void;
  onSelectSection: (sectionId: string) => void;
  onOpenRevision: (sectionId: string) => void;
  manuscriptIndexById: Map<string, number>;
}) {
  const generatedCount = group.sections.filter((section) =>
    section.content.trim(),
  ).length;

  return (
    <li className="proposal-section-tree-group">
      <button
        type="button"
        onClick={onToggle}
        className="proposal-section-tree-group-header"
        aria-expanded={!collapsed}
      >
        <span
          className={`proposal-section-tree-chevron ${collapsed ? "is-collapsed" : ""}`}
          aria-hidden
        >
          ▾
        </span>
        <span className="min-w-0 flex-1 text-left text-[12px] font-bold leading-snug text-foreground">
          {group.label}
        </span>
        <span className="shrink-0 text-[10px] font-semibold text-zo-text-muted">
          {generatedCount}/{group.sections.length}
        </span>
      </button>
      {!collapsed ? (
        <ul className="proposal-section-tree-children">
          {group.sections.map((section) => (
            <SectionRow
              key={section.id}
              section={section}
              depth={1}
              active={selectedSectionId === section.id}
              highlighted={highlightedSectionId === section.id}
              flagCount={sectionManualFillCount(section.id, manualFillFlags)}
              hasRevision={Boolean(sectionRevisions[section.id])}
              sectionButtonRefs={sectionButtonRefs}
              onSelectSection={onSelectSection}
              onOpenRevision={onOpenRevision}
              indexLabel={String(manuscriptIndexById.get(section.id) ?? "·")}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export function ProposalSectionTree({
  sections,
  manuscriptIndexById,
  selectedSectionId,
  highlightedSectionId,
  manualFillFlags,
  sectionRevisions,
  sectionButtonRefs,
  onSelectSection,
  onOpenRevision,
}: ProposalSectionTreeProps) {
  const tree = useMemo(() => buildOutlineSectionTree(sections), [sections]);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(
    () => new Set(),
  );

  useEffect(() => {
    if (!selectedSectionId) return;
    for (const node of tree) {
      if (node.kind === "group" && groupContainsSection(node, selectedSectionId)) {
        setCollapsedGroups((current) => {
          if (!current.has(node.id)) return current;
          const next = new Set(current);
          next.delete(node.id);
          return next;
        });
        break;
      }
    }
  }, [selectedSectionId, tree]);

  return (
    <ul className="proposal-section-tree">
      {tree.map((node) =>
        node.kind === "group" ? (
          <SectionGroup
            key={node.id}
            group={node}
            selectedSectionId={selectedSectionId}
            highlightedSectionId={highlightedSectionId}
            manualFillFlags={manualFillFlags}
            sectionRevisions={sectionRevisions}
            sectionButtonRefs={sectionButtonRefs}
            collapsed={collapsedGroups.has(node.id)}
            onToggle={() =>
              setCollapsedGroups((current) => {
                const next = new Set(current);
                if (next.has(node.id)) next.delete(node.id);
                else next.add(node.id);
                return next;
              })
            }
            onSelectSection={onSelectSection}
            onOpenRevision={onOpenRevision}
            manuscriptIndexById={manuscriptIndexById}
          />
        ) : (
          <SectionRow
            key={node.section.id}
            section={node.section}
            depth={0}
            active={selectedSectionId === node.section.id}
            highlighted={highlightedSectionId === node.section.id}
            flagCount={sectionManualFillCount(node.section.id, manualFillFlags)}
            hasRevision={Boolean(sectionRevisions[node.section.id])}
            sectionButtonRefs={sectionButtonRefs}
            onSelectSection={onSelectSection}
            onOpenRevision={onOpenRevision}
            indexLabel={String(
              manuscriptIndexById.get(node.section.id) ?? "·",
            )}
          />
        ),
      )}
    </ul>
  );
}
