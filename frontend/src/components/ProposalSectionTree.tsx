"use client";

import { useEffect, useMemo, useState } from "react";
import type { ManualFillFlag } from "@/lib/proposal-manual-flags";
import {
  buildOutlineSectionTree,
  buildRfpTabDisplayNumbers,
  groupContainsSection,
  sectionListLabel,
  type OutlineTreeGroup,
} from "@/lib/proposal-outline-tree";
import {
  classifySectionHealth,
  deadSectionLabel,
  isManuscriptSectionDrafted,
} from "@/lib/proposal-section-health";
import type { OutlineSection } from "@/types/proposal";
import type { SectionRevisionRecord } from "./DraftSectionEditor";

function SectionDragGrip() {
  return (
    <svg
      className="proposal-section-drag-grip"
      width="14"
      height="18"
      viewBox="0 0 14 18"
      aria-hidden
    >
      <circle cx="4" cy="3" r="1.75" fill="currentColor" />
      <circle cx="10" cy="3" r="1.75" fill="currentColor" />
      <circle cx="4" cy="9" r="1.75" fill="currentColor" />
      <circle cx="10" cy="9" r="1.75" fill="currentColor" />
      <circle cx="4" cy="15" r="1.75" fill="currentColor" />
      <circle cx="10" cy="15" r="1.75" fill="currentColor" />
    </svg>
  );
}

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

/** Reorder flat section list so `fromId` lands at `toId`'s index. */
export function reorderSectionsById(
  sections: OutlineSection[],
  fromId: string,
  toId: string,
): OutlineSection[] {
  if (fromId === toId) return sections;
  const fromIndex = sections.findIndex((s) => s.id === fromId);
  const toIndex = sections.findIndex((s) => s.id === toId);
  if (fromIndex < 0 || toIndex < 0) return sections;
  const next = [...sections];
  const [item] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, item);
  return next;
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
  /** Drag-and-drop reorder of sidebar tabs (flat outline order). */
  onReorderSection?: (fromId: string, toId: string) => void;
}

function SectionRow({
  section,
  depth,
  active,
  highlighted,
  flagCount,
  hasRevision,
  canDelete,
  canDrag,
  listLabel,
  isDropTarget,
  sectionButtonRefs,
  onSelectSection,
  onOpenRevision,
  onDeleteSection,
  onDragStartSection,
  onDragOverSection,
  onDropSection,
  onDragEndSection,
}: {
  section: OutlineSection;
  depth: number;
  active: boolean;
  highlighted: boolean;
  flagCount: number;
  hasRevision: boolean;
  canDelete: boolean;
  canDrag: boolean;
  listLabel: string;
  isDropTarget: boolean;
  sectionButtonRefs: React.MutableRefObject<Map<string, HTMLButtonElement>>;
  onSelectSection: (sectionId: string) => void;
  onOpenRevision: (sectionId: string) => void;
  onDeleteSection?: (sectionId: string) => void;
  onDragStartSection?: (sectionId: string) => void;
  onDragOverSection?: (sectionId: string) => void;
  onDropSection?: (sectionId: string) => void;
  onDragEndSection?: () => void;
}) {
  const health = classifySectionHealth(section.content);
  const hasContent = isManuscriptSectionDrafted(section);
  const needsAttention = flagCount > 0 || hasRevision || !hasContent;
  let draftHint = "Heading only — not drafted yet";
  if (hasContent) {
    draftHint = "Draft has content";
  } else if (health) {
    draftHint = deadSectionLabel(health);
  }
  const titleHint = [
    draftHint,
    flagCount > 0 ? `${flagCount} fill-in tag(s)` : "",
    hasRevision
      ? "Section updated — double-click title area in review for changes"
      : "",
    canDrag ? "Drag this row to reorder" : "",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <li
      className={`proposal-section-tree-row ${canDrag ? "is-draggable" : ""} ${
        isDropTarget ? "is-drop-target" : ""
      }`}
      draggable={canDrag}
      onDragStart={
        canDrag
          ? (e) => {
              const target = e.target as HTMLElement | null;
              if (target?.closest?.(".proposal-section-delete-btn")) {
                e.preventDefault();
                return;
              }
              e.dataTransfer.effectAllowed = "move";
              e.dataTransfer.setData("text/plain", section.id);
              onDragStartSection?.(section.id);
            }
          : undefined
      }
      onDragEnd={canDrag ? () => onDragEndSection?.() : undefined}
      onDragOver={
        canDrag
          ? (e) => {
              e.preventDefault();
              e.dataTransfer.dropEffect = "move";
              onDragOverSection?.(section.id);
            }
          : undefined
      }
      onDrop={
        canDrag
          ? (e) => {
              e.preventDefault();
              onDropSection?.(section.id);
            }
          : undefined
      }
    >
      {canDrag ? (
        <span
          className="proposal-section-drag-handle"
          title="Drag row to reorder"
          aria-hidden
        >
          <SectionDragGrip />
        </span>
      ) : null}
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
          className={`proposal-section-list-label min-w-0 flex-1 truncate text-left ${
            active ? "is-active-label" : ""
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
          draggable={false}
          onMouseDown={(e) => e.stopPropagation()}
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
  canDrag,
  dragOverId,
  rfpTabNumberById,
  sectionButtonRefs,
  collapsed,
  onToggle,
  onSelectSection,
  onOpenRevision,
  onDeleteSection,
  onDragStartSection,
  onDragOverSection,
  onDropSection,
  onDragEndSection,
}: {
  group: OutlineTreeGroup;
  selectedSectionId: string | null;
  highlightedSectionId: string | null;
  manualFillFlags: ManualFillFlag[];
  sectionRevisions: Record<string, SectionRevisionRecord>;
  canDelete: boolean;
  canDrag: boolean;
  dragOverId: string | null;
  rfpTabNumberById: Map<string, number>;
  sectionButtonRefs: React.MutableRefObject<Map<string, HTMLButtonElement>>;
  collapsed: boolean;
  onToggle: () => void;
  onSelectSection: (sectionId: string) => void;
  onOpenRevision: (sectionId: string) => void;
  onDeleteSection?: (sectionId: string) => void;
  onDragStartSection?: (sectionId: string) => void;
  onDragOverSection?: (sectionId: string) => void;
  onDropSection?: (sectionId: string) => void;
  onDragEndSection?: () => void;
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
        <span
          className="proposal-section-tree-group-label min-w-0 flex-1 text-left"
          title={group.label}
        >
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
              canDrag={canDrag}
              listLabel={sectionListLabel(section, rfpTabNumberById)}
              isDropTarget={dragOverId === section.id}
              sectionButtonRefs={sectionButtonRefs}
              onSelectSection={onSelectSection}
              onOpenRevision={onOpenRevision}
              onDeleteSection={onDeleteSection}
              onDragStartSection={onDragStartSection}
              onDragOverSection={onDragOverSection}
              onDropSection={onDropSection}
              onDragEndSection={onDragEndSection}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export function ProposalSectionTree({
  sections,
  selectedSectionId,
  highlightedSectionId,
  manualFillFlags,
  sectionRevisions,
  sectionButtonRefs,
  onSelectSection,
  onOpenRevision,
  onDeleteSection,
  onReorderSection,
}: ProposalSectionTreeProps) {
  const tree = useMemo(() => buildOutlineSectionTree(sections), [sections]);
  const rfpTabNumberById = useMemo(
    () => buildRfpTabDisplayNumbers(sections),
    [sections],
  );
  const canDelete = sections.length > 1;
  const canDrag = Boolean(onReorderSection) && sections.length > 1;
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(
    () => new Set(),
  );
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);

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

  const handleDrop = (toId: string) => {
    if (!onReorderSection || !draggingId || draggingId === toId) {
      setDraggingId(null);
      setDragOverId(null);
      return;
    }
    onReorderSection(draggingId, toId);
    setDraggingId(null);
    setDragOverId(null);
  };

  const dragProps = canDrag
    ? {
        onDragStartSection: (id: string) => {
          setDraggingId(id);
          setDragOverId(id);
        },
        onDragOverSection: (id: string) => setDragOverId(id),
        onDropSection: handleDrop,
        onDragEndSection: () => {
          setDraggingId(null);
          setDragOverId(null);
        },
      }
    : {};

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
            canDrag={canDrag}
            dragOverId={dragOverId}
            rfpTabNumberById={rfpTabNumberById}
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
            {...dragProps}
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
            canDrag={canDrag}
            listLabel={sectionListLabel(node.section, rfpTabNumberById)}
            isDropTarget={dragOverId === node.section.id}
            sectionButtonRefs={sectionButtonRefs}
            onSelectSection={onSelectSection}
            onOpenRevision={onOpenRevision}
            onDeleteSection={onDeleteSection}
            {...dragProps}
          />
        ),
      )}
    </ul>
  );
}
