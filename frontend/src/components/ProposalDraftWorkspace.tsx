"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  buildDefaultOutline,
  computeDraftStats,
  countWords,
  createCustomSection,
} from "@/lib/proposal-draft";
import {
  findBudgetSection,
  mergeBudgetIntoOutline,
} from "@/lib/proposal-budget-content";
import {
  fetchProposalDraft,
  generateFullProposalStaged,
  generateProposalPricing,
  generateProposalSections1to3,
  recoverProposalDraftIfSaved,
  runPhase4PreSubmitReview,
  runPhase4PreSubmitAutoFix,
  saveProposalDraft,
  type FullProposalProgress,
} from "@/lib/proposal-api";
import type { OutlineSection, ProposalBudget, ProposalOutline, ProposalResearch, PreSubmitReview } from "@/types/proposal";
import type { RfpRecord } from "@/types/rfp";
import { SectionStatusPill } from "./SectionStatusPill";
import { MarkdownReportBody } from "./MarkdownReportBody";
import { SectionEditChat } from "./SectionEditChat";
import { ProposalBudgetPanel } from "./ProposalBudgetPanel";
import { ProposalReviewPanel } from "./ProposalReviewPanel";
import { OutlineTabs, TabPanel } from "./ui/OutlineTabs";

type WorkspaceTab = "outline" | "content" | "pricing" | "review" | "export";

const baseWorkspaceTabs = [
  { id: "outline", label: "Outline" },
  { id: "content", label: "Content" },
  { id: "pricing", label: "Budget" },
  { id: "review", label: "Review" },
  { id: "export", label: "Export" },
];

function StatCard({
  label,
  value,
  sub,
  variant = "default",
}: {
  label: string;
  value: string | number;
  sub?: string;
  variant?: "default" | "danger" | "success";
}) {
  const variantClass =
    variant === "danger"
      ? "proposal-stat-card--danger"
      : variant === "success"
        ? "proposal-stat-card--success"
        : "";
  return (
    <div className={`proposal-stat-card ${variantClass}`}>
      <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-zo-text-muted">
        {label}
      </p>
      <p className="font-heading mt-1 text-xl font-bold leading-none tabular-nums text-inherit md:text-2xl">
        {value}
      </p>
      {sub ? (
        <p className="mt-1.5 text-[11px] font-medium leading-snug text-zo-text-muted">
          {sub}
        </p>
      ) : (
        <span className="mt-1 block h-[0.85rem]" aria-hidden />
      )}
    </div>
  );
}

function ResearchStatusPanel({
  research,
  fullProposalDone,
  phase3Done,
  sectionCount,
  defaultExpanded,
}: {
  research: ProposalResearch;
  fullProposalDone: boolean;
  phase3Done: boolean;
  sectionCount: number;
  defaultExpanded: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const avoidances = research.writingAvoidances ?? [];
  const statusLabel = fullProposalDone
    ? "Full proposal ready"
    : phase3Done
      ? "RFP sections drafted"
      : "Research ready";

  return (
    <div className="mx-3 mb-3 md:mx-4">
      <div className="overflow-hidden rounded-2xl border border-zo-border/80 bg-white shadow-sm">
        <button
          type="button"
          onClick={() => setExpanded((open) => !open)}
          className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left transition-smooth hover:bg-[#fafbfc] md:px-6"
          aria-expanded={expanded}
        >
          <div className="min-w-0">
            <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-zo-orange">
              {statusLabel}
            </p>
            <p className="mt-1.5 text-sm leading-relaxed text-zo-text-secondary">
              {research.rfpSections.length} mapped sections ·{" "}
              {research.evidenceCorpus.length} evidence items
              {fullProposalDone ? ` · ${sectionCount} in manuscript` : ""}
            </p>
          </div>
          <svg
            className={`h-5 w-5 shrink-0 text-zo-text-muted transition-transform ${
              expanded ? "rotate-180" : ""
            }`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
            aria-hidden
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
          </svg>
        </button>

        {expanded && (
          <div className="space-y-6 border-t border-zo-border/60 px-5 py-5 md:px-6 md:py-6">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <div className="rounded-xl bg-[#fafbfc] px-4 py-3">
                <p className="text-[10px] font-bold uppercase tracking-wide text-zo-text-muted">
                  Static
                </p>
                <p className="mt-1 text-sm text-foreground">
                  Sections 1–3 — company, team, case studies
                </p>
              </div>
              <div className="rounded-xl bg-[#fafbfc] px-4 py-3">
                <p className="text-[10px] font-bold uppercase tracking-wide text-zo-text-muted">
                  RFP-varying
                </p>
                <p className="mt-1 text-sm text-foreground">
                  {research.rfpSections.length} sections from structural map
                </p>
              </div>
              <div className="rounded-xl bg-[#fafbfc] px-4 py-3 sm:col-span-2 lg:col-span-1">
                <p className="text-[10px] font-bold uppercase tracking-wide text-zo-text-muted">
                  Retrieval
                </p>
                <p className="mt-1 text-sm text-foreground">
                  {research.retrievalRounds} round
                  {research.retrievalRounds === 1 ? "" : "s"} ·{" "}
                  {research.evidenceCorpus.length} KB excerpts
                </p>
              </div>
            </div>

            {avoidances.length > 0 && (
              <div className="rounded-xl border border-amber-200/80 bg-amber-50/50 px-4 py-4">
                <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-amber-900/70">
                  Avoid from past losses (08_ / 09_)
                </p>
                <ul className="mt-3 space-y-3">
                  {avoidances.map((item) => (
                    <li
                      key={item}
                      className="text-sm leading-relaxed text-amber-950/85"
                    >
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {(research.proofPoints?.length ?? 0) > 0 && (
              <div className="rounded-xl border border-zo-border bg-white px-4 py-4">
                <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-zo-orange">
                  Proof points (requirement → case study)
                </p>
                <ul className="mt-3 space-y-3">
                  {research.proofPoints!.slice(0, 6).map((point) => (
                    <li key={`${point.requirement}-${point.caseStudy}`} className="text-sm">
                      <p className="font-medium text-foreground">{point.caseStudy}</p>
                      <p className="mt-0.5 text-zo-text-muted">{point.requirement}</p>
                      {point.narrativeHook && (
                        <p className="mt-1 text-zo-text-secondary italic">
                          {point.narrativeHook}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function IconButton({
  onClick,
  label,
  children,
  variant = "default",
}: {
  onClick: () => void;
  label: string;
  children: React.ReactNode;
  variant?: "default" | "danger";
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className={`flex h-9 w-9 items-center justify-center rounded-lg border transition-smooth ${
        variant === "danger"
          ? "border-red-200 text-zo-error hover:bg-red-50"
          : "border-zo-border text-zo-text-secondary hover:border-zo-teal hover:bg-[var(--zo-hover-bg)] hover:text-zo-orange"
      }`}
    >
      {children}
    </button>
  );
}

interface ProposalDraftWorkspaceProps {
  rfp: RfpRecord;
}

export function ProposalDraftWorkspace({ rfp }: ProposalDraftWorkspaceProps) {
  const [outline, setOutline] = useState<ProposalOutline>(() =>
    buildDefaultOutline(rfp)
  );
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("outline");
  const [selectedSectionId, setSelectedSectionId] = useState<string | null>(
    null
  );
  const [isGenerating, setIsGenerating] = useState(false);
  const [isFullProposalRunning, setIsFullProposalRunning] = useState(false);
  const [fullProposalProgress, setFullProposalProgress] =
    useState<FullProposalProgress | null>(null);
  const [isPricingRunning, setIsPricingRunning] = useState(false);
  const [pricingError, setPricingError] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [isReviewRunning, setIsReviewRunning] = useState(false);
  const [isAutoFixing, setIsAutoFixing] = useState(false);
  const [autoFixMode, setAutoFixMode] = useState<"quick" | "ai" | null>(null);
  const autoFixAbortRef = useRef<AbortController | null>(null);
  const [autoFixNotice, setAutoFixNotice] = useState<string | null>(null);
  const [presubmitReview, setPresubmitReview] = useState<PreSubmitReview | null>(null);
  const [budget, setBudget] = useState<ProposalBudget | null>(null);
  const [research, setResearch] = useState<ProposalResearch | null>(null);
  const [newSectionTitle, setNewSectionTitle] = useState("");
  const [hydrated, setHydrated] = useState(false);
  const [copied, setCopied] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [generateNotice, setGenerateNotice] = useState<string | null>(null);
  const [provider, setProvider] = useState<string | null>(null);
  const skipNextSaveRef = useRef(false);
  const editorScrollRef = useRef<HTMLDivElement>(null);
  const sectionButtonRefs = useRef<Map<string, HTMLButtonElement>>(new Map());

  useEffect(() => {
    if (!selectedSectionId) return;
    sectionButtonRefs.current
      .get(selectedSectionId)
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    editorScrollRef.current?.scrollTo({ top: 0 });
  }, [selectedSectionId]);

  const selectSection = useCallback((id: string) => {
    setSelectedSectionId(id);
  }, []);

  const handleJumpToSection = useCallback(
    (sectionId: string) => {
      setActiveTab("outline");
      setSelectedSectionId(sectionId);
      requestAnimationFrame(() => {
        sectionButtonRefs.current.get(sectionId)?.scrollIntoView({
          block: "nearest",
          behavior: "smooth",
        });
      });
    },
    []
  );

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const { draft, provider: p, research: r } = await fetchProposalDraft(rfp.id);
      if (cancelled) return;

      if (draft) {
        setOutline(draft);
        setSelectedSectionId(draft.sections[0]?.id ?? null);
        setActiveTab(draft.sections.some((s) => s.content) ? "content" : "outline");
        setProvider(p ?? null);
      } else {
        const defaults = buildDefaultOutline(rfp);
        setOutline(defaults);
        setSelectedSectionId(defaults.sections[0]?.id ?? null);
      }
      setResearch(r ?? null);
      setBudget(r?.budget ?? null);
      setPresubmitReview(r?.presubmitReview ?? null);
      setHydrated(true);
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [rfp]);

  useEffect(() => {
    if (!hydrated) return;
    if (skipNextSaveRef.current) {
      skipNextSaveRef.current = false;
      return;
    }
    const timer = setTimeout(() => {
      void saveProposalDraft(rfp.id, outline);
    }, 800);
    return () => clearTimeout(timer);
  }, [outline, rfp.id, hydrated]);

  const stats = useMemo(() => computeDraftStats(outline), [outline]);
  const pageLimit = rfp.pageLimit ?? 30;
  const pageOverLimit = stats.totalPages > pageLimit;
  const pageProgress = Math.min(100, (stats.totalPages / pageLimit) * 100);
  const pageOverflowProgress = pageOverLimit
    ? Math.min(100, ((stats.totalPages - pageLimit) / pageLimit) * 100)
    : 0;
  const sectionProgress = Math.round(
    (stats.generatedSections / outline.sections.length) * 100
  );

  const reviewCriticalCount =
    presubmitReview?.issues.filter((i) => i.severity === "critical").length ?? 0;

  const workspaceTabs = useMemo(
    () =>
      baseWorkspaceTabs.map((tab) =>
        tab.id === "review" && reviewCriticalCount > 0
          ? { ...tab, count: reviewCriticalCount }
          : tab
      ),
    [reviewCriticalCount]
  );

  const selectedSection = outline.sections.find(
    (s) => s.id === selectedSectionId
  );

  const sections1to3Done = useMemo(
    () =>
      outline.sections
        .slice(0, 3)
        .every((section) => section.content.trim().length > 0),
    [outline.sections]
  );

  const phase2Done = (research?.evidenceCorpus?.length ?? 0) > 0;

  const phase3Done = useMemo(
    () =>
      sections1to3Done &&
      phase2Done &&
      outline.sections.some(
        (section) =>
          section.source === "rfp" && section.content.trim().length > 0
      ),
    [outline.sections, phase2Done, sections1to3Done]
  );

  const fullProposalDone = phase3Done;

  const anyPipelineRunning =
    isGenerating || isFullProposalRunning || isPricingRunning || isReviewRunning || isAutoFixing;

  const handleRunReview = useCallback(async () => {
    setIsReviewRunning(true);
    setReviewError(null);
    try {
      const { review, research: updatedResearch } = await runPhase4PreSubmitReview(
        rfp.id
      );
      setPresubmitReview(review);
      setResearch(updatedResearch);
      setActiveTab("review");
    } catch (error) {
      setReviewError(
        error instanceof Error ? error.message : "Pre-submit review failed"
      );
    } finally {
      setIsReviewRunning(false);
    }
  }, [rfp.id]);

  const handleAutoFix = useCallback(async () => {
    autoFixAbortRef.current?.abort();
    const controller = new AbortController();
    autoFixAbortRef.current = controller;
    setIsAutoFixing(true);
    setAutoFixMode("ai");
    setReviewError(null);
    setAutoFixNotice(null);
    try {
      const { review, research: updatedResearch, draft, autoFix } =
        await runPhase4PreSubmitAutoFix(rfp.id, {
          useLlm: true,
          signal: controller.signal,
        });
      skipNextSaveRef.current = true;
      setOutline(draft);
      setPresubmitReview(review);
      setResearch(updatedResearch);
      await saveProposalDraft(rfp.id, draft);
      setAutoFixNotice(
        `Auto-fix: ${autoFix.issuesBefore} → ${autoFix.issuesAfter} findings · ` +
          `${autoFix.sectionsPatched} section(s) updated. ` +
          (autoFix.stoppedReason === "cancelled"
            ? "Stopped — partial progress was saved."
            : autoFix.stoppedReason === "ready"
              ? "Re-check compliance items before upload."
              : autoFix.issuesAfter > 0
                ? "Re-run review or edit remaining sections manually."
                : "All fixable issues resolved.")
      );
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") {
        const recovered = await recoverProposalDraftIfSaved(rfp.id, {
          minSectionsWithContent: 3,
        });
        if (recovered) {
          skipNextSaveRef.current = true;
          setOutline(recovered.draft);
          if (recovered.research) {
            setResearch(recovered.research);
            setPresubmitReview(recovered.research.presubmitReview ?? null);
          }
          setAutoFixNotice("Stopped — saved progress loaded.");
        } else {
          setReviewError("Auto-fix stopped.");
        }
      } else {
        setReviewError(
          error instanceof Error ? error.message : "Auto-fix failed"
        );
      }
    } finally {
      if (autoFixAbortRef.current === controller) {
        autoFixAbortRef.current = null;
      }
      setIsAutoFixing(false);
      setAutoFixMode(null);
    }
  }, [rfp.id]);

  const handleStopAutoFix = useCallback(() => {
    autoFixAbortRef.current?.abort();
  }, []);

  const handleGeneratePricing = useCallback(async () => {
    if (
      budget &&
      !confirm("Regenerate budget from Supermemory? (Uses LLM tokens.)")
    ) {
      return;
    }
    setIsPricingRunning(true);
    setPricingError(null);
    try {
      const { budget: generated, research: updatedResearch, draft } =
        await generateProposalPricing(rfp.id);
      setBudget(generated);
      setResearch(updatedResearch);

      const mergedOutline = draft ?? mergeBudgetIntoOutline(outline, generated);
      skipNextSaveRef.current = true;
      setOutline(mergedOutline);
      await saveProposalDraft(rfp.id, mergedOutline);

      const budgetSection = findBudgetSection(mergedOutline.sections);
      if (budgetSection) {
        setSelectedSectionId(budgetSection.id);
        setActiveTab("content");
      } else {
        setActiveTab("pricing");
      }
    } catch (error) {
      setPricingError(
        error instanceof Error ? error.message : "Pricing generation failed"
      );
    } finally {
      setIsPricingRunning(false);
    }
  }, [rfp.id, budget, outline]);

  const handleGenerateFullProposal = useCallback(async () => {
    if (
      fullProposalDone &&
      !confirm(
        "Proposal already generated. Re-run full pipeline anyway? (Uses LLM tokens.)"
      )
    ) {
      return;
    }
    setIsFullProposalRunning(true);
    setFullProposalProgress(null);
    setGenerateError(null);
    setGenerateNotice(null);
    try {
      const { draft, research: updatedResearch } =
        await generateFullProposalStaged(rfp.id, setFullProposalProgress);
      skipNextSaveRef.current = true;
      setOutline(draft);
      if (updatedResearch) {
        setResearch(updatedResearch);
      }
      await saveProposalDraft(rfp.id, draft);
      setActiveTab("content");
      setSelectedSectionId(
        draft.sections.find((s) => s.content)?.id ?? draft.sections[0]?.id ?? null
      );
    } catch (error) {
      setFullProposalProgress("recovering");
      const recovered = await recoverProposalDraftIfSaved(rfp.id, {
        minSectionsWithContent: 10,
      });
      if (recovered) {
        skipNextSaveRef.current = true;
        setOutline(recovered.draft);
        if (recovered.research) {
          setResearch(recovered.research);
        }
        setActiveTab("content");
        setSelectedSectionId(
          recovered.draft.sections.find((s) => s.content)?.id ??
            recovered.draft.sections[0]?.id ??
            null
        );
        setGenerateNotice(
          "Connection timed out, but your proposal was saved on the server — loaded from draft."
        );
        setGenerateError(null);
      } else {
        setGenerateError(
          error instanceof Error
            ? error.message
            : "Full proposal generation failed"
        );
      }
    } finally {
      setIsFullProposalRunning(false);
      setFullProposalProgress(null);
    }
  }, [rfp.id, fullProposalDone]);

  const handleGenerateSections1to3 = useCallback(async () => {
    if (
      sections1to3Done &&
      !confirm(
        "Sections 1–3 already have content. Re-generate anyway? (Uses LLM tokens.)"
      )
    ) {
      return;
    }
    setIsGenerating(true);
    setGenerateError(null);
    setGenerateNotice(null);
    try {
      const generated = await generateProposalSections1to3(rfp.id);
      skipNextSaveRef.current = true;
      setOutline(generated);
      await saveProposalDraft(rfp.id, generated);
      setActiveTab("content");
      setSelectedSectionId(
        generated.sections.find((s) => s.content)?.id ??
          generated.sections[0]?.id ??
          null
      );
    } catch (error) {
      setGenerateError(
        error instanceof Error ? error.message : "Sections 1–3 generation failed"
      );
    } finally {
      setIsGenerating(false);
    }
  }, [rfp.id, sections1to3Done]);

  const handleResetOutline = () => {
    if (!confirm("Reset outline and clear all generated content?")) return;
    const defaults = buildDefaultOutline(rfp);
    setOutline(defaults);
    setSelectedSectionId(defaults.sections[0]?.id ?? null);
    void saveProposalDraft(rfp.id, defaults);
    setResearch(null);
  };

  const updateSection = (id: string, patch: Partial<OutlineSection>) => {
    setOutline((prev) => ({
      ...prev,
      sections: prev.sections.map((s) =>
        s.id === id ? { ...s, ...patch } : s
      ),
      updatedAt: new Date().toISOString(),
    }));
  };

  const moveSection = (id: string, direction: -1 | 1) => {
    setOutline((prev) => {
      const index = prev.sections.findIndex((s) => s.id === id);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= prev.sections.length) {
        return prev;
      }
      const sections = [...prev.sections];
      [sections[index], sections[target]] = [sections[target], sections[index]];
      return { ...prev, sections, updatedAt: new Date().toISOString() };
    });
  };

  const removeSection = (id: string) => {
    setOutline((prev) => {
      const sections = prev.sections.filter((s) => s.id !== id);
      if (selectedSectionId === id) {
        setSelectedSectionId(sections[0]?.id ?? null);
      }
      return { ...prev, sections, updatedAt: new Date().toISOString() };
    });
  };

  const addCustomSection = () => {
    const title = newSectionTitle.trim();
    if (!title) return;
    const section = createCustomSection(title);
    setOutline((prev) => ({
      ...prev,
      sections: [...prev.sections, section],
      updatedAt: new Date().toISOString(),
    }));
    setSelectedSectionId(section.id);
    setNewSectionTitle("");
  };

  const fullManuscript = outline.sections
    .filter((s) => s.content)
    .map((s) => `## ${s.title}\n\n${s.content}`)
    .join("\n\n---\n\n");

  const handleCopy = async () => {
    await navigator.clipboard.writeText(fullManuscript);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!hydrated) {
    return (
      <div className="proposal-workspace-card">
        <div className="animate-pulse space-y-4 p-8">
          <div className="h-8 w-2/3 rounded-lg bg-zo-warm-gray" />
          <div className="h-4 w-1/2 rounded bg-zo-warm-gray/70" />
          <div className="mt-8 grid grid-cols-3 gap-4">
            <div className="h-20 rounded-xl bg-zo-warm-gray/60" />
            <div className="h-20 rounded-xl bg-zo-warm-gray/60" />
            <div className="h-20 rounded-xl bg-zo-warm-gray/60" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <section className="proposal-workspace-card">
      <div className="proposal-workspace-header-wrap">
        <div className="proposal-header zo-panel-white relative overflow-hidden rounded-xl px-4 py-4 md:px-5 md:py-4">
          <div
            className="pointer-events-none absolute -right-16 -top-16 h-48 w-48 rounded-full bg-[#ef5018]/15"
            aria-hidden
          />

          <div className="relative grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-full bg-[#ef5018] px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-white">
                  Proposal Draft
                </span>
                <span className="text-xs font-medium text-black/65">
                  {rfp.client}
                </span>
              </div>
              <h2 className="mt-2 text-lg font-bold leading-snug tracking-tight text-black md:text-xl lg:text-[1.45rem]">
                {rfp.title}
              </h2>
              <p className="mt-1.5 text-sm text-black/65">
                {rfp.location || "—"} · Page limit {pageLimit}
                {provider ? ` · via ${provider}` : ""}
              </p>
              {fullProposalDone && (
                <div className="mt-2.5 flex flex-wrap gap-1.5">
                  <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-emerald-800">
                    Manuscript complete
                  </span>
                  {pageOverLimit && (
                    <span className="rounded-full border border-red-200 bg-red-50 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-red-800">
                      Over page limit
                    </span>
                  )}
                  {reviewCriticalCount > 0 && (
                    <span className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-900">
                      {reviewCriticalCount} critical review issue
                      {reviewCriticalCount === 1 ? "" : "s"}
                    </span>
                  )}
                </div>
              )}
            </div>

            <div className="proposal-stat-grid">
              <StatCard
                label="Words"
                value={stats.totalWords.toLocaleString()}
              />
              <StatCard
                label="Pages"
                value={stats.totalPages}
                sub={pageOverLimit ? `${stats.totalPages - pageLimit} over limit` : `of ${pageLimit} max`}
                variant={pageOverLimit ? "danger" : "default"}
              />
              <StatCard
                label="Sections"
                value={`${stats.generatedSections}/${outline.sections.length}`}
                sub={`${sectionProgress}% done`}
                variant={sectionProgress === 100 ? "success" : "default"}
              />
            </div>
          </div>

          <div className="relative mt-4">
            <div className="mb-2 flex items-center justify-between text-xs font-semibold text-black/65">
              <span>Manuscript progress</span>
              <span className={pageOverLimit ? "text-red-700" : ""}>
                {stats.totalPages} / {pageLimit} pages
                {pageOverLimit ? ` (+${stats.totalPages - pageLimit})` : ""}
              </span>
            </div>
            <div className="proposal-progress-track">
              <div
                className={`proposal-progress-fill ${pageOverLimit ? "proposal-progress-fill--at-limit" : ""}`}
                style={{ width: `${pageProgress}%` }}
              />
              {pageOverLimit && (
                <div
                  className="proposal-progress-overflow"
                  style={{ width: `${pageOverflowProgress}%` }}
                />
              )}
            </div>
          </div>
        </div>
      </div>

      {(generateNotice || generateError) && (
        <div
          className={`border-b px-4 py-2.5 text-sm md:px-5 ${
            generateNotice
              ? "border-amber-200/80 bg-amber-50 text-amber-950"
              : "border-red-200/80 bg-red-50 text-zo-error"
          }`}
        >
          {generateNotice ?? generateError}
        </div>
      )}

      <div className="proposal-toolbar sticky top-16 z-10 flex flex-col gap-4 border-y sm:flex-row sm:items-center sm:justify-between">
        <OutlineTabs
          tabs={workspaceTabs}
          activeTab={activeTab}
          onChange={(id) => setActiveTab(id as WorkspaceTab)}
        />
        <div className="proposal-toolbar-actions flex w-full flex-wrap items-center gap-4 sm:w-auto sm:justify-end">
          <button
            type="button"
            onClick={handleResetOutline}
            className="zo-btn secondary"
            disabled={anyPipelineRunning}
          >
            Reset
          </button>
          <button
            type="button"
            onClick={handleGenerateSections1to3}
            disabled={anyPipelineRunning}
            className="zo-btn secondary disabled:opacity-60"
            title="Static zö blocks only: Company Overview, Team, Case Studies"
          >
            {isGenerating ? (
              <>
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-zo-orange/30 border-t-zo-orange" />
                Sections 1–3…
              </>
            ) : (
              "Sections 1–3 only"
            )}
          </button>
          <button
            type="button"
            onClick={handleGenerateFullProposal}
            disabled={anyPipelineRunning}
            className="zo-btn disabled:opacity-60"
            title="Static Sections 1–3 + RFP-mapped sections from evidence (full pipeline)"
          >
            {isFullProposalRunning ? (
              <>
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-zo-white/30 border-t-zo-white" />
                {fullProposalProgress === "sections-1-3"
                  ? "Sections 1–3…"
                  : fullProposalProgress === "phase-2"
                    ? "Phase 2 research…"
                    : fullProposalProgress === "phase-3"
                      ? "Phase 3 drafting…"
                      : fullProposalProgress === "recovering"
                        ? "Checking saved draft…"
                        : "Generating…"}
              </>
            ) : (
              <>
                <svg
                  className="h-4 w-4"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z"
                  />
                </svg>
                Generate Full Proposal
              </>
            )}
          </button>
        </div>
      </div>

      {activeTab === "outline" && phase2Done && research && (
        <ResearchStatusPanel
          research={research}
          fullProposalDone={fullProposalDone}
          phase3Done={phase3Done}
          sectionCount={outline.sections.length}
          defaultExpanded={activeTab === "outline"}
        />
      )}

      <div className="proposal-workspace-body">
      {/* Outline tab */}
      <TabPanel id="outline" activeTab={activeTab} className="proposal-workspace-tab">
        <div className="proposal-outline-layout grid gap-0 lg:grid-cols-[260px_minmax(0,1fr)] lg:gap-3 lg:p-3">
          <div className="proposal-section-list flex min-h-0 flex-col overflow-hidden rounded-none border-b border-zo-border lg:rounded-xl lg:border lg:border-zo-border">
            <div className="flex shrink-0 items-center justify-between border-b border-zo-border/60 px-3 py-2.5">
              <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-zo-text-muted">
                Sections
              </p>
              <span className="text-xs font-semibold text-zo-orange">
                {outline.sections.length} total
              </span>
            </div>
            <ul className="custom-scrollbar min-h-0 flex-1 overflow-y-auto">
              {outline.sections.map((section, index) => {
                const active = selectedSectionId === section.id;
                const hasContent = Boolean(section.content);
                return (
                  <li key={section.id}>
                    <button
                      type="button"
                      ref={(node) => {
                        if (node) sectionButtonRefs.current.set(section.id, node);
                        else sectionButtonRefs.current.delete(section.id);
                      }}
                      onClick={() => selectSection(section.id)}
                      className={`proposal-section-list-item ${
                        active ? "is-active" : ""
                      }`}
                    >
                      <span
                        className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${
                          hasContent
                            ? "bg-[#ef5018] text-white"
                            : "border border-zo-border bg-[var(--zo-input-bg)] text-zo-text-muted"
                        }`}
                      >
                        {index + 1}
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
                          {section.custom ? (
                            <span className="text-[9px] font-bold uppercase text-zo-orange">
                              Custom
                            </span>
                          ) : null}
                          {section.pageLimit ? (
                            <span className="text-[10px] text-zo-text-muted">
                              {section.pageLimit} pg
                            </span>
                          ) : null}
                        </div>
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>

            <div className="shrink-0 border-t border-zo-border bg-[var(--zo-input-bg)] p-3">
              <div className="flex gap-2">
                <input
                  type="text"
                  value={newSectionTitle}
                  onChange={(e) => setNewSectionTitle(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && addCustomSection()}
                  placeholder="New section title…"
                  className="min-w-0 flex-1 zo-input px-3 py-2 text-sm outline-none transition-smooth focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10"
                />
                <button
                  type="button"
                  onClick={addCustomSection}
                  className="zo-btn shrink-0 !px-3 !py-2"
                >
                  Add
                </button>
              </div>
            </div>
          </div>

          <div className="proposal-editor-pane flex min-h-0 flex-col overflow-hidden rounded-none lg:rounded-xl lg:border lg:border-zo-border">
            {selectedSection ? (
              <>
                <div
                  ref={editorScrollRef}
                  className="custom-scrollbar min-h-0 flex-1 overflow-y-auto proposal-tab-panel"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-zo-text-muted">
                      Editing section {outline.sections.findIndex((s) => s.id === selectedSection.id) + 1}
                    </p>
                    <input
                      type="text"
                      value={selectedSection.title}
                      onChange={(e) =>
                        updateSection(selectedSection.id, {
                          title: e.target.value,
                        })
                      }
                      className="font-heading mt-2 w-full border-b-2 border-transparent bg-transparent text-xl font-bold text-foreground outline-none transition-smooth focus:border-zo-orange md:text-2xl"
                    />
                  </div>
                  <div className="flex gap-1.5">
                    <IconButton
                      onClick={() => moveSection(selectedSection.id, -1)}
                      label="Move up"
                    >
                      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5" />
                      </svg>
                    </IconButton>
                    <IconButton
                      onClick={() => moveSection(selectedSection.id, 1)}
                      label="Move down"
                    >
                      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                      </svg>
                    </IconButton>
                    {selectedSection.custom && (
                      <IconButton
                        onClick={() => removeSection(selectedSection.id)}
                        label="Remove section"
                        variant="danger"
                      >
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      </IconButton>
                    )}
                  </div>
                </div>

                <div className="mt-4 grid gap-3 sm:grid-cols-3">
                  <label className="block">
                    <span className="text-xs font-semibold text-zo-text-muted">
                      Page limit
                    </span>
                    <input
                      type="number"
                      min={1}
                      value={selectedSection.pageLimit ?? ""}
                      onChange={(e) =>
                        updateSection(selectedSection.id, {
                          pageLimit: e.target.value
                            ? Number(e.target.value)
                            : undefined,
                        })
                      }
                      className="zo-input mt-1.5 w-full px-3 py-2.5 text-sm outline-none focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10"
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs font-semibold text-zo-text-muted">
                      Word target
                    </span>
                    <input
                      type="number"
                      min={100}
                      value={selectedSection.wordTarget}
                      onChange={(e) =>
                        updateSection(selectedSection.id, {
                          wordTarget: Number(e.target.value) || 500,
                        })
                      }
                      className="zo-input mt-1.5 w-full px-3 py-2.5 text-sm outline-none focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10"
                    />
                  </label>
                  <div>
                    <span className="text-xs font-semibold text-zo-text-muted">
                      Source
                    </span>
                    <p className="mt-2.5 flex items-center gap-2">
                      <span className="rounded-lg bg-zo-warm-gray/60 px-2.5 py-1 text-xs font-semibold capitalize text-zo-text-secondary">
                        {selectedSection.source}
                      </span>
                      {selectedSection.required && (
                        <span className="text-xs font-medium text-zo-teal">
                          Required
                        </span>
                      )}
                    </p>
                  </div>
                </div>

                <label className="mt-5 block">
                  <div className="mb-2 flex items-center justify-between gap-4">
                    <span className="text-xs font-bold uppercase tracking-[0.12em] text-zo-text-muted">
                      Draft content
                    </span>
                    <span className="text-xs font-medium text-zo-text-muted">
                      {countWords(selectedSection.content)} words
                      {selectedSection.wordTarget > 0 && (
                        <span className="text-zo-text-muted/70">
                          {" "}
                          / {selectedSection.wordTarget} target
                        </span>
                      )}
                    </span>
                  </div>
                  <textarea
                    value={selectedSection.content}
                    onChange={(e) =>
                      updateSection(selectedSection.id, {
                        content: e.target.value,
                        status: e.target.value ? "generated" : "outline",
                      })
                    }
                    rows={8}
                    placeholder="Generate Sections 1–3 or run full proposal to auto-fill, or write manually…"
                    className="proposal-draft-textarea zo-input w-full px-3 py-3 text-sm leading-[1.7] text-foreground outline-none transition-smooth focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10"
                  />
                </label>
                </div>

                <div className="proposal-section-agent-dock">
                  <SectionEditChat
                    rfpId={rfp.id}
                    section={selectedSection}
                    disabled={anyPipelineRunning}
                    docked
                    onSectionUpdated={(updatedDraft, updatedResearch) => {
                      skipNextSaveRef.current = true;
                      setOutline(updatedDraft);
                      if (updatedResearch) {
                        setResearch(updatedResearch);
                        if (updatedResearch.budget) {
                          setBudget(updatedResearch.budget);
                        }
                      }
                      void saveProposalDraft(rfp.id, updatedDraft);
                    }}
                  />
                </div>
              </>
            ) : (
              <div className="flex min-h-[16rem] flex-1 flex-col items-center justify-center p-6 text-center">
                <p className="text-sm text-zo-text-muted">
                  Select a section from the list to edit.
                </p>
              </div>
            )}
          </div>
        </div>
      </TabPanel>

      {/* Content tab */}
      <TabPanel id="content" activeTab={activeTab} className="proposal-workspace-tab">
        {outline.sections.some((s) => s.content.trim()) ? (
          <div className="proposal-content-scroll custom-scrollbar grid gap-0 lg:grid-cols-[minmax(0,1fr)_minmax(200px,260px)] lg:gap-4 lg:p-3">
            <div className="space-y-4 p-3 md:p-4 lg:px-2 lg:py-2">
              {outline.sections.map((section, index) =>
                section.content ? (
                  <article
                    key={section.id}
                    id={section.id}
                    className="proposal-content-article scroll-mt-28"
                  >
                    {/* Section card header */}
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-3">
                        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#ef5018] text-sm font-bold text-white shadow-[0_4px_12px_rgba(239,80,24,0.3)]">
                          {index + 1}
                        </span>
                        <div className="min-w-0">
                          <h3 className="text-[1.05rem] font-bold leading-tight tracking-tight text-foreground">
                            {section.title}
                          </h3>
                          <p className="mt-0.5 text-[11px] text-zo-text-muted">
                            {countWords(section.content).toLocaleString()} words
                            {section.pageLimit ? ` · ~${section.pageLimit} pages` : ""}
                          </p>
                        </div>
                      </div>
                      <SectionStatusPill status={section.status} />
                    </div>
                    {/* Divider */}
                    <div className="my-5 h-px bg-zo-border/60" />
                    {/* Section body: parsed markdown */}
                    <div className="proposal-prose">
                      <MarkdownReportBody body={section.content} variant="document" />
                    </div>
                  </article>
                ) : null
              )}
            </div>

            <nav className="proposal-on-page-nav hidden lg:block lg:rounded-2xl lg:border lg:border-zo-border/80">
              <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-zo-text-muted">
                On this page
              </p>
              <ul className="mt-4 space-y-0.5">
                {outline.sections
                  .filter((s) => s.content)
                  .map((section, index) => (
                    <li key={section.id}>
                      <a
                        href={`#${section.id}`}
                        className="proposal-on-page-link"
                        title={section.title}
                      >
                        <span className="proposal-on-page-num">{index + 1}</span>
                        <span className="proposal-on-page-title">{section.title}</span>
                      </a>
                    </li>
                  ))}
              </ul>
            </nav>
          </div>
        ) : (
          <div className="flex min-h-[360px] flex-col items-center justify-center px-8 py-16 text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-[#ef5018]/15 text-[#ef5018]">
              <svg className="h-7 w-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
              </svg>
            </div>
            <p className="font-heading mt-5 text-xl font-bold text-foreground">
              No content generated yet
            </p>
            <p className="mt-2 max-w-sm text-sm leading-relaxed text-zo-text-muted">
              Generate the full proposal (static Sections 1–3 plus RFP-specific
              sections) or run each phase separately.
            </p>
            <button
              type="button"
              onClick={handleGenerateFullProposal}
              disabled={isFullProposalRunning}
              className="zo-btn mt-6 disabled:opacity-60"
            >
              Generate Full Proposal
            </button>
          </div>
        )}
      </TabPanel>

      {/* Budget tab — Supermemory pricing KB, RFP-aware */}
      <TabPanel id="pricing" activeTab={activeTab} className="proposal-workspace-tab">
        <div className="proposal-tab-panel proposal-tab-panel-scroll">
          <ProposalBudgetPanel
            budget={budget}
            isRunning={isPricingRunning}
            error={pricingError}
            disabled={anyPipelineRunning}
            onGenerate={() => void handleGeneratePricing()}
          />
        </div>
      </TabPanel>

      <TabPanel id="review" activeTab={activeTab} className="proposal-workspace-tab">
        <ProposalReviewPanel
            review={presubmitReview}
            isRunning={isReviewRunning}
            isAutoFixing={isAutoFixing}
            autoFixMode={autoFixMode}
            error={reviewError}
            autoFixNotice={autoFixNotice}
            disabled={anyPipelineRunning}
            onRunReview={() => void handleRunReview()}
            onAutoFix={() => void handleAutoFix()}
            onStopAutoFix={handleStopAutoFix}
            onJumpToSection={handleJumpToSection}
          />
      </TabPanel>

      {/* Export tab */}
      <TabPanel id="export" activeTab={activeTab} className="proposal-workspace-tab">
        <div className="proposal-tab-panel proposal-tab-panel-scroll grid gap-5 md:grid-cols-2">
          <div>
            <h3 className="font-heading text-lg font-bold text-foreground">
              Export manuscript
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-zo-text-muted">
              Design-ready plain text for Curt and the layout team. Sections are
              separated with horizontal rules.
            </p>

            <div className="mt-6 space-y-3">
              <div className="zo-surface-panel flex items-center justify-between border border-zo-border px-4 py-3">
                <span className="text-sm text-zo-text-secondary">Words</span>
                <span className="font-semibold tabular-nums text-foreground">
                  {stats.totalWords.toLocaleString()}
                </span>
              </div>
              <div className="zo-surface-panel flex items-center justify-between border border-zo-border px-4 py-3">
                <span className="text-sm text-zo-text-secondary">
                  Est. pages
                </span>
                <span className="font-semibold tabular-nums text-foreground">
                  {stats.totalPages}
                </span>
              </div>
              <div className="zo-surface-panel flex items-center justify-between border border-zo-border px-4 py-3">
                <span className="text-sm text-zo-text-secondary">
                  Sections
                </span>
                <span className="font-semibold tabular-nums text-foreground">
                  {stats.generatedSections}
                </span>
              </div>
            </div>

            <div className="mt-6 flex flex-wrap gap-3">
              <button
                type="button"
                onClick={handleCopy}
                disabled={!fullManuscript}
                className="zo-btn !py-2.5 disabled:opacity-40"
              >
                {copied ? "Copied!" : "Copy manuscript"}
              </button>
              <a
                href={`data:text/plain;charset=utf-8,${encodeURIComponent(fullManuscript)}`}
                download={`${rfp.client.replace(/\s+/g, "-")}-proposal-draft.txt`}
                className={`zo-btn secondary !py-2.5 disabled:opacity-40 ${
                  fullManuscript ? "" : "pointer-events-none opacity-40"
                }`}
              >
                Download .txt
              </a>
            </div>
          </div>

          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-zo-text-muted">
              Preview
            </p>
            <textarea
              readOnly
              value={fullManuscript || "Generate proposal content first…"}
              rows={20}
              className="zo-input h-full min-h-[320px] w-full resize-none px-4 py-4 font-mono text-xs leading-relaxed"
            />
          </div>
        </div>
      </TabPanel>
      </div>
    </section>
  );
}
