"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  buildDefaultOutline,
  computeDraftStats,
  countWords,
  countSectionsWithContent,
  createCustomSection,
  isLikelyWipedOutline,
  rebuildOutlineFromResearch,
  staticSections1to3Complete,
} from "@/lib/proposal-draft";
import {
  findBudgetSection,
  mergeBudgetIntoOutline,
} from "@/lib/proposal-budget-content";
import {
  buildPipelineStatus,
  fetchProposalDraft,
  generateFullProposalStaged,
  generateProposalPricing,
  generateProposalSections1to3,
  pipelineResumeMessage,
  PIPELINE_PHASE_LABELS,
  recoverProposalDraftIfSaved,
  runPhase3Drafting,
  runPhase3_5BudgetWithRecovery,
  runPhase3_5BudgetReconcileWithRecovery,
  runPhase3_6SelfEditWithRecovery,
  runPhase4PreSubmitReview,
  runPhase4PreSubmitAutoFix,
  runPhase4FinalizeGaps,
  saveProposalDraft,
  startLiveDraftPolling,
  type FullProposalProgress,
  type ProposalPipelineStatus,
} from "@/lib/proposal-api";
import type { OutlineSection, ProposalBudget, ProposalOutline, ProposalResearch, PreSubmitReview } from "@/types/proposal";
import type { RfpRecord } from "@/types/rfp";
import { SectionStatusPill } from "./SectionStatusPill";
import { MarkdownReportBody } from "./MarkdownReportBody";
import { DraftSectionEditor, type SectionRevisionRecord } from "./DraftSectionEditor";
import { SectionRevisionCompare } from "./SectionRevisionCompare";
import { ProposalBudgetPanel } from "./ProposalBudgetPanel";
import { ProposalReviewPanel } from "./ProposalReviewPanel";
import { ProposalManualFlagsPanel } from "./ProposalManualFlagsPanel";
import { OutlineTabs, TabPanel } from "./ui/OutlineTabs";
import {
  scanSubmissionFlags,
  mergeSubmissionFlags,
  resolveFlagHighlight,
  sectionManualFillCount,
  summarizeManualFillFlags,
  type FlagHighlightRange,
  type ManualFillFlag,
} from "@/lib/proposal-manual-flags";
import { phaseIsComplete } from "@/lib/proposal-pipeline-checkpoint";

type WorkspaceTab = "outline" | "content" | "pricing" | "review" | "export";

type SectionRevisionMap = Record<string, SectionRevisionRecord>;

function revisionsStorageKey(rfpId: string): string {
  return `zo-proposal-section-revisions:${rfpId}`;
}

function loadStoredRevisions(rfpId: string): SectionRevisionMap {
  if (typeof window === "undefined") return {};
  try {
    const raw = sessionStorage.getItem(revisionsStorageKey(rfpId));
    if (!raw) return {};
    const parsed = JSON.parse(raw) as SectionRevisionMap;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function persistStoredRevisions(rfpId: string, revisions: SectionRevisionMap): void {
  if (typeof window === "undefined") return;
  try {
    if (Object.keys(revisions).length === 0) {
      sessionStorage.removeItem(revisionsStorageKey(rfpId));
      return;
    }
    sessionStorage.setItem(revisionsStorageKey(rfpId), JSON.stringify(revisions));
  } catch {
    // ignore quota errors
  }
}

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
  goRfpCount?: number;
  onOpenGoRfpPicker?: () => void;
}

export function ProposalDraftWorkspace({
  rfp,
  goRfpCount,
  onOpenGoRfpPicker,
}: ProposalDraftWorkspaceProps) {
  const [outline, setOutline] = useState<ProposalOutline>(() =>
    buildDefaultOutline(rfp)
  );
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("outline");
  const [selectedSectionId, setSelectedSectionId] = useState<string | null>(
    null
  );
  const [isFullProposalRunning, setIsFullProposalRunning] = useState(false);
  const [fullProposalProgress, setFullProposalProgress] =
    useState<FullProposalProgress | null>(null);
  const [liveGeneratedCount, setLiveGeneratedCount] = useState(0);
  const [liveLatestSectionTitle, setLiveLatestSectionTitle] = useState<string | null>(
    null
  );
  const [isPricingRunning, setIsPricingRunning] = useState(false);
  const [isRefiningBudget, setIsRefiningBudget] = useState(false);
  const [pricingError, setPricingError] = useState<string | null>(null);
  const [refineBudgetError, setRefineBudgetError] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [isReviewRunning, setIsReviewRunning] = useState(false);
  const [isAutoFixing, setIsAutoFixing] = useState(false);
  const [isFinalizingGaps, setIsFinalizingGaps] = useState(false);
  const [gapResolveNotice, setGapResolveNotice] = useState<string | null>(null);
  const [gapResolveError, setGapResolveError] = useState<string | null>(null);
  const [autoFixMode, setAutoFixMode] = useState<"quick" | "ai" | null>(null);
  const autoFixAbortRef = useRef<AbortController | null>(null);
  const [autoFixNotice, setAutoFixNotice] = useState<string | null>(null);
  const [presubmitReview, setPresubmitReview] = useState<PreSubmitReview | null>(null);
  const [showManualFlags, setShowManualFlags] = useState(false);
  const [highlightedSectionId, setHighlightedSectionId] = useState<string | null>(null);
  const [activeSubmissionFlag, setActiveSubmissionFlag] = useState<ManualFillFlag | null>(null);
  const [showSectionMeta, setShowSectionMeta] = useState(false);
  const [editorFocusMode, setEditorFocusMode] = useState(false);
  const [budget, setBudget] = useState<ProposalBudget | null>(null);
  const [research, setResearch] = useState<ProposalResearch | null>(null);
  const [newSectionTitle, setNewSectionTitle] = useState("");
  const [hydrated, setHydrated] = useState(false);
  const [copied, setCopied] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [generateNotice, setGenerateNotice] = useState<string | null>(null);
  const [provider, setProvider] = useState<string | null>(null);
  const [pipelineStatus, setPipelineStatus] =
    useState<ProposalPipelineStatus | null>(null);
  const skipNextSaveRef = useRef(false);
  const saveGenerationRef = useRef(0);
  const editorScrollRef = useRef<HTMLDivElement>(null);
  const sectionButtonRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const [sectionRevisions, setSectionRevisions] = useState<SectionRevisionMap>({});
  const [revisionDrawerSectionId, setRevisionDrawerSectionId] = useState<string | null>(
    null
  );

  const applyOutlineFromServer = useCallback((draft: ProposalOutline) => {
    saveGenerationRef.current += 1;
    skipNextSaveRef.current = true;
    setOutline(draft);
  }, []);

  const recordSectionRevision = useCallback(
    (sectionId: string, revision: SectionRevisionRecord) => {
      setSectionRevisions((prev) => {
        const next = { ...prev, [sectionId]: revision };
        persistStoredRevisions(rfp.id, next);
        return next;
      });
    },
    [rfp.id]
  );

  const dismissSectionRevision = useCallback(
    (sectionId: string) => {
      setSectionRevisions((prev) => {
        const next = { ...prev };
        delete next[sectionId];
        persistStoredRevisions(rfp.id, next);
        return next;
      });
      setRevisionDrawerSectionId((current) => (current === sectionId ? null : current));
    },
    [rfp.id]
  );

  useEffect(() => {
    setSectionRevisions(loadStoredRevisions(rfp.id));
    setRevisionDrawerSectionId(null);
  }, [rfp.id]);

  const activeRevision =
    revisionDrawerSectionId && sectionRevisions[revisionDrawerSectionId]
      ? sectionRevisions[revisionDrawerSectionId]
      : null;

  const revisionDrawerSection = revisionDrawerSectionId
    ? outline.sections.find((s) => s.id === revisionDrawerSectionId) ?? null
    : null;

  useEffect(() => {
    if (!selectedSectionId) return;
    sectionButtonRefs.current
      .get(selectedSectionId)
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    editorScrollRef.current?.scrollTo({ top: 0 });
  }, [selectedSectionId]);

  const selectSection = useCallback((id: string) => {
    setSelectedSectionId(id);
    setActiveSubmissionFlag((current) =>
      current && current.sectionId !== id ? null : current
    );
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

  const handleJumpToManualFlag = useCallback((flag: ManualFillFlag) => {
    setShowManualFlags(false);
    setHighlightedSectionId(flag.sectionId);
    setActiveSubmissionFlag(flag);
    setActiveTab("content");
    window.setTimeout(() => setHighlightedSectionId(null), 4000);
  }, []);

  const activeFlagHighlight = useMemo((): FlagHighlightRange | null => {
    if (!activeSubmissionFlag) return null;
    const section = outline.sections.find((s) => s.id === activeSubmissionFlag.sectionId);
    if (!section) return null;
    return resolveFlagHighlight(activeSubmissionFlag, section.content ?? "");
  }, [activeSubmissionFlag, outline.sections]);

  useEffect(() => {
    if (activeTab !== "content" || !activeSubmissionFlag) return;
    const sectionId = activeSubmissionFlag.sectionId;
    const frame = requestAnimationFrame(() => {
      document.getElementById(sectionId)?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    });
    return () => cancelAnimationFrame(frame);
  }, [activeTab, activeSubmissionFlag]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      let draft: ProposalOutline | null = null;
      let research: ProposalResearch | null = null;
      let providerName: string | null = null;
      let status: ProposalPipelineStatus | null = null;

      for (let attempt = 0; attempt < 3; attempt += 1) {
        const result = await fetchProposalDraft(rfp.id);
        if (cancelled) return;
        draft = result.draft;
        research = result.research;
        providerName = result.provider ?? null;
        status = result.pipelineStatus;
        if (draft || research) break;
        await new Promise((resolve) => setTimeout(resolve, 400 * (attempt + 1)));
      }

      if (!draft && !research) {
        const defaults = buildDefaultOutline(rfp);
        setOutline(defaults);
        setSelectedSectionId(defaults.sections[0]?.id ?? null);
        setHydrated(true);
        return;
      }

      setResearch(research);
      setBudget(research?.budget ?? null);
      setPresubmitReview(research?.presubmitReview ?? null);
      setProvider(providerName);
      setPipelineStatus(
        status ?? buildPipelineStatus(draft, research)
      );

      const contentSections = draft ? countSectionsWithContent(draft) : 0;
      const researchReady = (research?.rfpSections?.length ?? 0) > 0;

      saveGenerationRef.current += 1;
      skipNextSaveRef.current = true;

      if (draft && contentSections > 0) {
        setOutline(draft);
        setSelectedSectionId(draft.sections[0]?.id ?? null);
        setActiveTab("content");
      } else if (researchReady && research && isLikelyWipedOutline(draft ?? buildDefaultOutline(rfp), research)) {
        const rebuilt = rebuildOutlineFromResearch(rfp, research, draft);
        setOutline(rebuilt);
        setSelectedSectionId(rebuilt.sections[0]?.id ?? null);
        setActiveTab("outline");
        setGenerateNotice(
          "Section list restored from cached research — use Generate proposal to re-draft content."
        );
      } else if (draft) {
        setOutline(draft);
        setSelectedSectionId(draft.sections[0]?.id ?? null);
        setActiveTab(draft.sections.some((s) => s.content) ? "content" : "outline");
      } else {
        const defaults = buildDefaultOutline(rfp);
        setOutline(defaults);
        setSelectedSectionId(defaults.sections[0]?.id ?? null);
      }

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
    if (isLikelyWipedOutline(outline, research)) {
      return;
    }
    const generation = saveGenerationRef.current;
    const timer = setTimeout(() => {
      if (generation !== saveGenerationRef.current) return;
      void saveProposalDraft(rfp.id, outline);
    }, 800);
    return () => clearTimeout(timer);
  }, [outline, rfp.id, hydrated, research]);

  useEffect(() => {
    if (!hydrated) return;
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      void fetchProposalDraft(rfp.id).then((snap) => {
        if (snap.pipelineStatus) setPipelineStatus(snap.pipelineStatus);
        if (snap.research) setResearch(snap.research);
      });
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [rfp.id, hydrated]);

  const stats = useMemo(() => computeDraftStats(outline), [outline]);
  const manualFillFlags = useMemo(
    () =>
      mergeSubmissionFlags(
        scanSubmissionFlags(outline, {
          budget,
          rfpTitle: rfp.title,
          rfpClient: rfp.client,
          rfpSections: research?.rfpSections,
        }),
        presubmitReview?.manualFillFlags
      ),
    [outline, budget, rfp.title, rfp.client, research?.rfpSections, presubmitReview?.manualFillFlags]
  );
  const manualFillCount = manualFillFlags.length;
  const manualFillSummary = useMemo(
    () => summarizeManualFillFlags(manualFillFlags),
    [manualFillFlags]
  );
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
      baseWorkspaceTabs.map((tab) => {
        if (tab.id === "review" && reviewCriticalCount > 0) {
          return { ...tab, count: reviewCriticalCount };
        }
        if (tab.id === "content" && manualFillCount > 0) {
          return { ...tab, count: manualFillCount };
        }
        return tab;
      }),
    [reviewCriticalCount, manualFillCount]
  );

  const selectedSection = outline.sections.find(
    (s) => s.id === selectedSectionId
  );

  const sections1to3Done = useMemo(
    () => staticSections1to3Complete(outline),
    [outline]
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

  const selfEditDone = useMemo(
    () => phaseIsComplete(outline, research, "phase-3-6-self-edit"),
    [outline, research]
  );

  const fullProposalDone = phase3Done && selfEditDone;

  const manuscriptRecoveryNeeded = useMemo(
    () =>
      hydrated &&
      (research?.rfpSections?.length ?? 0) > 0 &&
      isLikelyWipedOutline(outline, research),
    [hydrated, outline, research]
  );

  const canResumePipeline =
    Boolean(pipelineStatus?.canResume) && !pipelineStatus?.isComplete;

  const anyPipelineRunning =
    isFullProposalRunning ||
    isPricingRunning ||
    isRefiningBudget ||
    isReviewRunning ||
    isAutoFixing ||
    isFinalizingGaps;

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

  const handleFinalizeGaps = useCallback(
    async (options?: { stayOnTab?: boolean }) => {
      setIsFinalizingGaps(true);
      setReviewError(null);
      setAutoFixNotice(null);
      setGapResolveError(null);
      setGapResolveNotice(null);
      try {
        const { review, research: updatedResearch, draft } =
          await runPhase4FinalizeGaps(rfp.id);
        setPresubmitReview(review);
        setResearch(updatedResearch);
        if (draft) {
          applyOutlineFromServer(draft);
          await saveProposalDraft(rfp.id, draft);
        }
        const flagCount = review.manualFillFlags?.length ?? 0;
        const beforeCount = manualFillCount;
        const notice =
          flagCount > 0
            ? `KB filled what it could — ${beforeCount} → ${flagCount} item(s) for Sonja/Ella.`
            : "KB resolved all submission gaps.";
        setAutoFixNotice(
          flagCount > 0
            ? `Final editor pass complete — ${flagCount} item(s) assigned to Sonja/Ella via MANUAL FILL tags. Open manual fill-ins on the Outline tab.`
            : "Final editor pass complete — KB resolved all submission gaps."
        );
        setGapResolveNotice(notice);
        if (!options?.stayOnTab) {
          setActiveTab("review");
        }
      } catch (error) {
        const message =
          error instanceof Error ? error.message : "Finalize gaps failed";
        setReviewError(message);
        setGapResolveError(message);
      } finally {
        setIsFinalizingGaps(false);
      }
    },
    [rfp.id, manualFillCount]
  );

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
      applyOutlineFromServer(draft);
      setPresubmitReview(review);
      setResearch(updatedResearch);
      await saveProposalDraft(rfp.id, draft);
      setAutoFixNotice(
          `Auto-fix: ${autoFix.issuesBefore} → ${autoFix.issuesAfter} findings · ` +
            `${autoFix.sectionsPatched} of ${autoFix.sectionsTargeted ?? autoFix.sectionsPatched} flagged section(s) updated. ` +
          (autoFix.stoppedReason === "cancelled"
            ? "Stopped — partial progress was saved."
            : autoFix.stoppedReason === "ready"
              ? "Re-check compliance items before upload."
                : autoFix.stoppedReason === "regressed"
                  ? "Some sections were skipped — auto-fix only applies changes that reduce findings."
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
          applyOutlineFromServer(recovered.draft);
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
      applyOutlineFromServer(mergedOutline);
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

  const handleRefineBudget = useCallback(async () => {
    if (!budget) {
      setRefineBudgetError("Generate a budget first, then run Budget refinery.");
      return;
    }
    setIsRefiningBudget(true);
    setRefineBudgetError(null);
    try {
      const { budget: refined, research: updatedResearch, draft } =
        await runPhase3_5BudgetReconcileWithRecovery(rfp.id);
      setBudget(refined);
      setResearch(updatedResearch);

      const mergedOutline = draft ?? mergeBudgetIntoOutline(outline, refined);
      applyOutlineFromServer(mergedOutline);
      await saveProposalDraft(rfp.id, mergedOutline);

      const budgetSection = findBudgetSection(mergedOutline.sections);
      if (budgetSection) {
        setSelectedSectionId(budgetSection.id);
        setActiveTab("content");
      } else {
        setActiveTab("pricing");
      }
    } catch (error) {
      setRefineBudgetError(
        error instanceof Error ? error.message : "Budget refinery failed"
      );
    } finally {
      setIsRefiningBudget(false);
    }
  }, [rfp.id, budget, outline]);

  const handleLiveDraftUpdate = useCallback((draft: ProposalOutline) => {
    applyOutlineFromServer(draft);
    setActiveTab("content");
    const withContent = draft.sections.filter((s) => s.content?.trim());
    setLiveGeneratedCount(withContent.length);
    const latest = withContent[withContent.length - 1];
    if (latest) {
      setLiveLatestSectionTitle(latest.title);
      setSelectedSectionId(latest.id);
    }
  }, [applyOutlineFromServer]);

  const handleGenerateFullProposal = useCallback(async () => {
    const canResume = pipelineStatus?.canResume ?? false;
    const resumePhase = pipelineStatus?.resumeFromPhase;
    const forwardOnlyResume =
      resumePhase === "phase-3-5-budget" || resumePhase === "phase-4-review";

    if (canResume && !fullProposalDone) {
      // Resume without confirmation when manuscript is incomplete.
    } else if (
      canResume &&
      fullProposalDone &&
      !forwardOnlyResume &&
      !confirm(
        `Resume pipeline from ${resumePhase ? PIPELINE_PHASE_LABELS[resumePhase] : "checkpoint"}? Completed phases will be skipped.`
      )
    ) {
      return;
    } else if (
      !canResume &&
      fullProposalDone &&
      !confirm(
        "Proposal already generated. Re-run full pipeline anyway? (Uses LLM tokens.)"
      )
    ) {
      return;
    }

    setIsFullProposalRunning(true);
    setFullProposalProgress(null);
    setLiveGeneratedCount(countSectionsWithContent(outline));
    setLiveLatestSectionTitle(null);
    setGenerateError(null);
    setGenerateNotice(null);
    try {
      const { draft, research: updatedResearch } =
        await generateFullProposalStaged(rfp.id, setFullProposalProgress, {
          startFrom: canResume ? pipelineStatus?.resumeFromPhase : undefined,
          forceRestart: !canResume && fullProposalDone,
          onDraftUpdate: handleLiveDraftUpdate,
        });
      applyOutlineFromServer(draft);
      if (updatedResearch) {
        setResearch(updatedResearch);
        setPipelineStatus(buildPipelineStatus(draft, updatedResearch));
        if (updatedResearch.budget) {
          setBudget(updatedResearch.budget);
        }
        if (updatedResearch.presubmitReview) {
          setPresubmitReview(updatedResearch.presubmitReview);
        }
      }
      await saveProposalDraft(rfp.id, draft);
      setActiveTab("content");
      setSelectedSectionId(
        draft.sections.find((s) => s.content)?.id ?? draft.sections[0]?.id ?? null
      );
      if (canResume) {
        setGenerateNotice("Pipeline resumed and completed from the last checkpoint.");
      }
    } catch (error) {
      setFullProposalProgress("recovering");
      const errMsg =
        error instanceof Error ? error.message : "Full proposal generation failed";
      const recovered = await recoverProposalDraftIfSaved(rfp.id, {
        minSectionsWithContent: 10,
      });
      if (recovered) {
        applyOutlineFromServer(recovered.draft);
        if (recovered.research) {
          setResearch(recovered.research);
          const status = buildPipelineStatus(recovered.draft, recovered.research);
          setPipelineStatus(status);
          if (recovered.research.budget) {
            setBudget(recovered.research.budget);
          }
          if (recovered.research.presubmitReview) {
            setPresubmitReview(recovered.research.presubmitReview);
          }
        }
        setActiveTab("content");
        setSelectedSectionId(
          recovered.draft.sections.find((s) => s.content)?.id ??
            recovered.draft.sections[0]?.id ??
            null
        );
        const status = buildPipelineStatus(
          recovered.draft,
          recovered.research
        );
        setGenerateNotice(
          `Step failed, but progress is saved. ${pipelineResumeMessage(status, { blocker: errMsg })}`
        );
        setGenerateError(null);
      } else {
        setGenerateError(errMsg);
      }
    } finally {
      setIsFullProposalRunning(false);
      setFullProposalProgress(null);
      setLiveLatestSectionTitle(null);
    }
  }, [rfp.id, fullProposalDone, pipelineStatus, outline, handleLiveDraftUpdate]);

  const handleResetOutline = async () => {
    if (
      !confirm(
        "Reset outline and clear ALL generated content?\n\nThis will permanently delete:\n• All generated sections (Sections 1–3, RFP sections)\n• All pipeline checkpoints from the database\n• Research cache and evidence corpus\n\nThis cannot be undone."
      )
    ) {
      return;
    }

    // 1. Hard-delete from DB (draft + checkpoint + research cache)
    try {
      await fetch(`/api/rfps/${rfp.id}/proposal/reset`, { method: "POST" });
    } catch {
      // Non-fatal — proceed with local reset anyway
    }

    // 2. Reset local state to defaults
    const defaults = buildDefaultOutline(rfp);
    saveGenerationRef.current += 1;
    skipNextSaveRef.current = true;
    setOutline(defaults);
    setSectionRevisions({});
    persistStoredRevisions(rfp.id, {});
    setRevisionDrawerSectionId(null);
    setSelectedSectionId(defaults.sections[0]?.id ?? null);
    setPresubmitReview(null);
    setResearch(null);
    setGenerateError(null);
    setFullProposalProgress(null);
    setLiveLatestSectionTitle(null);
  };


  const handleRecoverManuscript = useCallback(async () => {
    if (!research?.rfpSections?.length) {
      setGenerateError("No cached research to recover from. Run Generate Full Proposal.");
      return;
    }
    setIsFullProposalRunning(true);
    setFullProposalProgress("phase-3");
    setGenerateError(null);
    try {
      const rebuilt = rebuildOutlineFromResearch(rfp, research, outline);
      applyOutlineFromServer(rebuilt);
      await saveProposalDraft(rfp.id, rebuilt);

      setFullProposalProgress("sections-1-3");
      await generateProposalSections1to3(rfp.id);

      setFullProposalProgress("phase-3");
      const { draft: drafted, research: afterPhase3 } = await runPhase3Drafting(rfp.id);

      setFullProposalProgress("phase-3-6-self-edit");
      const { draft: polished, research: afterEdit } =
        await runPhase3_6SelfEditWithRecovery(rfp.id);

      setFullProposalProgress("phase-3-5-budget");
      const { draft, research: updatedResearch, budget } =
        await runPhase3_5BudgetWithRecovery(rfp.id);

      setFullProposalProgress("phase-4-review");
      const { research: reviewedResearch } = await runPhase4PreSubmitReview(rfp.id);

      const finalDraft = draft ?? polished ?? drafted;
      applyOutlineFromServer(finalDraft);
      setResearch(reviewedResearch ?? updatedResearch ?? afterEdit ?? afterPhase3);
      if (budget) setBudget(budget);
      setPresubmitReview(reviewedResearch.presubmitReview ?? null);
      await saveProposalDraft(rfp.id, finalDraft);
      setGenerateNotice("Manuscript re-drafted from cached KB research.");
      setActiveTab("content");
    } catch (error) {
      setGenerateError(
        error instanceof Error ? error.message : "Manuscript recovery failed"
      );
    } finally {
      setIsFullProposalRunning(false);
      setFullProposalProgress(null);
    }
  }, [research, rfp, outline]);

  const handleDraftSections1to3 = useCallback(async () => {
    setIsFullProposalRunning(true);
    setFullProposalProgress("sections-1-3");
    setGenerateError(null);
    setGenerateNotice(null);
    setLiveGeneratedCount(countSectionsWithContent(outline));
    setLiveLatestSectionTitle(null);
    const stopPolling = startLiveDraftPolling(rfp.id, handleLiveDraftUpdate);
    try {
      const draft = await generateProposalSections1to3(rfp.id);
      applyOutlineFromServer(draft);
      await saveProposalDraft(rfp.id, draft);
      setActiveTab("content");
      setSelectedSectionId(
        draft.sections.find((s) => s.content)?.id ?? draft.sections[0]?.id ?? null
      );
      setGenerateNotice("Sections 1–3 successfully drafted.");
    } catch (error) {
      setGenerateError(
        error instanceof Error ? error.message : "Sections 1–3 generation failed"
      );
    } finally {
      stopPolling();
      setIsFullProposalRunning(false);
      setFullProposalProgress(null);
      setLiveLatestSectionTitle(null);
    }
  }, [rfp.id, outline, applyOutlineFromServer, handleLiveDraftUpdate]);

  const handlePrimaryPipeline = useCallback(async () => {
    if (manuscriptRecoveryNeeded) {
      await handleRecoverManuscript();
      return;
    }
    await handleGenerateFullProposal();
  }, [manuscriptRecoveryNeeded, handleRecoverManuscript, handleGenerateFullProposal]);

  const primaryPipelineLabel = useMemo(() => {
    if (isFullProposalRunning) {
      const totalSections = outline.sections.length;
      const showLiveCount =
        liveGeneratedCount > 0 &&
        (fullProposalProgress === "sections-1-3" ||
          fullProposalProgress === "phase-3" ||
          fullProposalProgress === "phase-3-6-self-edit");
      if (showLiveCount) {
        const title = liveLatestSectionTitle
          ? ` — ${liveLatestSectionTitle.slice(0, 36)}${liveLatestSectionTitle.length > 36 ? "…" : ""}`
          : "";
        return `Section ${liveGeneratedCount}/${totalSections}${title}…`;
      }
      if (fullProposalProgress === "sections-1-3") return "Drafting sections 1–3…";
      if (fullProposalProgress === "phase-2") return "Researching RFP…";
      if (fullProposalProgress === "phase-3") return "Drafting sections…";
      if (fullProposalProgress === "phase-3-6-self-edit") {
        return manualFillCount > 0
          ? `Senior editor — ${manualFillCount} flags…`
          : "Senior editor polishing…";
      }
      if (fullProposalProgress === "phase-3-5-budget") return "Building budget…";
      if (fullProposalProgress === "phase-4-review") return "Pre-submit review…";
      if (fullProposalProgress === "recovering") return "Checking saved draft…";
      return "Working…";
    }
    if (canResumePipeline) return "Continue proposal";
    return "Generate proposal";
  }, [
    isFullProposalRunning,
    fullProposalProgress,
    canResumePipeline,
    liveGeneratedCount,
    liveLatestSectionTitle,
    manualFillCount,
    outline.sections.length,
  ]);

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
                {onOpenGoRfpPicker && goRfpCount ? (
                  <button
                    type="button"
                    onClick={onOpenGoRfpPicker}
                    className="proposal-go-picker-btn"
                  >
                    <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
                    </svg>
                    Go RFPs
                    <span className="proposal-go-picker-count">{goRfpCount}</span>
                  </button>
                ) : null}
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
                  {manualFillCount > 0 && (
                    <span className="rounded-full border border-red-200 bg-red-50 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-red-800">
                      {manualFillCount} manual fill-in
                      {manualFillCount === 1 ? "" : "s"}
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
            onClick={() => setShowManualFlags((open) => !open)}
            className={`zo-btn secondary ${
              manualFillCount > 0
                ? "!border-amber-300 !bg-amber-50 !text-amber-950"
                : ""
            }`}
            title="Regex scan: VERIFY tags, budget $0, reference contacts, hours table, PSA acks, MWBE consistency — no AI"
          >
            <svg
              className="h-4 w-4"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
              aria-hidden
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M3 3v1.5M3 21v-6m0 0l2.77-.693a9 9 0 016.208.682l.108.054a9 9 0 009.69-1.51M3 15l2.77-.693a9 9 0 016.208.682l.108.054a9 9 0 009.69-1.51M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
            Flag submission gaps
            {manualFillCount > 0 ? ` (${manualFillCount})` : ""}
          </button>
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
            onClick={() => void handleDraftSections1to3()}
            disabled={anyPipelineRunning}
            className="zo-btn secondary disabled:opacity-60"
            title="Draft Sections 1–3 from the knowledge base"
          >
            {isFullProposalRunning && fullProposalProgress === "sections-1-3" ? (
              <>
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-zo-teal/30 border-t-zo-teal" />
                Drafting sections 1–3…
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
                Draft Sections 1–3
              </>
            )}
          </button>
          <button
            type="button"
            onClick={() => void handlePrimaryPipeline()}
            disabled={anyPipelineRunning}
            className="zo-btn disabled:opacity-60"
            title={
              manuscriptRecoveryNeeded
                ? "Re-draft all sections from cached KB research"
                : canResumePipeline
                  ? pipelineResumeMessage(pipelineStatus!)
                  : "Sections 1–3, RFP drafting, budget, and pre-submit review"
            }
          >
            {isFullProposalRunning && fullProposalProgress !== "sections-1-3" ? (
              <>
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-zo-white/30 border-t-zo-white" />
                {primaryPipelineLabel}
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
                {primaryPipelineLabel}
              </>
            )}
          </button>
        </div>
      </div>

      <ProposalManualFlagsPanel
        open={showManualFlags}
        flags={manualFillFlags}
        summary={manualFillSummary}
        activeSectionId={
          activeSubmissionFlag?.sectionId ?? selectedSectionId ?? highlightedSectionId
        }
        onJumpToFlag={handleJumpToManualFlag}
        onClose={() => setShowManualFlags(false)}
        onResolveAll={() => void handleFinalizeGaps({ stayOnTab: true })}
        isResolving={isFinalizingGaps}
        resolveNotice={gapResolveNotice}
        resolveError={gapResolveError}
      />

      <div className="proposal-workspace-body">
      {/* Outline tab */}
      <TabPanel id="outline" activeTab={activeTab} className="proposal-workspace-tab">
          <div className={`proposal-outline-layout grid min-h-0 flex-1 gap-0 overflow-hidden lg:grid-cols-[200px_minmax(0,1fr)] lg:gap-2 lg:p-2 ${editorFocusMode ? "is-editor-focus" : ""}`}>
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
                const flagCount = sectionManualFillCount(section.id, manualFillFlags);
                const hasRevision = Boolean(sectionRevisions[section.id]);
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
                      } ${highlightedSectionId === section.id ? "is-flag-target" : ""}`}
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
                              onClick={(e) => {
                                e.stopPropagation();
                                selectSection(section.id);
                                setRevisionDrawerSectionId(section.id);
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
                <div className="proposal-editor-chrome">
                  <div className="flex items-center gap-2">
                    <span className="shrink-0 text-[10px] font-bold tabular-nums text-zo-text-muted">
                      §{outline.sections.findIndex((s) => s.id === selectedSection.id) + 1}
                    </span>
                    <input
                      type="text"
                      value={selectedSection.title}
                      onChange={(e) =>
                        updateSection(selectedSection.id, {
                          title: e.target.value,
                        })
                      }
                      className="proposal-editor-chrome-title min-w-0 flex-1"
                      aria-label="Section title"
                    />
                    <button
                      type="button"
                      onClick={() => setShowSectionMeta((open) => !open)}
                      className="proposal-editor-focus-toggle"
                      aria-expanded={showSectionMeta}
                    >
                      {showSectionMeta ? "Hide limits" : "Limits"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditorFocusMode((on) => !on)}
                      className={`proposal-editor-focus-toggle ${editorFocusMode ? "is-active" : ""}`}
                      title="Hide section list for a taller editor"
                    >
                      {editorFocusMode ? "Show list" : "Focus"}
                    </button>
                    <div className="flex shrink-0 gap-1">
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
                      {selectedSection.custom ? (
                        <IconButton
                          onClick={() => removeSection(selectedSection.id)}
                          label="Remove section"
                          variant="danger"
                        >
                          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                          </svg>
                        </IconButton>
                      ) : null}
                    </div>
                  </div>
                </div>

                {showSectionMeta ? (
                  <div className="proposal-editor-meta proposal-editor-meta-compact">
                    <div className="proposal-editor-meta-grid">
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
                          className="zo-input mt-1 w-full px-3 py-2 text-sm outline-none focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10"
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
                          className="zo-input mt-1 w-full px-3 py-2 text-sm outline-none focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10"
                        />
                      </label>
                      <div>
                        <span className="text-xs font-semibold text-zo-text-muted">
                          Source
                        </span>
                        <p className="mt-1.5 flex flex-wrap items-center gap-1.5">
                          <span className="rounded-lg bg-zo-warm-gray/60 px-2 py-0.5 text-[11px] font-semibold capitalize text-zo-text-secondary">
                            {selectedSection.source}
                          </span>
                          {selectedSection.required ? (
                            <span className="text-[11px] font-medium text-zo-teal">
                              Required
                            </span>
                          ) : null}
                        </p>
                      </div>
                    </div>
                  </div>
                ) : null}

                <div ref={editorScrollRef} className="proposal-editor-body">
                  <DraftSectionEditor
                    rfpId={rfp.id}
                    section={selectedSection}
                    wordCount={countWords(selectedSection.content)}
                    disabled={anyPipelineRunning}
                    value={selectedSection.content}
                    highlightRange={
                      activeSubmissionFlag?.sectionId === selectedSectionId
                        ? activeFlagHighlight
                        : null
                    }
                    onUserEditStart={() => setActiveSubmissionFlag(null)}
                    onChange={(content) =>
                      updateSection(selectedSection.id, {
                        content,
                        status: content ? "generated" : "outline",
                      })
                    }
                    onSectionUpdated={(updatedDraft, updatedResearch) => {
                      applyOutlineFromServer(updatedDraft);
                      if (updatedResearch) {
                        setResearch(updatedResearch);
                        if (updatedResearch.budget) {
                          setBudget(updatedResearch.budget);
                        }
                      }
                      void saveProposalDraft(rfp.id, updatedDraft);
                    }}
                    storedRevision={
                      selectedSection ? sectionRevisions[selectedSection.id] ?? null : null
                    }
                    revisionDrawerOpen={revisionDrawerSectionId === selectedSection?.id}
                    onRevisionRecorded={(revision) =>
                      recordSectionRevision(selectedSection.id, revision)
                    }
                    onRevisionDrawerOpenChange={(open) =>
                      setRevisionDrawerSectionId(open ? selectedSection.id : null)
                    }
                    onRevisionDismiss={() => dismissSectionRevision(selectedSection.id)}
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
          <div className="proposal-content-layout grid gap-0 lg:grid-cols-[minmax(0,1fr)_minmax(200px,260px)] lg:gap-4 lg:p-3">
            <div className="proposal-content-scroll custom-scrollbar space-y-4 p-3 md:p-4 lg:px-2 lg:py-2">
              {outline.sections.map((section, index) =>
                section.content ? (
                  <article
                    key={section.id}
                    id={section.id}
                    className={`proposal-content-article scroll-mt-32 ${
                      highlightedSectionId === section.id ? "is-flag-target" : ""
                    }`}
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
                            {sectionManualFillCount(section.id, manualFillFlags) > 0 ? (
                              <span className="ml-1 font-semibold text-amber-800">
                                · {sectionManualFillCount(section.id, manualFillFlags)} manual fill-in
                                {sectionManualFillCount(section.id, manualFillFlags) === 1 ? "" : "s"}
                              </span>
                            ) : null}
                          </p>
                        </div>
                      </div>
                      <SectionStatusPill status={section.status} />
                    </div>
                    {/* Divider */}
                    <div className="my-5 h-px bg-zo-border/60" />
                    {/* Section body: parsed markdown */}
                    <div className="proposal-prose">
                      <MarkdownReportBody
                        body={section.content}
                        variant="document"
                        highlightTexts={
                          activeSubmissionFlag?.sectionId === section.id && activeFlagHighlight
                            ? [activeFlagHighlight.text]
                            : []
                        }
                      />
                    </div>
                  </article>
                ) : null
              )}
            </div>

            <nav className="proposal-on-page-nav hidden lg:block lg:rounded-2xl lg:border lg:border-zo-border/80">
              <p className="shrink-0 text-[10px] font-bold uppercase tracking-[0.14em] text-zo-text-muted">
                On this page
              </p>
              <div className="proposal-on-page-nav-scroll custom-scrollbar">
                <ul className="space-y-0.5">
                  {outline.sections
                    .filter((s) => s.content)
                    .map((section, index) => (
                      <li key={section.id}>
                        <a
                          href={`#${section.id}`}
                          className="proposal-on-page-link"
                          title={section.title}
                          onClick={(event) => {
                            event.preventDefault();
                            document
                              .getElementById(section.id)
                              ?.scrollIntoView({ behavior: "smooth", block: "start" });
                          }}
                        >
                          <span className="proposal-on-page-num">{index + 1}</span>
                          <span className="proposal-on-page-title">{section.title}</span>
                        </a>
                      </li>
                    ))}
                </ul>
              </div>
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
              Draft Sections 1–3 from the knowledge base, or generate the full proposal (including RFP-specific sections).
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              <button
                type="button"
                onClick={handleDraftSections1to3}
                disabled={isFullProposalRunning}
                className="zo-btn disabled:opacity-60"
              >
                Draft Sections 1–3
              </button>
              <button
                type="button"
                onClick={handleGenerateFullProposal}
                disabled={isFullProposalRunning}
                className="zo-btn secondary disabled:opacity-60"
              >
                Generate Full Proposal
              </button>
            </div>
          </div>
        )}
      </TabPanel>

      {/* Budget tab — Supermemory pricing KB, RFP-aware */}
      <TabPanel id="pricing" activeTab={activeTab} className="proposal-workspace-tab">
        <div className="proposal-tab-panel proposal-tab-panel-scroll">
          <ProposalBudgetPanel
            budget={budget}
            isRunning={isPricingRunning}
            isRefining={isRefiningBudget}
            error={pricingError}
            refineError={refineBudgetError}
            disabled={anyPipelineRunning}
            onGenerate={() => void handleGeneratePricing()}
            onRefine={() => void handleRefineBudget()}
          />
        </div>
      </TabPanel>

      <TabPanel id="review" activeTab={activeTab} className="proposal-workspace-tab">
        <ProposalReviewPanel
            review={presubmitReview}
            rfpClient={rfp.client}
            rfpTitle={rfp.title}
            isRunning={isReviewRunning}
            isAutoFixing={isAutoFixing}
            isFinalizingGaps={isFinalizingGaps}
            autoFixMode={autoFixMode}
            error={reviewError}
            autoFixNotice={autoFixNotice}
            disabled={anyPipelineRunning}
            onRunReview={() => void handleRunReview()}
            onAutoFix={() => void handleAutoFix()}
            onFinalizeGaps={() => void handleFinalizeGaps()}
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

      {typeof document !== "undefined" && activeRevision && revisionDrawerSectionId
        ? createPortal(
            <>
              <button
                type="button"
                className="proposal-revision-drawer-backdrop"
                aria-label="Close revision summary"
                onClick={() => setRevisionDrawerSectionId(null)}
              />
              <div
                className="proposal-revision-drawer"
                role="dialog"
                aria-labelledby="proposal-revision-drawer-title"
              >
                {revisionDrawerSection ? (
                  <p
                    id="proposal-revision-drawer-title"
                    className="shrink-0 border-b border-zo-border/70 px-4 py-2.5 text-sm font-semibold text-foreground"
                  >
                    {revisionDrawerSection.title}
                  </p>
                ) : null}
                <SectionRevisionCompare
                  before={activeRevision.before}
                  after={activeRevision.after}
                  summary={activeRevision.summary}
                  instruction={activeRevision.instruction}
                  onDismiss={() => dismissSectionRevision(revisionDrawerSectionId)}
                />
              </div>
            </>,
            document.body
          )
        : null}
    </section>
  );
}
