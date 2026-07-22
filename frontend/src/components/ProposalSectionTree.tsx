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

function SectionDraftCheckbox({
  checked,
  needsAttention,
}: {
  checked: boolean;
  needsAttention: boolean;
}) {
  return (
    <span
      className={`proposal-section-checkbox ${checked ? "is-checked" : ""} ${
        needsAttention ? "needs-attention" : ""
      }`}
      aria-hidden
    >
      {checked ? (
        <svg className="h-3 w-3" viewBox="0 0 12 12" fill="none">
          <path
            d="M2.5 6.2 4.8 8.5 9.5 3.8"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      ) : null}
    </span>
  );
}

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
  onDeleteSection?: (sectionId: string) => void;
}

function sectionListLabel(
  section: OutlineSection,
  manuscriptIndexById: Map<string, number>,
): string {
  const title = (section.title || "").trim();
  // Already numbered like "3.1 — Deschutes" or "2.2 — Gil"
  if (/^\d+(\.\d+)?\s*[—\-–.:]/.test(title) || /^\d+\.\d+/.test(title)) {
    return title;
  }
  const n = manuscriptIndexById.get(section.id);
  if (n == null) return title;
  return `${n}. ${title}`;
}

function SectionRow({
  section,
  depth,
  active,
  highlighted,
  flagCount,
  hasRevision,
  canDelete,
  listLabel,
  sectionButtonRefs,
  onSelectSection,
  onOpenRevision,
  onDeleteSection,
}: {
  section: OutlineSection;
  depth: number;
  active: boolean;
  highlighted: boolean;
  flagCount: number;
  hasRevision: boolean;
  canDelete: boolean;
  listLabel: string;
  sectionButtonRefs: React.MutableRefObject<Map<string, HTMLButtonElement>>;
  onSelectSection: (sectionId: string) => void;
  onOpenRevision: (sectionId: string) => void;
  onDeleteSection?: (sectionId: string) => void;
}) {
  const hasContent = Boolean(section.content.trim());
  const needsAttention = flagCount > 0 || hasRevision;
  const titleHint = [
    hasContent ? "Draft has content" : "Not drafted yet",
    flagCount > 0 ? `${flagCount} fill-in tag(s)` : "",
    hasRevision
      ? "Section updated — double-click title area in review for changes"
      : "",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <li className="proposal-section-tree-row">
      <button
        type="button"
        ref={(node) => {
          if (node) sectionButtonRefs.current.set(section.id, node);
          else sectionButtonRefs.current.delete(section.id);
        }}
        onClick={() => onSelectSection(section.id)}
        onDoubleClick={() => {
          if (hasRevision) onOpenRevision(section.id);
        }}
        title={titleHint}
        aria-current={active ? "true" : undefined}
        className={`proposal-section-list-item ${
          depth > 0 ? "proposal-section-list-item--child" : ""
        } ${active ? "is-active" : ""} ${highlighted ? "is-flag-target" : ""}`}
        style={depth > 0 ? { paddingLeft: `${8 + depth * 10}px` } : undefined}
      >
        <SectionDraftCheckbox
          checked={hasContent}
          needsAttention={needsAttention && !hasContent}
        />
        <span
          className={`min-w-0 flex-1 truncate text-left text-[13px] leading-snug ${
            active
              ? "font-semibold text-zo-orange"
              : "font-medium text-foreground"
          }`}
        >
          {listLabel}
        </span>
      </button>
      {canDelete && onDeleteSection ? (
        <button
          type="button"
          className="proposal-section-delete-btn"
          aria-label={`Delete ${section.title}`}
          title="Delete section"
          onClick={(e) => {
            e.stopPropagation();
            onDeleteSection(section.id);
          }}
        >
          ×
        </button>
      ) : null}
    </li>
  );
}

function SectionGroup({
  group,
  selectedSectionId,
  highlightedSectionId,
  manualFillFlags,
  sectionRevisions,
  canDelete,
  manuscriptIndexById,
  sectionButtonRefs,
  collapsed,
  onToggle,
  onSelectSection,
  onOpenRevision,
  onDeleteSection,
}: {
  group: OutlineTreeGroup;
  selectedSectionId: string | null;
  highlightedSectionId: string | null;
  manualFillFlags: ManualFillFlag[];
  sectionRevisions: Record<string, SectionRevisionRecord>;
  canDelete: boolean;
  manuscriptIndexById: Map<string, number>;
  sectionButtonRefs: React.MutableRefObject<Map<string, HTMLButtonElement>>;
  collapsed: boolean;
  onToggle: () => void;
  onSelectSection: (sectionId: string) => void;
  onOpenRevision: (sectionId: string) => void;
  onDeleteSection?: (sectionId: string) => void;
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
        <span className="proposal-section-tree-group-label min-w-0 flex-1 text-left">
          {group.label}
        </span>
        <span className="shrink-0 text-[10px] font-semibold tabular-nums text-zo-text-muted">
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
              canDelete={canDelete}
              listLabel={sectionListLabel(section, manuscriptIndexById)}
              sectionButtonRefs={sectionButtonRefs}
              onSelectSection={onSelectSection}
              onOpenRevision={onOpenRevision}
              onDeleteSection={onDeleteSection}
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
  onDeleteSection,
}: ProposalSectionTreeProps) {
  const tree = useMemo(() => buildOutlineSectionTree(sections), [sections]);
  const canDelete = sections.length > 1;
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
            canDelete={canDelete}
            manuscriptIndexById={manuscriptIndexById}
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
            onDeleteSection={onDeleteSection}
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
            canDelete={canDelete}
            listLabel={sectionListLabel(node.section, manuscriptIndexById)}
            sectionButtonRefs={sectionButtonRefs}
            onSelectSection={onSelectSection}
            onOpenRevision={onOpenRevision}
            onDeleteSection={onDeleteSection}
          />
        ),
      )}
    </ul>
  );
}
