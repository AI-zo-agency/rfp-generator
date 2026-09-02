"use client";

import { AppsList24Regular, ChevronDown24Regular, ChevronUp24Regular, Grid24Regular } from "@fluentui/react-icons";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  KnowledgeBaseDocumentCard,
  KnowledgeBaseDocumentListRow,
  KnowledgeBaseEmptyState,
} from "@/components/KnowledgeBaseDocumentViews";
import { KB_DOCUMENT_TYPES } from "@/lib/kb-document-types";
import type { KnowledgeBaseStatus } from "@/lib/knowledge-base-api";
import {
  categoryCounts,
  filterDocumentsByCategory,
  groupDocumentsByCategory,
  readStoredViewMode,
  storeViewMode,
  type KbViewMode,
} from "@/lib/kb-documents-view";
import type { KnowledgeBaseDocument } from "@/types/knowledge-base-doc";
import { kbAccentBorderHover } from "@/lib/kb-brand";

function ViewModeToggle({
  mode,
  onChange,
}: {
  mode: KbViewMode;
  onChange: (mode: KbViewMode) => void;
}) {
  return (
    <div
      className="inline-flex rounded-xl border border-zo-border bg-white p-1 shadow-sm"
      role="group"
      aria-label="Document view mode"
    >
      <button
        type="button"
        onClick={() => onChange("grid")}
        aria-pressed={mode === "grid"}
        className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors ${
          mode === "grid"
            ? "bg-[#ef5018] text-white shadow-[0_4px_14px_rgba(239,80,24,0.22)]"
            : "text-zo-text-muted hover:bg-[#fff4ef] hover:text-[#ef5018]"
        }`}
      >
        <Grid24Regular className="h-4 w-4" aria-hidden />
        Grid
      </button>
      <button
        type="button"
        onClick={() => onChange("list")}
        aria-pressed={mode === "list"}
        className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors ${
          mode === "list"
            ? "bg-[#ef5018] text-white shadow-[0_4px_14px_rgba(239,80,24,0.22)]"
            : "text-zo-text-muted hover:bg-[#fff4ef] hover:text-[#ef5018]"
        }`}
      >
        <AppsList24Regular className="h-4 w-4" aria-hidden />
        List
      </button>
    </div>
  );
}

function CategoryFilterBar({
  selected,
  counts,
  onChange,
}: {
  selected: string | "all";
  counts: Map<string, number>;
  onChange: (category: string | "all") => void;
}) {
  const total = [...counts.values()].reduce((sum, count) => sum + count, 0);

  const knownValues = new Set(KB_DOCUMENT_TYPES.map((type) => type.value));
  const extraCategories = [...counts.keys()].filter(
    (category) => !knownValues.has(category)
  );

  const chips = [
    { value: "all" as const, label: "All categories", count: total },
    ...KB_DOCUMENT_TYPES.map((type) => ({
      value: type.value,
      label: type.label,
      count: counts.get(type.value) ?? 0,
    })),
    ...extraCategories.map((category) => ({
      value: category,
      label: category,
      count: counts.get(category) ?? 0,
    })),
  ];

  return (
    <div className="min-w-0 flex-1">
      <p className="mb-2 text-[11px] font-bold uppercase tracking-widest text-zo-text-muted">
        Filter by category
      </p>
      <div className="flex gap-2 overflow-x-auto pb-1 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {chips.map((chip) => {
          const active = selected === chip.value;
          return (
            <button
              key={chip.value}
              type="button"
              onClick={() => onChange(chip.value)}
              className={`inline-flex shrink-0 items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-semibold transition-colors ${
                active
                  ? "border-[#ef5018] bg-[#ef5018] text-white shadow-[0_4px_14px_rgba(239,80,24,0.22)]"
                  : chip.count === 0
                    ? "border-zo-border/70 bg-[#fff4ef]/60 text-zo-text-muted hover:border-[rgba(239,80,24,0.3)]"
                    : "border-zo-border bg-white text-zo-text-secondary hover:border-[rgba(239,80,24,0.4)] hover:text-[#ef5018]"
              }`}
            >
              <span>{chip.label}</span>
              <span
                className={`rounded-full px-1.5 py-0.5 text-[10px] ${
                  active
                    ? "bg-white/20 text-white"
                    : "bg-zo-warm-gray text-zo-text-muted"
                }`}
              >
                {chip.count}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function CategorySection({
  category,
  title,
  count,
  viewMode,
  documents,
  containerTag,
  onDeleted,
  showCategoryInCards,
}: {
  category: string;
  title: string;
  count: number;
  viewMode: KbViewMode;
  documents: KnowledgeBaseDocument[];
  containerTag: string;
  onDeleted: () => void | Promise<void>;
  showCategoryInCards: boolean;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const sectionId = category.replace(/[^a-z0-9_-]+/gi, "-");

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3 border-b border-zo-border/80 pb-2">
        <button
          type="button"
          onClick={() => setCollapsed((open) => !open)}
          aria-expanded={!collapsed}
          aria-controls={`kb-section-${sectionId}`}
          className="group flex min-w-0 flex-1 items-center gap-3 text-left"
        >
          <span className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-zo-border bg-white text-zo-text-muted transition-colors ${kbAccentBorderHover}`}>
            {collapsed ? (
              <ChevronDown24Regular className="h-4 w-4" aria-hidden />
            ) : (
              <ChevronUp24Regular className="h-4 w-4" aria-hidden />
            )}
          </span>
          <div className="min-w-0">
            <h3 className="font-heading text-lg font-bold text-foreground group-hover:text-[#ef5018]">
              {title}
            </h3>
            <p className="text-xs text-zo-text-muted">
              {count} document{count === 1 ? "" : "s"}
            </p>
          </div>
        </button>
      </div>

      {!collapsed ? (
        <div id={`kb-section-${sectionId}`}>
          {viewMode === "grid" ? (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {documents.map((document) => (
                <KnowledgeBaseDocumentCard
                  key={document.id}
                  document={document}
                  containerTag={containerTag}
                  onDeleted={onDeleted}
                  showCategory={showCategoryInCards}
                />
              ))}
            </div>
          ) : (
            <div className="zo-card overflow-hidden">
              {documents.map((document) => (
                <KnowledgeBaseDocumentListRow
                  key={document.id}
                  document={document}
                  containerTag={containerTag}
                  onDeleted={onDeleted}
                />
              ))}
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}

export function KnowledgeBaseGrid({
  reloadToken = 0,
  onStatusChange,
  onStatsChange,
}: {
  reloadToken?: number;
  onStatusChange?: (status: KnowledgeBaseStatus) => void;
  onStatsChange?: (stats: {
    documentCount: number;
    categoryCount: number;
    loading: boolean;
  }) => void;
}) {
  const [documents, setDocuments] = useState<KnowledgeBaseDocument[]>([]);
  const [status, setStatus] = useState<KnowledgeBaseStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [viewMode, setViewMode] = useState<KbViewMode>("grid");
  const [selectedCategory, setSelectedCategory] = useState<string | "all">("all");

  const loadDocuments = useCallback(async () => {
    setLoading(true);
    try {
      const [docsRes, statusRes] = await Promise.all([
        fetch("/api/knowledge-base/documents"),
        fetch("/api/knowledge-base/status"),
      ]);

      if (docsRes.ok) {
        const body = (await docsRes.json()) as {
          documents: KnowledgeBaseDocument[];
        };
        setDocuments(body.documents ?? []);
      } else {
        setDocuments([]);
      }

      if (statusRes.ok) {
        setStatus((await statusRes.json()) as KnowledgeBaseStatus);
      }
    } catch {
      setDocuments([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments, reloadToken]);

  useEffect(() => {
    setViewMode(readStoredViewMode());
  }, []);

  const counts = useMemo(() => categoryCounts(documents), [documents]);

  useEffect(() => {
    onStatsChange?.({
      documentCount: documents.length,
      categoryCount: counts.size,
      loading,
    });
  }, [documents.length, counts.size, loading, onStatsChange]);

  useEffect(() => {
    if (status) onStatusChange?.(status);
  }, [status, onStatusChange]);

  const filteredDocuments = useMemo(
    () => filterDocumentsByCategory(documents, selectedCategory),
    [documents, selectedCategory]
  );
  const groupedDocuments = useMemo(
    () => groupDocumentsByCategory(filteredDocuments),
    [filteredDocuments]
  );

  const containerTag = status?.containerTag ?? "zo-agency";
  const isFiltered = selectedCategory !== "all";

  function handleViewModeChange(mode: KbViewMode) {
    setViewMode(mode);
    storeViewMode(mode);
  }

  return (
    <div className="space-y-6">
      <div>
        <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h2 className="font-heading text-xl font-bold text-foreground">
              Uploaded documents
            </h2>
            <p className="mt-1 text-sm text-zo-text-muted">
              Grouped by type in container{" "}
              <strong className="font-semibold text-foreground">{containerTag}</strong>
            </p>
          </div>
          {!loading && documents.length > 0 ? (
            <ViewModeToggle mode={viewMode} onChange={handleViewModeChange} />
          ) : null}
        </div>

        {!loading && documents.length > 0 ? (
          <div className="zo-card mb-6 flex flex-wrap items-end justify-between gap-4 p-4">
            <CategoryFilterBar
              selected={selectedCategory}
              counts={counts}
              onChange={setSelectedCategory}
            />
          </div>
        ) : null}

        {loading ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {["a", "b", "c", "d", "e", "f"].map((key) => (
              <div
                key={key}
                className="zo-card h-36 animate-pulse bg-zo-warm-gray/40"
              />
            ))}
          </div>
        ) : documents.length === 0 ? (
          <KnowledgeBaseEmptyState
            filtered={false}
            onUploaded={loadDocuments}
          />
        ) : filteredDocuments.length === 0 ? (
          <KnowledgeBaseEmptyState
            filtered
            onUploaded={loadDocuments}
          />
        ) : (
          <div className="space-y-10">
            {groupedDocuments.map((group) => (
              <CategorySection
                key={group.category}
                category={group.category}
                title={group.categoryTitle}
                count={group.documents.length}
                viewMode={viewMode}
                documents={group.documents}
                containerTag={containerTag}
                onDeleted={loadDocuments}
                showCategoryInCards={isFiltered}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
