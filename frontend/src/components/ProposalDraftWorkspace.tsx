"use client";

// Proposal Draft Workspace - Key Personas Enabled

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  buildDefaultOutline,
  countWords,
  countSectionsWithContent,
  createCustomSection,
  isLikelyWipedOutline,
  rebuildOutlineFromResearch,
  staticSections1to3Complete,
  stripLegacyMonolithSections,
} from "@/lib/proposal-draft";
import { getManuscriptSections, normalizeOutlineSectionOrder, resolveManuscriptJumpTarget, buildManuscriptIndexMap } from "@/lib/proposal-outline-tree";
import {
  isManuscriptSectionDrafted,
  isSectionDrafted,
  stripLeadingTitleEcho,
} from "@/lib/proposal-section-health";
import { createMarkdownSourceMap } from "@/lib/markdown-source-map";
import { repairReferenceSectionsInOutline } from "@/lib/reference-table-repair";
import { toggleWrapMarkers } from "@/lib/markdown-inline-format";
import { buildScanRfpSummary, type ScanRfpFulfillReport, type ScanRfpSummary } from "@/lib/proposal-scan-report";
import { ScanRfpSummaryBanner } from "@/components/ScanRfpSummaryBanner";
import { QueuedJobBanner } from "@/components/QueuedJobBanner";
import {
  buildPipelineStatus,
  fetchProposalDraft,
  generateFullProposalStaged,
  generateProposalSections1to3,
  pipelineResumeMessage,
  PROPOSAL_INITIAL_LOAD_TIMEOUT_MS,
  recoverProposalDraftIfSaved,
  resetProposal,
  restartProposalFromIntelligence,
  restartProposalFromCaseStudies,
  runPhase3Drafting,
  runPhase3_5BudgetWithRecovery,
  runPhase3_6SelfEditWithRecovery,
  runDesignerCompactManuscript,
  runPhase4PreSubmitReview,
  runPhase4FinalizeGaps,
  runFulfillRfpGaps,
  runAlignRfpOutline,
  previewAlignRfpOutline,
  runPacketRedistribute,
  previewPacketRedistribute,
  pollAlignRfpOutlineCompletion,
  restoreProposalSnapshot,
  stopProposalGeneration,
  downloadProposalDocx,
  saveProposalDraft,
  startLiveDraftPolling,
  fullProposalProgressFromInFlight,
  getProposalJobStatus,
  pollFulfillScanCompletion,
  matchCaseStudiesForRfp,
  type CaseStudyMatchResult,
  type FullProposalProgress,
  type ProposalPipelineStatus,
} from "@/lib/proposal-api";
import { getLlmCostForRfp, type LlmCostRfpBreakdown } from "@/lib/llm-cost-service";
import type { OutlineSection, ProposalBudget, ProposalOutline, ProposalResearch, PreSubmitReview } from "@/types/proposal";
import type { RfpRecord } from "@/types/rfp";
import { ProposalSectionTree, reorderSectionsById } from "./ProposalSectionTree";
import { MatchRfpPacketControl } from "./MatchRfpPacketControl";
import { CapabilityHoverTip } from "./CapabilityHoverTip";
import { ManuscriptSelectionBubble } from "./ManuscriptSelectionBubble";
import {
  capabilityById,
  formatDoesDoesntBlock,
} from "@/lib/proposal-tool-guide";
import {
  PacketPlaceReportBanner,
  type PacketPlaceReport,
} from "./PacketPlaceReportBanner";
import { PacketPlacePreviewModal } from "./PacketPlacePreviewModal";
import { AlignOutlinePreviewModal } from "./AlignOutlinePreviewModal";
import type { PacketPlacePreview } from "@/lib/proposal-api";
import type { AlignOutlinePreview } from "@/lib/proposal-api";
import { SectionStatusPill } from "./SectionStatusPill";
import { MarkdownReportBody, stripManuscriptDisplayArtifacts } from "./MarkdownReportBody";
import { DraftSectionEditor, type SectionRevisionRecord } from "./DraftSectionEditor";
import {
  ProposalSectionChatPanel,
  buildSectionPinReference,
  type SectionChatMessage,
  type SectionChatReference,
} from "./ProposalSectionChatPanel";
import { SectionRevisionCompare } from "./SectionRevisionCompare";
import { ProposalManualFlagsPanel } from "./ProposalManualFlagsPanel";
import { ProposalReviewToolbar } from "./ProposalReviewToolbar";
import { ProposalWorkflowRail } from "./ProposalWorkflowRail";
import { ProposalVersionCompare } from "./ProposalVersionCompare";
import { KeyPersonasBox } from "./KeyPersonasBox";
import { KeyPersonasModal } from "./KeyPersonasModal";
import { CaseStudyMatchModal } from "./CaseStudyMatchModal";
import { ProposalTabMoreMenu } from "./ProposalTabMoreMenu";
import { OutlineTabs, TabPanel } from "./ui/OutlineTabs";
import {
  ConfirmDialogProvider,
  useConfirmDialog,
} from "./ConfirmDialog";
import {
  scanSubmissionFlags,
  mergeSubmissionFlags,
  actionableSubmissionFlags,
  resolveFlagHighlight,
  sectionManualFillCount,
  summarizeManualFillFlags,
  type FlagHighlightRange,
  type ManualFillFlag,
} from "@/lib/proposal-manual-flags";
import {
  FULFILL_SCAN_PHASE,
  FULFILL_SCAN_STEP_LABELS,
  ALIGN_RFP_OUTLINE_PHASE,
  PACKET_REDISTRIBUTE_PHASE,
  isBuildPipelineComplete,
  pipelineServerStillWorkingMessage,
  inProgressPhaseLabel,
  type PipelineInProgressPhase,
} from "@/lib/proposal-pipeline-checkpoint";

type WorkspaceTab = "outline" | "content" | "export";

function prepareOutline(draft: ProposalOutline): ProposalOutline {
  const cleaned: ProposalOutline = {
    ...draft,
    sections: draft.sections.map((s) => ({
      ...s,
      content: s.content
        ? stripManuscriptDisplayArtifacts(s.content)
        : s.content,
    })),
  };
  const ordered = normalizeOutlineSectionOrder(
    stripLegacyMonolithSections(cleaned),
  ) as ProposalOutline;
  return repairReferenceSectionsInOutline(ordered);
}

type SectionRevisionMap = Record<string, SectionRevisionRecord>;

function revisionsStorageKey(rfpId: string): string {
  return `zo-proposal-section-revisions:${rfpId}`;
}

function buildBannerDismissKey(rfpId: string): string {
  return `proposal-build-banner-dismissed:${rfpId}`;
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
  { id: "outline", label: "Build" },
  { id: "content", label: "Review" },
  { id: "export", label: "Download" },
];

function getProposalPlainStatus(options: {
  fullProposalDone: boolean;
  manuscriptComplete: boolean;
  manualFillCount: number;
  reviewCriticalCount: number;
  readyToSubmit: boolean;
  hasEndingReport: boolean;
  isGenerating: boolean;
}): { headline: string; tone: "neutral" | "action" | "good" } {
  if (options.isGenerating) {
    return { headline: "Writing your proposal…", tone: "neutral" };
  }
  if (!options.fullProposalDone && !options.manuscriptComplete) {
    return {
      headline: "Not started — click Build my proposal to build the draft.",
      tone: "action",
    };
  }
  if (options.manualFillCount > 0) {
    return {
      headline: `${options.manualFillCount} item${options.manualFillCount === 1 ? "" : "s"} need your input — see Checklist on Review.`,
      tone: "action",
    };
  }
  if (options.reviewCriticalCount > 0) {
    return {
      headline: `${options.reviewCriticalCount} issue${options.reviewCriticalCount === 1 ? "" : "s"} to review before submit.`,
      tone: "action",
    };
  }
  if (options.readyToSubmit) {
    return { headline: "Ready to export and submit.", tone: "good" };
  }
  if (options.hasEndingReport) {
    return {
      headline: "Draft complete — open Submit for the final checklist.",
      tone: "neutral",
    };
  }
  return { headline: "Draft complete — read Review, then Submit.", tone: "good" };
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

export function ProposalDraftWorkspace(props: ProposalDraftWorkspaceProps) {
  return (
    <ConfirmDialogProvider>
      <ProposalDraftWorkspaceInner {...props} />
    </ConfirmDialogProvider>
  );
}

function ProposalDraftWorkspaceInner({
  rfp,
  goRfpCount,
  onOpenGoRfpPicker,
}: ProposalDraftWorkspaceProps) {
  const confirm = useConfirmDialog();
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
  const [isFinalizingGaps, setIsFinalizingGaps] = useState(false);
  const [isFulfillingRfpGaps, setIsFulfillingRfpGaps] = useState(false);
  const [isAligningRfpOutline, setIsAligningRfpOutline] = useState(false);
  const [isPlacingPacketContent, setIsPlacingPacketContent] = useState(false);
  const [placeReport, setPlaceReport] = useState<PacketPlaceReport | null>(null);
  const [placePreviewOpen, setPlacePreviewOpen] = useState(false);
  const [placePreviewLoading, setPlacePreviewLoading] = useState(false);
  const [placePreviewError, setPlacePreviewError] = useState<string | null>(null);
  const [placePreview, setPlacePreview] = useState<PacketPlacePreview | null>(
    null
  );
  const [alignPreviewOpen, setAlignPreviewOpen] = useState(false);
  const [alignPreviewLoading, setAlignPreviewLoading] = useState(false);
  const [alignPreviewError, setAlignPreviewError] = useState<string | null>(null);
  const [alignPreview, setAlignPreview] = useState<AlignOutlinePreview | null>(
    null
  );
  // True from the moment Stop is clicked until the backend + Celery task have
  // actually stopped (job no longer in-flight). Keeps a "Stopping…" state up so
  // the user knows the request is in progress, not instantly done.
  const [isStopping, setIsStopping] = useState(false);
  const [isRestoringSnapshot, setIsRestoringSnapshot] = useState(false);
  const [restoreSnapshotAt, setRestoreSnapshotAt] = useState("");
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false);
  const [isResettingDraft, setIsResettingDraft] = useState(false);
  const [isDesignerCompacting, setIsDesignerCompacting] = useState(false);
  const [isMatchingCaseStudies, setIsMatchingCaseStudies] = useState(false);
  const [caseStudyMatchOpen, setCaseStudyMatchOpen] = useState(false);
  const [caseStudyMatchResult, setCaseStudyMatchResult] =
    useState<CaseStudyMatchResult | null>(null);
  const [caseStudyMatchError, setCaseStudyMatchError] = useState<string | null>(null);
  const [gapResolveNotice, setGapResolveNotice] = useState<string | null>(null);
  const [gapResolveError, setGapResolveError] = useState<string | null>(null);
  const [presubmitReview, setPresubmitReview] = useState<PreSubmitReview | null>(null);
  const [showManualFlags, setShowManualFlags] = useState(false);
  const [highlightedSectionId, setHighlightedSectionId] = useState<string | null>(null);
  const [lastSavedAt, setLastSavedAt] = useState<number | null>(null);
  const [reviewSectionQuery, setReviewSectionQuery] = useState("");
  const [reviewFocusMode, setReviewFocusMode] = useState(false);
  const activeSectionTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const compareDetailsRef = useRef<HTMLDetailsElement | null>(null);
  const [activeSubmissionFlag, setActiveSubmissionFlag] = useState<ManualFillFlag | null>(null);
  const [budget, setBudget] = useState<ProposalBudget | null>(null);
  const [research, setResearch] = useState<ProposalResearch | null>(null);
  const [newSectionTitle, setNewSectionTitle] = useState("");
  const [hydrated, setHydrated] = useState(false);
  const [draftLoadState, setDraftLoadState] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [isDownloadingDocx, setIsDownloadingDocx] = useState(false);
  const [docxDownloadError, setDocxDownloadError] = useState<string | null>(null);
  const [docxDownloaded, setDocxDownloaded] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [generateNotice, setGenerateNotice] = useState<string | null>(null);
  const [scanSummary, setScanSummary] = useState<ScanRfpSummary | null>(null);
  const [scanSummaryExpanded, setScanSummaryExpanded] = useState(false);
  // True right after a Complete & clean run finishes successfully, so the UI can
  // confirm completion and disable the button until the draft actually changes
  // (avoids a needless re-run). Cleared on any edit / new scan / stop.
  const [fulfillJustCompleted, setFulfillJustCompleted] = useState(false);
  // Lets the user dismiss the server-derived "finished successfully" banner
  // (the one that shows after a refresh). Reset when a new scan starts.
  const [completionBannerDismissed, setCompletionBannerDismissed] = useState(false);
  const [buildBannerDismissed, setBuildBannerDismissed] = useState(false);
  const [provider, setProvider] = useState<string | null>(null);
  const [pipelineStatus, setPipelineStatus] =
    useState<ProposalPipelineStatus | null>(null);
  const [rfpCost, setRfpCost] = useState<LlmCostRfpBreakdown | null>(null);
  const rfpCostRef = useRef<LlmCostRfpBreakdown | null>(null);
  rfpCostRef.current = rfpCost;
  const skipNextSaveRef = useRef(false);
  const saveGenerationRef = useRef(0);
  const fullProposalAbortRef = useRef<AbortController | null>(null);
  /** Sync lock — React state lags a tick; prevents a second click aborting the first run. */
  const fullProposalInFlightRef = useRef(false);
  const fulfillAbortRef = useRef<AbortController | null>(null);
  /** Prevents double green-banner when request race + poll both see completion. */
  const fulfillCompletionShownRef = useRef(false);
  /** True once this run's live poll saw inProgressPhase === fulfill-scan. */
  const fulfillSawRunningRef = useRef(false);
  const editorScrollRef = useRef<HTMLDivElement>(null);
  const sectionButtonRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const contentScrollRef = useRef<HTMLDivElement | null>(null);
  const submitScrollRef = useRef<HTMLDivElement | null>(null);
  const liveContentFingerprintRef = useRef<Map<string, number>>(new Map());
  const outlineRef = useRef(outline);
  useEffect(() => {
    outlineRef.current = outline;
  }, [outline]);
  const [sectionRevisions, setSectionRevisions] = useState<SectionRevisionMap>({});
  const [revisionDrawerSectionId, setRevisionDrawerSectionId] = useState<string | null>(
    null
  );
  const [sectionChatReference, setSectionChatReference] = useState<SectionChatReference | null>(
    null
  );
  const [sectionChatBusy, setSectionChatBusy] = useState(false);
  const [sectionChatMessages, setSectionChatMessages] = useState<SectionChatMessage[]>([]);
  const [showKeyPersonas, setShowKeyPersonas] = useState(true);
  const assistantPaneRef = useRef<HTMLDivElement>(null);

  const handleKeyPersonasChange = useCallback((selectedPersonaIds: string[]) => {
    skipNextSaveRef.current = true;
    setOutline((prev) => ({
      ...prev,
      selectedKeyPersonas: selectedPersonaIds,
    }));
  }, []);

  // Key personas are a precondition for generation: the proposal names and
  // staffs these people, and a draft built without them has to be regenerated
  // rather than edited. The gate holds the requested action and runs it once a
  // selection exists, so the user is not sent back to find the button again.
  const [personaGateOpen, setPersonaGateOpen] = useState(false);
  const pendingGenerateRef = useRef<null | (() => void)>(null);

  const selectedPersonaIds = useMemo(
    () => outline.selectedKeyPersonas || [],
    [outline.selectedKeyPersonas]
  );
  const hasKeyPersonas = selectedPersonaIds.length > 0;

  const fmtUsd = useCallback((value: number) => {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 3,
    }).format(value);
  }, []);

  const costByRunType = useMemo(() => {
    const runs = rfpCost?.byRun ?? [];
    const totals = {
      generate: 0,
      completeScan: 0,
      chat: 0,
    };
    for (const run of runs) {
      if (run.runType === "generate_proposal") totals.generate += run.costUsd;
      else if (run.runType === "complete_scan") totals.completeScan += run.costUsd;
      else if (run.runType === "chat") totals.chat += run.costUsd;
    }
    return totals;
  }, [rfpCost]);

  useEffect(() => {
    if (!hydrated || draftLoadState !== "ready") return;

    let cancelled = false;
    let timer: number | null = null;
    const pipelineInFlight = Boolean(research?.pipelineCheckpoint?.inProgressPhase);
    const running =
      isFullProposalRunning || isFulfillingRfpGaps || pipelineInFlight;

    const load = async () => {
      const next = await getLlmCostForRfp(rfp.id);
      if (cancelled || !next) return;
      setRfpCost(next);
    };

    void load();
    if (running) {
      timer = window.setInterval(() => void load(), 4000);
    }

    return () => {
      cancelled = true;
      if (timer !== null) window.clearInterval(timer);
    };
  }, [
    rfp.id,
    hydrated,
    draftLoadState,
    isFullProposalRunning,
    isFulfillingRfpGaps,
    research?.pipelineCheckpoint?.inProgressPhase,
  ]);

  const requireKeyPersonas = useCallback(
    (run: () => void) => {
      if (selectedPersonaIds.length > 0) {
        run();
        return;
      }
      pendingGenerateRef.current = run;
      setPersonaGateOpen(true);
    },
    [selectedPersonaIds]
  );

  const closePersonaGate = useCallback(() => {
    setPersonaGateOpen(false);
    const pending = pendingGenerateRef.current;
    pendingGenerateRef.current = null;
    // Only continue when a selection was actually made — dismissing the modal
    // must not start a run the gate exists to prevent.
    if (pending && (outline.selectedKeyPersonas || []).length > 0) {
      pending();
    }
  }, [outline.selectedKeyPersonas]);

  const openSectionChat = useCallback((request?: SectionChatReference | null) => {
    if (request) {
      setSectionChatReference(request);
      window.setTimeout(() => {
        assistantPaneRef.current?.scrollIntoView({
          behavior: "smooth",
          block: "nearest",
        });
      }, 40);
    } else {
      setSectionChatReference(null);
    }
  }, []);

  const applyOutlineFromServer = useCallback((draft: ProposalOutline) => {
    saveGenerationRef.current += 1;
    skipNextSaveRef.current = true;
    setOutline(prepareOutline(draft));
  }, []);

  const handlePersonasDraftSynced = useCallback(
    (draft: ProposalOutline) => {
      applyOutlineFromServer(draft);
      setGenerateNotice("Team Bios updated to match Key Personas.");
    },
    [applyOutlineFromServer]
  );

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

  const applySectionImproveFromServer = useCallback(
    (updatedDraft: ProposalOutline, updatedResearch: ProposalResearch | null) => {
      // Backend already persisted the improved manuscript + After snapshot.
      // Do not PUT again — a slim client save can race and drop chat content.
      applyOutlineFromServer(updatedDraft);
      if (updatedResearch) {
        setResearch(updatedResearch);
        if (updatedResearch.budget) {
          setBudget(updatedResearch.budget);
        }
      }
      const authoritativeIds = new Set(
        updatedDraft.sections.map((section) => section.id)
      );
      const authoritativeCount = updatedDraft.sections.length;
      const improvedById = new Map(
        updatedDraft.sections.map((section) => [section.id, section] as const)
      );
      void fetchProposalDraft(rfp.id).then((snap) => {
        if (!snap.draft) return;
        const snapIds = new Set(snap.draft.sections.map((section) => section.id));
        const missing = [...authoritativeIds].filter((id) => !snapIds.has(id));
        // Stale autosave can wipe sections the improve just added — push back.
        if (
          missing.length > 0 ||
          snap.draft.sections.length < authoritativeCount
        ) {
          const repaired: ProposalOutline = {
            ...updatedDraft,
            updatedAt: new Date().toISOString(),
          };
          applyOutlineFromServer(repaired);
          void saveProposalDraft(rfp.id, repaired);
          return;
        }
        // In-flight autosave can also overwrite the improve save with pre-edit
        // bodies (e.g. VERIFY tags). Prefer the improve response for any section
        // whose content still disagrees with what chat just wrote.
        let contentRegressed = false;
        const mergedSections = snap.draft.sections.map((section) => {
          const improved = improvedById.get(section.id);
          if (!improved) return section;
          if ((improved.content || "") === (section.content || "")) {
            return section;
          }
          contentRegressed = true;
          return {
            ...section,
            ...improved,
            content: improved.content,
            status: improved.status ?? section.status,
          };
        });
        if (contentRegressed) {
          const repaired: ProposalOutline = {
            ...snap.draft,
            ...updatedDraft,
            sections: mergedSections,
            updatedAt: new Date().toISOString(),
          };
          applyOutlineFromServer(repaired);
          void saveProposalDraft(rfp.id, repaired);
          return;
        }
        applyOutlineFromServer(snap.draft);
        if (snap.research) {
          setResearch(snap.research);
          if (snap.research.budget) {
            setBudget(snap.research.budget);
          }
        }
      });
    },
    [applyOutlineFromServer, rfp.id]
  );

  useEffect(() => {
    const revisions = loadStoredRevisions(rfp.id);
    queueMicrotask(() => {
      setSectionRevisions(revisions);
      setRevisionDrawerSectionId(null);
    });
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
    // Drop a stale Improve/Revise pin from another tab so chat follows the
    // section the user just opened (not leftover Client References).
    setSectionChatReference((current) =>
      current && current.sectionId !== id ? null : current
    );
    // Always land in the editor for the clicked section — including empty ones.
    setActiveTab("outline");
  }, []);

  const scrollToManuscriptSection = useCallback((requestedId: string) => {
    const targetId = resolveManuscriptJumpTarget(outline.sections, requestedId);
    setActiveSubmissionFlag((current) =>
      current && current.sectionId !== targetId ? null : current
    );
    setHighlightedSectionId(targetId);
    setSelectedSectionId(targetId);

    const scroller =
      activeTab === "export"
        ? submitScrollRef.current
        : contentScrollRef.current;
    const target = document.getElementById(targetId);
    if (!target) {
      // Empty sections may not be mounted on Submit — open Sections editor instead.
      setActiveTab("outline");
      window.setTimeout(() => setHighlightedSectionId(null), 2200);
      return;
    }
    window.setTimeout(() => setHighlightedSectionId(null), 2200);
    const scrollableScroller =
      scroller && scroller.scrollHeight > scroller.clientHeight + 2;
    if (scrollableScroller && scroller) {
      const sRect = scroller.getBoundingClientRect();
      const tRect = target.getBoundingClientRect();
      scroller.scrollTo({
        top: scroller.scrollTop + (tRect.top - sRect.top) - 20,
        behavior: "smooth",
      });
      return;
    }
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [outline.sections, activeTab]);

  const handleJumpToManualFlag = useCallback(
    (flag: ManualFillFlag) => {
      setShowManualFlags(false);
      setHighlightedSectionId(flag.sectionId);
      setActiveSubmissionFlag(flag);
      setActiveTab("content");
      window.setTimeout(() => setHighlightedSectionId(null), 4000);
      window.requestAnimationFrame(() => {
        scrollToManuscriptSection(flag.sectionId);
      });
    },
    [scrollToManuscriptSection]
  );

  const activeFlagHighlight = useMemo((): FlagHighlightRange | null => {
    if (!activeSubmissionFlag) return null;
    const section = outline.sections.find((s) => s.id === activeSubmissionFlag.sectionId);
    if (!section) return null;
    return resolveFlagHighlight(activeSubmissionFlag, section.content ?? "");
  }, [activeSubmissionFlag, outline.sections]);

  const manuscriptSections = useMemo(
    () => getManuscriptSections(outline.sections),
    [outline.sections]
  );

  const manuscriptIndexById = useMemo(
    () => buildManuscriptIndexMap(outline.sections),
    [outline.sections]
  );

  const reviewSections = useMemo(() => {
    const q = reviewSectionQuery.trim().toLowerCase();
    if (!q) return outline.sections;
    return outline.sections.filter((s) => s.title.toLowerCase().includes(q));
  }, [outline.sections, reviewSectionQuery]);

  const manuscriptProgress = useMemo(() => {
    const total = manuscriptSections.length;
    // Count only genuinely drafted sections. A failed section holds a short
    // [VERIFY: ...] stub, so a bare trim() check reported "16/16 drafted" while
    // three sections held no draft — contradicting the pipeline, which correctly
    // refuses to mark Phase 3 complete for them.
    const complete = manuscriptSections.filter((s) =>
      isManuscriptSectionDrafted(s)
    ).length;
    return { complete, total };
  }, [manuscriptSections]);

  useEffect(() => {
    if (activeTab !== "content" && activeTab !== "export") return;
    const pane =
      activeTab === "export" ? submitScrollRef.current : contentScrollRef.current;
    if (!pane) return;
    const layout = pane.closest(".proposal-content-layout");
    if (!layout) return;

    const onWheel = (raw: Event) => {
      if (!(raw instanceof WheelEvent)) return;
      const event = raw;
      const target = event.target;
      if (!(target instanceof Node)) return;
      const nav = layout.querySelector(".proposal-on-page-nav");
      if (nav?.contains(target)) return;
      if (!pane.contains(target)) return;

      const { scrollHeight, clientHeight } = pane;
      if (scrollHeight <= clientHeight + 1) return;

      event.preventDefault();
      pane.scrollTop += event.deltaY;
    };

    layout.addEventListener("wheel", onWheel, { passive: false });
    return () => layout.removeEventListener("wheel", onWheel);
  }, [activeTab, manuscriptSections.length]);

  useEffect(() => {
    let cancelled = false;

    const defaults = buildDefaultOutline(rfp);
    setOutline(defaults);
    setSelectedSectionId(defaults.sections[0]?.id ?? null);
    setHydrated(true);
    setDraftLoadState("loading");
    setGenerateError(null);

    async function load() {
      let draft: ProposalOutline | null = null;
      let research: ProposalResearch | null = null;
      let providerName: string | null = null;
      let status: ProposalPipelineStatus | null = null;

      try {
        for (let attempt = 0; attempt < 2; attempt += 1) {
          const result = await fetchProposalDraft(rfp.id, {
            timeoutMs: PROPOSAL_INITIAL_LOAD_TIMEOUT_MS,
          });
          if (cancelled) return;
          draft = result.draft;
          research = result.research;
          providerName = result.provider ?? null;
          status = result.pipelineStatus;
          if (draft || research) break;
          await new Promise((resolve) => setTimeout(resolve, 400 * (attempt + 1)));
        }
      } catch (error) {
        if (cancelled) return;
        setDraftLoadState("error");
        setGenerateError(
          error instanceof Error
            ? error.message
            : "Could not load proposal from server."
        );
        return;
      }

      if (cancelled) return;

      setDraftLoadState("ready");

      if (!draft && !research) {
        return;
      }

      setResearch(research);
      setBudget(research?.budget ?? null);
      setPresubmitReview(research?.presubmitReview ?? null);
      setProvider(providerName);
      setPipelineStatus(
        buildPipelineStatus(draft, research, status)
      );

      const inFlightPhase = research?.pipelineCheckpoint?.inProgressPhase;
      if (inFlightPhase) {
        setGenerateNotice(pipelineServerStillWorkingMessage(inFlightPhase));
      }

      const contentSections = draft ? countSectionsWithContent(draft) : 0;
      const researchReady = (research?.rfpSections?.length ?? 0) > 0;
      const recoverableSnap = [...(draft?.snapshots ?? [])]
        .reverse()
        .find((s) => (s.sectionCount ?? s.sections?.length ?? 0) > 0);

      saveGenerationRef.current += 1;
      skipNextSaveRef.current = true;

      // Live draft wiped but snapshot still in Supabase — restore automatically.
      if (contentSections === 0 && recoverableSnap?.savedAt) {
        try {
          const restored = await restoreProposalSnapshot(
            rfp.id,
            recoverableSnap.savedAt
          );
          if (cancelled) return;
          const prepared = prepareOutline(restored);
          setOutline(prepared);
          setSelectedSectionId(
            prepared.sections.find((s) => s.content)?.id ??
              prepared.sections[0]?.id ??
              null
          );
          setActiveTab("content");
          setPipelineStatus(
            buildPipelineStatus(prepared, research, status)
          );
          setGenerateNotice(
            `Recovered manuscript from saved version (“${recoverableSnap.label}”) — live draft had been emptied by a bad autosave.`
          );
          return;
        } catch {
          // Fall through to empty / rebuild paths below.
        }
      }

      if (draft && contentSections > 0) {
        const prepared = prepareOutline(draft);
        setOutline(prepared);
        setSelectedSectionId(prepared.sections[0]?.id ?? null);
        setActiveTab("content");
        const lastScan =
          prepared.lastFulfillReport ?? draft.lastFulfillReport ?? null;
        if (lastScan && typeof lastScan === "object") {
          setScanSummary(
            buildScanRfpSummary(lastScan as ScanRfpFulfillReport)
          );
          setScanSummaryExpanded(false);
        }
      } else if (researchReady && research && isLikelyWipedOutline(draft ?? buildDefaultOutline(rfp), research)) {
        const rebuilt = prepareOutline(
          rebuildOutlineFromResearch(rfp, research, draft)
        );
        setOutline(rebuilt);
        setSelectedSectionId(rebuilt.sections[0]?.id ?? null);
        setActiveTab("outline");
        setGenerateNotice(
          recoverableSnap
            ? `Live draft is empty — use Sections → saved version menu (“${recoverableSnap.label}”) to restore your manuscript.`
            : "Section list restored from cached research — use Build my proposal to re-draft content."
        );
      } else if (draft) {
        const prepared = prepareOutline(draft);
        setOutline(prepared);
        setSelectedSectionId(prepared.sections[0]?.id ?? null);
        setActiveTab(prepared.sections.some((s) => s.content) ? "content" : "outline");
        if (contentSections === 0 && recoverableSnap) {
          setGenerateNotice(
            `Live draft is empty — use Sections → saved version menu (“${recoverableSnap.label}”) to restore your manuscript.`
          );
        }
      } else {
        const defaults = buildDefaultOutline(rfp);
        setOutline(defaults);
        setSelectedSectionId(defaults.sections[0]?.id ?? null);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [rfp]);

  useEffect(() => {
    try {
      setBuildBannerDismissed(
        sessionStorage.getItem(buildBannerDismissKey(rfp.id)) === "1"
      );
    } catch {
      setBuildBannerDismissed(false);
    }
  }, [rfp.id]);

  /** Keep trying to sync while the backend is busy with generation. */
  useEffect(() => {
    if (!hydrated) return;
    if (draftLoadState !== "loading" && draftLoadState !== "error") return;

    let cancelled = false;
    const retry = async () => {
      try {
        const result = await fetchProposalDraft(rfp.id, {
          timeoutMs: PROPOSAL_INITIAL_LOAD_TIMEOUT_MS,
        });
        if (cancelled) return;
        if (!result.draft && !result.research) return;
        setDraftLoadState("ready");
        setGenerateError(null);
        setResearch(result.research);
        setBudget(result.research?.budget ?? null);
        setPresubmitReview(result.research?.presubmitReview ?? null);
        setProvider(result.provider ?? null);
        if (result.research) {
          setPipelineStatus(
            buildPipelineStatus(result.draft, result.research, result.pipelineStatus)
          );
          const inFlight = result.research.pipelineCheckpoint?.inProgressPhase;
          if (inFlight) {
            setGenerateNotice(pipelineServerStillWorkingMessage(inFlight));
            setActiveTab("content");
          }
        }
        saveGenerationRef.current += 1;
        skipNextSaveRef.current = true;
        if (result.draft) {
          const prepared = prepareOutline(result.draft);
          setOutline(prepared);
          setSelectedSectionId(
            prepared.sections.find((s) => s.content)?.id ??
              prepared.sections[0]?.id ??
              null
          );
          if (countSectionsWithContent(prepared) > 0) {
            setActiveTab("content");
          }
        }
      } catch {
        // Keep banner / error until a later retry succeeds.
      }
    };

    const timer = setInterval(() => {
      void retry();
    }, 12_000);
    void retry();

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [hydrated, draftLoadState, rfp.id]);

  useEffect(() => {
    if (!hydrated) return;
    // Never autosave the empty default shell while the initial GET is still in flight —
    // that race was wiping full Supabase manuscripts (snapshots survived, live draft did not).
    if (draftLoadState !== "ready") return;
    if (
      isFullProposalRunning ||
      isFulfillingRfpGaps ||
      isAligningRfpOutline ||
      isPlacingPacketContent ||
      isRestoringSnapshot
    )
      return; // never overwrite backend partials mid-generation / scan / restore
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
      void saveProposalDraft(rfp.id, outline).then(() => {
        if (generation === saveGenerationRef.current) setLastSavedAt(Date.now());
      });
    }, 800);
    return () => clearTimeout(timer);
  }, [
    outline,
    rfp.id,
    hydrated,
    research,
    isFullProposalRunning,
    isFulfillingRfpGaps,
    isAligningRfpOutline,
    isPlacingPacketContent,
    isRestoringSnapshot,
    draftLoadState,
  ]);

  useEffect(() => {
    if (!hydrated) return;
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      void fetchProposalDraft(rfp.id).then((snap) => {
        // Always sync — including null after Reset — so stale research/checkpoint
        // cannot resurrect "Continue proposal" on an empty outline.
        setPipelineStatus(snap.pipelineStatus);
        setResearch(snap.research);
        if (snap.research?.budget) setBudget(snap.research.budget);
        if (!snap.research) {
          setBudget(null);
          setPresubmitReview(null);
        }
        // Keep Key Personas badge honest with the server (Clear / Reset / other tab).
        if (snap.draft) {
          const serverIds = snap.draft.selectedKeyPersonas ?? [];
          setOutline((prev) => {
            const localIds = prev.selectedKeyPersonas ?? [];
            if (
              serverIds.length === localIds.length &&
              serverIds.every((id, i) => id === localIds[i])
            ) {
              return prev;
            }
            return { ...prev, selectedKeyPersonas: serverIds };
          });
        }
      });
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [rfp.id, hydrated]);

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
  const actionableFlags = useMemo(
    () => actionableSubmissionFlags(manualFillFlags),
    [manualFillFlags]
  );
  const manualFillCount = actionableFlags.length;
  const manualFillSummary = useMemo(
    () => summarizeManualFillFlags(actionableFlags),
    [actionableFlags]
  );
  const sectionProgress = Math.round(
    manuscriptProgress.total > 0
      ? (manuscriptProgress.complete / manuscriptProgress.total) * 100
      : 0
  );

  const reviewCriticalCount =
    presubmitReview?.issues.filter((i) => i.severity === "critical").length ?? 0;

  const workspaceTabs = useMemo(
    () =>
      baseWorkspaceTabs.map((tab) => {
        if (tab.id === "content" && manualFillCount > 0) {
          return { ...tab, count: manualFillCount };
        }
        return tab;
      }),
    [manualFillCount]
  );

  const selectedSection = outline.sections.find(
    (s) => s.id === selectedSectionId
  );

  const assistantViewSectionId =
    selectedSectionId ??
    manuscriptSections[0]?.id ??
    outline.sections[0]?.id ??
    "";

  // The toolbar acts on whichever manuscript textarea last had focus/selection —
  // not just the section highlighted in the SECTIONS list — so formatting works
  // no matter where in the document the user actually clicked or selected text.
  const [focusedSectionId, setFocusedSectionId] = useState<string | null>(null);
  // Raw-markdown editing is now OPT-IN: clicking a section only selects it and
  // shows the formatted render — the raw `#`/`|` source appears only for the one
  // section the user explicitly puts into edit mode via the "Edit source" button.
  const [editingSectionId, setEditingSectionId] = useState<string | null>(null);
  const [reviewPreviewSelection, setReviewPreviewSelection] = useState<{
    start: number;
    end: number;
  } | null>(null);
  const sectionTextareaRefs = useRef<Map<string, HTMLTextAreaElement>>(new Map());

  const activeReviewSectionId = focusedSectionId ?? assistantViewSectionId;

  const activeReviewSection = useMemo(
    () => outline.sections.find((s) => s.id === activeReviewSectionId) ?? null,
    [outline.sections, activeReviewSectionId]
  );

  const activeReviewMarkdown = useMemo(() => {
    if (!activeReviewSection) return "";
    return stripLeadingTitleEcho(
      activeReviewSection.content,
      activeReviewSection.title
    );
  }, [activeReviewSection]);

  const ensureReviewEditable = useCallback(() => {
    if (!activeReviewSectionId) return;
    setEditingSectionId(activeReviewSectionId);
  }, [activeReviewSectionId]);

  const captureReviewPreviewSelection = useCallback(() => {
    if (!activeReviewSection) return;
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) return;
    const text = sel.toString().replace(/\u00a0/g, " ");
    if (text.trim().length < 1) return;
    const source = stripLeadingTitleEcho(
      activeReviewSection.content,
      activeReviewSection.title
    );
    const range = createMarkdownSourceMap(source).find(text);
    if (!range) return;
    setReviewPreviewSelection(range);
  }, [activeReviewSection]);

  const resizeManuscriptTextarea = useCallback((el: HTMLTextAreaElement | null) => {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, []);

  const registerManuscriptTextarea = useCallback(
    (sectionId: string, el: HTMLTextAreaElement | null) => {
      if (el) {
        sectionTextareaRefs.current.set(sectionId, el);
        resizeManuscriptTextarea(el);
        // Only one manuscript textarea ever exists at a time (whichever section
        // is active) — keep the toolbar's target in sync as soon as it mounts,
        // not only once the user explicitly focuses it.
        activeSectionTextareaRef.current = el;
      } else {
        const prev = sectionTextareaRefs.current.get(sectionId);
        sectionTextareaRefs.current.delete(sectionId);
        if (prev && activeSectionTextareaRef.current === prev) {
          activeSectionTextareaRef.current = null;
        }
      }
    },
    [resizeManuscriptTextarea]
  );

  const handleManuscriptTextareaFocus = (
    sectionId: string,
    e: React.FocusEvent<HTMLTextAreaElement>
  ) => {
    activeSectionTextareaRef.current = e.currentTarget;
    setFocusedSectionId(sectionId);
  };

  const handleReviewComment = useCallback(
    (selectedText: string | null) => {
      if (!activeReviewSection) return;
      selectSection(activeReviewSection.id);
      openSectionChat(
        buildSectionPinReference(activeReviewSection, selectedText ?? activeReviewSection.content)
      );
    },
    [activeReviewSection, selectSection, openSectionChat]
  );

  useEffect(() => {
    setReviewPreviewSelection(null);
  }, [activeReviewSectionId]);

  // After "Edit source" (or a toolbar format that opens it), focus the textarea.
  useEffect(() => {
    if (!editingSectionId) return;
    const el = sectionTextareaRefs.current.get(editingSectionId);
    if (el && document.activeElement !== el) {
      el.focus();
      if (reviewPreviewSelection) {
        const s = Math.min(reviewPreviewSelection.start, el.value.length);
        const e = Math.min(reviewPreviewSelection.end, el.value.length);
        if (e > s) el.setSelectionRange(s, e);
      }
    }
  }, [editingSectionId, reviewPreviewSelection]);

  useEffect(() => {
    if (!focusedSectionId) return;
    const el = sectionTextareaRefs.current.get(focusedSectionId);
    if (el && document.activeElement !== el) el.focus();
  }, [focusedSectionId]);

  const sections1to3Done = useMemo(
    () =>
      pipelineStatus?.completedPhases?.includes("sections-1-3") ??
      staticSections1to3Complete(outline),
    [outline, pipelineStatus]
  );

  const phase2Done = useMemo(
    () =>
      pipelineStatus?.completedPhases?.includes("phase-2") ??
      (research?.proposalExecutionPlan?.validation?.readinessStatus === "ready" ||
        (research?.evidenceCorpus?.length ?? 0) > 0),
    [pipelineStatus, research]
  );

  const phase3Done = useMemo(
    () =>
      pipelineStatus?.completedPhases?.includes("phase-3") ??
      (sections1to3Done &&
        phase2Done &&
        outline.sections.some(
          (section) =>
            section.source === "rfp" && section.content.trim().length > 0
        )),
    [outline.sections, phase2Done, pipelineStatus, sections1to3Done]
  );

  const selfEditDone = useMemo(
    () =>
      Boolean(
        pipelineStatus?.completedPhases?.includes("phase-3-6-self-edit")
      ),
    [pipelineStatus]
  );

  const buildPipelineComplete = useMemo(
    () => isBuildPipelineComplete(pipelineStatus, research),
    [pipelineStatus, research]
  );

  const fullProposalDone =
    buildPipelineComplete ||
    (pipelineStatus?.isComplete ?? (phase3Done && selfEditDone));

  const plainStatus = useMemo(
    () =>
      getProposalPlainStatus({
        fullProposalDone,
        manuscriptComplete:
          manuscriptProgress.total > 0 &&
          manuscriptProgress.complete === manuscriptProgress.total,
        manualFillCount,
        reviewCriticalCount,
        readyToSubmit: Boolean(research?.endingReport?.readyToSubmit),
        hasEndingReport: Boolean(research?.endingReport),
        isGenerating: isFullProposalRunning,
      }),
    [
      fullProposalDone,
      manuscriptProgress,
      manualFillCount,
      reviewCriticalCount,
      research?.endingReport,
      isFullProposalRunning,
    ]
  );

  const manuscriptRecoveryNeeded = useMemo(
    () =>
      hydrated &&
      (research?.rfpSections?.length ?? 0) > 0 &&
      isLikelyWipedOutline(outline, research),
    [hydrated, outline, research]
  );

  // Resume only when the manuscript itself has content. An empty post-Reset
  // shell (0 words) is always a fresh Generate — leftover research/checkpoint
  // must not keep showing "Continue proposal".
  const canResumePipeline =
    countSectionsWithContent(outline) > 0 &&
    Boolean(pipelineStatus?.canResume) &&
    !pipelineStatus?.isComplete &&
    !buildPipelineComplete;

  const fulfillResumeStep = research?.pipelineCheckpoint?.resumeFulfillStep ?? null;
  const canResumeFulfillScan =
    Boolean(fulfillResumeStep && fulfillResumeStep > 1) &&
    countSectionsWithContent(outline) > 0;
  const fulfillResumeLabel =
    canResumeFulfillScan &&
    fulfillResumeStep &&
    fulfillResumeStep <= FULFILL_SCAN_STEP_LABELS.length
      ? FULFILL_SCAN_STEP_LABELS[fulfillResumeStep - 1]
      : null;

  const serverPipelineActive = Boolean(
    research?.pipelineCheckpoint?.inProgressPhase
  );

  const effectiveFullProposalProgress = useMemo((): FullProposalProgress | null => {
    if (!isFullProposalRunning && !serverPipelineActive) return null;
    return (
      fullProposalProgress ??
      fullProposalProgressFromInFlight(
        research?.pipelineCheckpoint?.inProgressPhase
      )
    );
  }, [
    isFullProposalRunning,
    serverPipelineActive,
    fullProposalProgress,
    research?.pipelineCheckpoint?.inProgressPhase,
  ]);

  const anyPipelineRunning =
    isFullProposalRunning ||
    isPricingRunning ||
    isRefiningBudget ||
    isFinalizingGaps ||
    isFulfillingRfpGaps ||
    isAligningRfpOutline ||
    isPlacingPacketContent ||
    serverPipelineActive;

  const handleFinalizeGaps = useCallback(
    async (options?: { stayOnTab?: boolean }) => {
      setIsFinalizingGaps(true);
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
        setGapResolveNotice(notice);
        if (!options?.stayOnTab) {
          setActiveTab("content");
        }
      } catch (error) {
        const message =
          error instanceof Error ? error.message : "Finalize gaps failed";
        setGapResolveError(message);
      } finally {
        setIsFinalizingGaps(false);
      }
    },
    [rfp.id, manualFillCount, applyOutlineFromServer]
  );

  const handleRestoreSnapshot = useCallback(
    async (savedAtOverride?: string) => {
      const savedAt = savedAtOverride ?? restoreSnapshotAt;
      if (!savedAt) return false;
      const label =
        outline.snapshots?.find((s) => s.savedAt === savedAt)?.label ??
        "saved version";
      const restoreOk = await confirm({
        title: `Restore “${label}”?`,
        description:
          "This replaces the FULL live proposal with that saved checkpoint " +
          "(section order and wording).\n\n" +
          "Your current live draft is kept as “Live draft (before restore)” " +
          "in Saved versions so you can undo this restore.\n\n" +
          "Tip: pick a version in the dropdown first, then click Restore.",
        confirmLabel: "Restore checkpoint",
        tone: "default",
      });
      if (!restoreOk) {
        return false;
      }
      setIsRestoringSnapshot(true);
      setGapResolveError(null);
      const beforeIds = outline.sections.map((s) => s.id);
      try {
        // Server already persists the restore — do not PUT a slimmed client
        // copy afterward (that raced autosave and hollowed checkpoint bodies).
        const restored = await restoreProposalSnapshot(rfp.id, savedAt);
        applyOutlineFromServer(restored);
        setRestoreSnapshotAt(savedAt);

        const afterIds = restored.sections.map((s) => s.id);
        const orderChanged =
          beforeIds.length !== afterIds.length ||
          beforeIds.some((id, i) => id !== afterIds[i]);
        const beforeById = new Map(
          outline.sections.map((s) => [s.id, (s.content || "").trim()] as const)
        );
        const contentChanged = restored.sections.find((s) => {
          const prev = beforeById.get(s.id) ?? "";
          return prev !== (s.content || "").trim();
        });
        const focus =
          contentChanged ??
          restored.sections.find((s) => (s.content || "").trim()) ??
          restored.sections[0];
        if (focus) {
          setSelectedSectionId(focus.id);
        }

        const filled = restored.sections.filter((s) =>
          Boolean(s.content?.trim())
        ).length;
        const orderNote = orderChanged
          ? " Section order was restored too — check the Outline tab."
          : "";
        setGapResolveNotice(
          `Restored “${label}” (${filled} sections with content).${orderNote}`
        );
        setGenerateNotice(
          `Restored “${label}”. Current draft was saved as “Live draft (before restore)”.`
        );
        setActiveTab("outline");
        return true;
      } catch (error) {
        setGapResolveError(
          error instanceof Error ? error.message : "Restore failed"
        );
        return false;
      } finally {
        setIsRestoringSnapshot(false);
      }
    },
    [
      confirm,
      restoreSnapshotAt,
      outline.snapshots,
      outline.sections,
      rfp.id,
      applyOutlineFromServer,
    ]
  );

  const handleSnapshotDropdownChange = useCallback((savedAt: string) => {
    // Selecting only picks the checkpoint for Restore + Compare.
    // Never auto-load on change — that felt broken and easy to misfire.
    if (!savedAt) return;
    setRestoreSnapshotAt(savedAt);
  }, []);

  useEffect(() => {
    const snaps = outline.snapshots ?? [];
    if (!snaps.length) {
      setRestoreSnapshotAt("");
      return;
    }
    if (snaps.some((s) => s.savedAt === restoreSnapshotAt)) {
      return;
    }
    // Prefer undo points from structure jobs, then chat saves, then newest.
    const preferred =
      [...snaps]
        .reverse()
        .find((s) =>
          /before align to rfp|before scan rfp|before structure change/i.test(
            s.label ?? ""
          )
        ) ??
      [...snaps]
        .reverse()
        .find((s) => /saved after chat|after improving/i.test(s.label ?? "")) ??
      snaps[snaps.length - 1]!;
    setRestoreSnapshotAt(preferred.savedAt);
  }, [outline.snapshots, restoreSnapshotAt]);

  const selectedSnapshotForCompare = useMemo(
    () =>
      outline.snapshots?.find((s) => s.savedAt === restoreSnapshotAt) ?? null,
    [outline.snapshots, restoreSnapshotAt]
  );

  const handleCompareJumpToSection = useCallback(
    (sectionId: string) => {
      setActiveTab("content");
      setSelectedSectionId(sectionId);
      window.setTimeout(() => {
        scrollToManuscriptSection(sectionId);
      }, 80);
    },
    [scrollToManuscriptSection]
  );

  const handleOpenLastResults = useCallback(() => {
    if (!outline.lastFulfillReport) return;
    setScanSummary(buildScanRfpSummary(outline.lastFulfillReport as ScanRfpFulfillReport));
    setScanSummaryExpanded(true);
    window.requestAnimationFrame(() => {
      document.querySelector(".proposal-scan-v2")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }, [outline.lastFulfillReport]);

  const handleOpenCompareToSaved = useCallback(() => {
    const details = compareDetailsRef.current;
    if (!details) return;
    details.open = true;
    details.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, []);

  const handleLiveDraftUpdate = useCallback((draft: ProposalOutline) => {
    setHydrated(true);
    applyOutlineFromServer(draft);
    // Do not force Review tab on each poll — user may be on Sections or Submit while generating.
    const withContent = draft.sections.filter((s) => s.content?.trim());
    setLiveGeneratedCount(withContent.length);

    // Section 1 must be readable first. While any 1.x subsection is still empty,
    // keep focus on the newest Section 1 subsection instead of jumping ahead.
    const section1Ids = draft.sections
      .filter((s) => s.id.startsWith("section-1-"))
      .map((s) => s.id);
    const section1Complete =
      section1Ids.length > 0 &&
      section1Ids.every((id) =>
        draft.sections.find((s) => s.id === id)?.content?.trim()
      );

    const newestInGroup = (prefix: string) =>
      [...withContent].reverse().find((s) => s.id.startsWith(prefix));

    const fingerprints = new Map(
      draft.sections.map((s) => [s.id, (s.content || "").length] as const)
    );
    const prev = liveContentFingerprintRef.current;

    // Currently writing = content grew since last poll (not merely "last completed").
    let growing: OutlineSection | undefined;
    for (const section of draft.sections) {
      const len = fingerprints.get(section.id) ?? 0;
      const prevLen = prev.get(section.id) ?? 0;
      if (len > prevLen) {
        growing = section;
      }
    }

    // Frontier = first empty section in order (what's next / about to fill).
    const frontier = draft.sections.find((s) => !(s.content || "").trim());

    liveContentFingerprintRef.current = fingerprints;

    const nonSection1 = withContent.filter((s) => !s.id.startsWith("section-1-"));
    const latestComplete = !section1Complete
      ? newestInGroup("section-1-") ?? withContent[withContent.length - 1]
      : newestInGroup("section-3-") ??
        newestInGroup("section-2-") ??
        nonSection1[nonSection1.length - 1] ??
        withContent[withContent.length - 1];

    // Prefer in-flight / next empty so the button does not keep naming a finished
    // section (e.g. 3.2 Oregon) while the next case study is drafting.
    const focus = !section1Complete
      ? growing ?? newestInGroup("section-1-") ?? frontier ?? latestComplete
      : growing ?? frontier ?? latestComplete;

    // Progress chip: only name a section that is still writing or next up —
    // never linger on a completed title like "3.2 — Oregon…".
    const progressTitle = growing?.title ?? frontier?.title ?? null;
    setLiveLatestSectionTitle(progressTitle);
    if (focus) {
      setSelectedSectionId(focus.id);
    }
  }, [applyOutlineFromServer]);

  const handleResearchPoll = useCallback((updated: ProposalResearch | null) => {
    if (!updated) return;
    setResearch(updated);
    if (updated.pipelineCheckpoint?.inProgressPhase === FULFILL_SCAN_PHASE) {
      fulfillSawRunningRef.current = true;
    }
    // Keep checkpoint + Complete & clean completion stamps live so the green
    // banner can appear as soon as Celery finishes — no manual refresh.
    setPipelineStatus((prev) =>
      buildPipelineStatus(outlineRef.current, updated, prev)
    );
  }, []);

  /**
   * Flip the UI to the green "finished successfully" state and pull the final
   * draft — used by the launch handler, reconnect poll, and live checkpoint
   * watcher so the user never needs a manual refresh.
   */
  const applyFulfillScanSuccessUi = useCallback(
    async (options?: {
      draft?: ProposalOutline | null;
      research?: ProposalResearch | null;
      fulfillReport?: Record<string, unknown> | null;
      skipFetch?: boolean;
    }) => {
      if (fulfillCompletionShownRef.current) {
        setIsFulfillingRfpGaps(false);
        return;
      }
      fulfillCompletionShownRef.current = true;

      let draft = options?.draft ?? null;
      let updatedResearch = options?.research ?? null;
      let report =
        options?.fulfillReport ??
        (draft?.lastFulfillReport as Record<string, unknown> | undefined) ??
        null;

      if (!options?.skipFetch) {
        try {
          const snapshot = await fetchProposalDraft(rfp.id);
          if (snapshot.draft) draft = snapshot.draft;
          if (snapshot.research) updatedResearch = snapshot.research;
          if (snapshot.draft?.lastFulfillReport) {
            report = snapshot.draft.lastFulfillReport as Record<string, unknown>;
          }
          if (snapshot.research) {
            setPipelineStatus(
              buildPipelineStatus(
                snapshot.draft,
                snapshot.research,
                snapshot.pipelineStatus
              )
            );
          }
        } catch {
          // Non-fatal — still show success from whatever we already have.
        }
      }

      if (draft) {
        applyOutlineFromServer({
          ...draft,
          lastFulfillReport: report ?? draft.lastFulfillReport,
        });
      }
      if (updatedResearch) {
        setResearch(updatedResearch);
        setPipelineStatus((prev) =>
          buildPipelineStatus(draft ?? outlineRef.current, updatedResearch, prev)
        );
        if (updatedResearch.budget) setBudget(updatedResearch.budget);
        if (updatedResearch.presubmitReview) {
          setPresubmitReview(updatedResearch.presubmitReview);
        }
      }
      if (report) {
        setScanSummary(buildScanRfpSummary(report as ScanRfpFulfillReport));
        setScanSummaryExpanded(false);
      }
      setGapResolveNotice("Saved version available (Before Review & fix).");
      setFulfillJustCompleted(true);
      setCompletionBannerDismissed(false);
      setGenerateNotice(
        "Review & fix finished successfully — this draft is up to date. The button stays disabled until you edit the draft again."
      );
      setGenerateError(null);
      setIsFulfillingRfpGaps(false);
      setActiveTab("content");
      window.requestAnimationFrame(() => {
        document
          .querySelector(".proposal-scan-v2")
          ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    },
    [rfp.id, applyOutlineFromServer]
  );

  const handleLiveDraftUpdateRef = useRef(handleLiveDraftUpdate);
  const handleResearchPollRef = useRef(handleResearchPoll);
  handleLiveDraftUpdateRef.current = handleLiveDraftUpdate;
  handleResearchPollRef.current = handleResearchPoll;

  /** Resume live manuscript updates when user reopens during backend generation. */
  useEffect(() => {
    if (
      !hydrated ||
      isFullProposalRunning ||
      isFulfillingRfpGaps ||
      isAligningRfpOutline
    )
      return;

    let cancelled = false;
    let stopPoll: (() => void) | null = null;
    let abort: AbortController | null = null;

    async function reconnect() {
      const checkpointPhase = research?.pipelineCheckpoint?.inProgressPhase;
      const job = await getProposalJobStatus(rfp.id);
      if (cancelled) return;

      const jobRunning =
        job?.status === "running" && Boolean(job.jobType);
      const activePhase: PipelineInProgressPhase | null =
        (jobRunning ? (job!.jobType as PipelineInProgressPhase) : null) ??
        checkpointPhase ??
        null;

      if (!activePhase) return;
      if (!jobRunning && !checkpointPhase) return;

      setGenerateError(null);
      setGenerateNotice(pipelineServerStillWorkingMessage(activePhase));

      stopPoll = startLiveDraftPolling(
        rfp.id,
        (draft) => handleLiveDraftUpdateRef.current(draft),
        (updated) => {
          handleResearchPollRef.current(updated);
          const cp = updated?.pipelineCheckpoint;
          const live = cp?.inProgressPhase;
          if (!live) return;
          if (live === ALIGN_RFP_OUTLINE_PHASE) {
            const detail = cp?.activityDetail?.trim();
            const step =
              cp?.stepIndex != null && cp.stepTotal
                ? `Step ${cp.stepIndex}/${cp.stepTotal}`
                : null;
            setGenerateNotice(
              [step, detail || cp?.activityLabel || "Align to RFP outline…"]
                .filter(Boolean)
                .join(" — ")
            );
            return;
          }
          setGenerateNotice(pipelineServerStillWorkingMessage(live));
        }
      );

      if (activePhase === FULFILL_SCAN_PHASE) {
        if (!jobRunning) return;
        setIsFulfillingRfpGaps(true);
        fulfillCompletionShownRef.current = false;
        fulfillSawRunningRef.current = true;
        abort = new AbortController();
        fulfillAbortRef.current = abort;
        try {
          const waited = await pollFulfillScanCompletion(rfp.id, abort.signal);
          if (cancelled) return;
          await applyFulfillScanSuccessUi({
            draft: waited.draft,
            research: waited.research,
            fulfillReport:
              (waited.draft?.lastFulfillReport as Record<string, unknown>) ??
              null,
          });
        } catch (error) {
          if (cancelled) return;
          // User Stop only — not poll/timeout AbortErrors.
          if (abort.signal.aborted) {
            setGenerateNotice(
              "Review & fix stopped — progress saved. Use Review & fix to resume from the last step."
            );
            setGenerateError(null);
            return;
          }
          const message =
            error instanceof Error ? error.message : "Review & fix failed";
          setGenerateError(message);
        } finally {
          if (fulfillAbortRef.current === abort) {
            fulfillAbortRef.current = null;
          }
          stopPoll?.();
          if (!cancelled) setIsFulfillingRfpGaps(false);
        }
        return;
      }

      // Align is not Generate — show Align rail + live notice, never "RFP tabs".
      if (activePhase === ALIGN_RFP_OUTLINE_PHASE) {
        if (!jobRunning) return;
        setIsAligningRfpOutline(true);
        abort = new AbortController();
        try {
          const waited = await pollAlignRfpOutlineCompletion(
            rfp.id,
            abort.signal
          );
          if (cancelled) return;
          if (waited.draft) applyOutlineFromServer(waited.draft);
          if (waited.research) {
            handleResearchPollRef.current(waited.research);
          }
          const report = (waited.draft?.lastFulfillReport ?? {}) as {
            changed?: boolean;
            beforeTitles?: string[];
            afterTitles?: string[];
          };
          const beforeN = report.beforeTitles?.length ?? 0;
          const afterN = report.afterTitles?.length ?? 0;
          setGenerateNotice(
            report.changed
              ? `Aligned to RFP outline — ${beforeN} → ${afterN} tabs (order/stubs only; prose unchanged).`
              : "Align finished — outline already matched the RFP order (prose unchanged)."
          );
        } catch (error) {
          if (cancelled) return;
          if (
            abort.signal.aborted ||
            (error instanceof DOMException && error.name === "AbortError")
          ) {
            setGenerateNotice("Align to RFP outline stopped.");
            setGenerateError(null);
            return;
          }
          setGenerateError(
            error instanceof Error ? error.message : "Align to RFP outline failed"
          );
        } finally {
          stopPoll?.();
          if (!cancelled) setIsAligningRfpOutline(false);
        }
        return;
      }

      const progressMap: Record<string, FullProposalProgress> = {
        "sections-1-3": "sections-1-3",
        "phase-2": "phase-2",
        "phase-3": "phase-3",
        "phase-3-6-self-edit": "phase-3-6-self-edit",
        "phase-3-5-budget": "phase-3-5-budget",
        "phase-4-review": "phase-4-review",
        "build-finalize": "build-finalize",
      };
      const mapped =
        progressMap[activePhase] ?? fullProposalProgressFromInFlight(activePhase);
      if (!mapped || !jobRunning) return;

      // Server is still drafting (Celery chained the next phase, or the tab
      // reconnected mid-run). Re-attach the client orchestrator so the UI does
      // not sit idle after Phase 2 while Phase 3+ keeps going.
      setIsFullProposalRunning(true);
      setFullProposalProgress(mapped);
      fullProposalInFlightRef.current = true;
      abort = new AbortController();
      fullProposalAbortRef.current = abort;
      try {
        const { draft, research: updatedResearch } =
          await generateFullProposalStaged(rfp.id, setFullProposalProgress, {
            signal: abort.signal,
            onDraftUpdate: (d) => handleLiveDraftUpdateRef.current(d),
            onResearchUpdate: (r) => handleResearchPollRef.current(r),
          });
        if (cancelled || abort.signal.aborted) return;
        applyOutlineFromServer(draft);
        if (updatedResearch) {
          handleResearchPollRef.current(updatedResearch);
        }
        setGenerateNotice(null);
      } catch (error) {
        if (cancelled || abort.signal.aborted) return;
        setGenerateError(
          error instanceof Error ? error.message : "Proposal generation failed"
        );
      } finally {
        fullProposalInFlightRef.current = false;
        if (fullProposalAbortRef.current === abort) {
          fullProposalAbortRef.current = null;
        }
        stopPoll?.();
        if (!cancelled) {
          setIsFullProposalRunning(false);
          setFullProposalProgress(null);
        }
      }
    }

    void reconnect();
    return () => {
      cancelled = true;
      stopPoll?.();
      // Never abort the live Generate controller — that made Phase 2→3 look
      // "Stopped" when this effect re-ran on checkpoint updates.
      if (abort && abort !== fullProposalAbortRef.current) {
        abort.abort();
      }
    };
  }, [
    hydrated,
    isFullProposalRunning,
    isFulfillingRfpGaps,
    isAligningRfpOutline,
    research?.pipelineCheckpoint?.inProgressPhase,
    rfp.id,
    applyOutlineFromServer,
    applyFulfillScanSuccessUi,
  ]);

  /**
   * Safety net: if Celery finishes while the long POST/race is still hanging,
   * the live research poll already cleared inProgressPhase and stamped
   * lastCleanFulfillScanAt — flip the green banner + draft without waiting
   * for the HTTP request (and without requiring a page refresh).
   */
  useEffect(() => {
    if (!hydrated || !isFulfillingRfpGaps || fulfillJustCompleted) return;
    if (fulfillCompletionShownRef.current) return;
    if (!fulfillSawRunningRef.current) return;
    const cp = research?.pipelineCheckpoint;
    if (!cp || cp.inProgressPhase === FULFILL_SCAN_PHASE) return;
    const at = cp.lastCleanFulfillScanAt;
    if (!at) return;
    const finishedAt = Date.parse(at);
    if (!Number.isFinite(finishedAt) || Date.now() - finishedAt > 15 * 60 * 1000) {
      return;
    }
    void applyFulfillScanSuccessUi();
  }, [
    hydrated,
    isFulfillingRfpGaps,
    fulfillJustCompleted,
    research?.pipelineCheckpoint?.inProgressPhase,
    research?.pipelineCheckpoint?.lastCleanFulfillScanAt,
    applyFulfillScanSuccessUi,
  ]);

  /** Don't keep a stale "Stopped" banner while the server job is still running. */
  useEffect(() => {
    if (!hydrated || !generateNotice?.startsWith("Stopped")) return;

    let cancelled = false;
    const syncRunningJob = async () => {
      const job = await getProposalJobStatus(rfp.id);
      if (cancelled || !job || job.status !== "running" || !job.jobType) return;

      setGenerateError(null);
      setGenerateNotice(
        pipelineServerStillWorkingMessage(job.jobType as PipelineInProgressPhase)
      );
      if (job.jobType === FULFILL_SCAN_PHASE) {
        setIsFulfillingRfpGaps(true);
        return;
      }
      if (job.jobType === ALIGN_RFP_OUTLINE_PHASE) {
        setIsAligningRfpOutline(true);
        return;
      }
      setIsFullProposalRunning(true);
      setFullProposalProgress(
        fullProposalProgressFromInFlight(job.jobType as PipelineInProgressPhase)
      );
    };

    void syncRunningJob();
    const interval = setInterval(() => void syncRunningJob(), 4000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [hydrated, rfp.id, generateNotice]);

  const rfpTabProgress = useMemo(() => {
    const ids = new Set(research?.rfpSections?.map((s) => s.id) ?? []);
    if (ids.size === 0) return null;
    const filled = outline.sections.filter(
      (s) => ids.has(s.id) && (s.content || "").trim()
    ).length;
    return { filled, total: ids.size };
  }, [research?.rfpSections, outline.sections]);

  const handleStopPipeline = useCallback(async () => {
    setIsStopping(true);
    fullProposalAbortRef.current?.abort();
    fulfillAbortRef.current?.abort();
    try {
      await stopProposalGeneration(rfp.id);
    } catch {
      // Still stop UI even if stop request fails (e.g. offline).
    }
    // Wait for the running task to actually wind down in Celery + backend — the
    // cancel is cooperative, so the worker finishes its current LLM call, then
    // records the stop. Poll job-status until it is no longer in-flight so the
    // "Stopping…" state stays up until it is genuinely stopped.
    const stopDeadline = Date.now() + 90_000;
    while (Date.now() < stopDeadline) {
      let job: Awaited<ReturnType<typeof getProposalJobStatus>> = null;
      try {
        job = await getProposalJobStatus(rfp.id);
      } catch {
        break; // status unreachable — stop waiting rather than hang forever
      }
      if (!job || (job.status !== "running" && job.status !== "queued")) {
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }
    let snapshot: Awaited<ReturnType<typeof fetchProposalDraft>> | null = null;
    try {
      snapshot = await fetchProposalDraft(rfp.id);
      if (snapshot.draft) {
        applyOutlineFromServer(snapshot.draft);
      }
      if (snapshot.research) {
        setResearch(snapshot.research);
        setPipelineStatus(
          buildPipelineStatus(
            snapshot.draft,
            snapshot.research,
            snapshot.pipelineStatus
          )
        );
        if (snapshot.research.budget) {
          setBudget(snapshot.research.budget);
        }
        if (snapshot.research.presubmitReview) {
          setPresubmitReview(snapshot.research.presubmitReview);
        }
      }
    } catch {
      // Non-fatal — checkpoint still updated on server.
    }
    setIsFullProposalRunning(false);
    setIsFulfillingRfpGaps(false);
    setIsPricingRunning(false);
    setIsRefiningBudget(false);
    setIsFinalizingGaps(false);
    setFullProposalProgress(null);
    setLiveLatestSectionTitle(null);
    fullProposalAbortRef.current = null;
    fulfillAbortRef.current = null;
    const resumeFulfillStep =
      snapshot?.research?.pipelineCheckpoint?.resumeFulfillStep ?? null;
    const resumeLabel =
      resumeFulfillStep &&
      resumeFulfillStep >= 1 &&
      resumeFulfillStep <= FULFILL_SCAN_STEP_LABELS.length
        ? FULFILL_SCAN_STEP_LABELS[resumeFulfillStep - 1]
        : null;
    setGenerateNotice(
      resumeLabel
        ? `Stopped — progress saved. Use Review & fix to resume from step ${resumeFulfillStep} (${resumeLabel}).`
        : "Stopped — progress saved in the database. Use Continue proposal to resume."
    );
    setGenerateError(null);
    setIsStopping(false);
  }, [rfp.id, applyOutlineFromServer]);

  const handleDesignerCompactAll = useCallback(async () => {
    if (isDesignerCompacting || anyPipelineRunning) return;
    setIsDesignerCompacting(true);
    setGenerateError(null);
    setGenerateNotice(null);
    try {
      const { draft, research: compactResearch } = await runDesignerCompactManuscript(
        rfp.id
      );
      applyOutlineFromServer(draft);
      setResearch(compactResearch);
      if (compactResearch.budget) setBudget(compactResearch.budget);
      setGenerateNotice(
        "Designer-compact pass complete — overlong tabs rewritten as tables/bullets with full RFP coverage."
      );
    } catch (error) {
      setGenerateError(
        error instanceof Error ? error.message : "Designer-compact pass failed"
      );
    } finally {
      setIsDesignerCompacting(false);
    }
  }, [
    rfp.id,
    applyOutlineFromServer,
    isDesignerCompacting,
    anyPipelineRunning,
  ]);

  const handleMatchCaseStudies = useCallback(async () => {
    if (isMatchingCaseStudies || anyPipelineRunning) return;
    setCaseStudyMatchOpen(true);
    setIsMatchingCaseStudies(true);
    setCaseStudyMatchError(null);
    setCaseStudyMatchResult(null);
    try {
      const result = await matchCaseStudiesForRfp(rfp.id);
      setCaseStudyMatchResult(result);
      setGenerateNotice(result.message || "Case study match complete.");
    } catch (error) {
      setCaseStudyMatchError(
        error instanceof Error ? error.message : "Case study match failed"
      );
    } finally {
      setIsMatchingCaseStudies(false);
    }
  }, [rfp.id, isMatchingCaseStudies, anyPipelineRunning]);

  // "Already cleaned" for this draft — true from this session's just-finished run
  // (fulfillJustCompleted) OR from the server (survives refresh, same for every
  // user; set false again the moment the draft is edited). Drives the "run
  // again?" confirm modal and the button's done state.
  const scanAlreadyDone =
    fulfillJustCompleted || Boolean(pipelineStatus?.fulfillScanUpToDate);

  // A Complete & clean run has completed for this RFP at some point (even if the
  // draft was edited since). Distinguishes a genuine FIRST run from a re-run, so
  // the confirm never wrongly says "first run" after a scan already happened.
  const hasCompletedScanBefore =
    fulfillJustCompleted || Boolean(pipelineStatus?.fulfillScanCompletedAt);

  // A Complete & clean run that finished in the last 30 min, from the SERVER —
  // so the "finished successfully" banner shows even after a page refresh (or
  // for a different user), not only in the session that launched it.
  const scanRecentlyCompleted = useMemo(() => {
    const at = pipelineStatus?.fulfillScanCompletedAt;
    if (!at) return false;
    const t = Date.parse(at);
    return Number.isFinite(t) && Date.now() - t < 30 * 60 * 1000;
  }, [pipelineStatus?.fulfillScanCompletedAt]);

  const handleFulfillRfpGaps = useCallback(async () => {
    // A duplicate invocation while one is already in flight (double-click,
    // a second call sharing this same handler) used to silently abort the
    // running request via fulfillAbortRef below — the run would look
    // "stopped" client-side even though the backend kept executing
    // independently to completion. No-op instead of restarting.
    if (isFulfillingRfpGaps) {
      return;
    }
    const completeCleanGuide = formatDoesDoesntBlock("completeClean", "ralph");
    const scanOk = await confirm(
      scanAlreadyDone && !canResumeFulfillScan
        ? {
            title: "Review & fix already ran for this draft",
            description:
              "This draft was just cleaned and nothing has changed since. Running it " +
              "again will re-check the same content and cost tokens for no new changes.\n\n" +
              `${completeCleanGuide}\n\n` +
              "Are you sure you want to run it again?",
            confirmLabel: "Run again anyway",
            tone: "default",
          }
        : canResumeFulfillScan
        ? {
            title: "Resume Review & fix?",
            description:
              `Continue from step ${fulfillResumeStep} — ${fulfillResumeLabel}.\n\n` +
              "Earlier steps are already saved on the draft. Pre-submit refresh and submission readiness still run in full — missing answers are filled from past won proposals and the ending report is rebuilt for designer handoff.\n\n" +
              completeCleanGuide,
            confirmLabel: "Resume",
            tone: "default",
          }
        : hasCompletedScanBefore
        ? {
            title: "Run Review & fix again?",
            description:
              "Review & fix already ran for this draft, and the draft has changed since. " +
              "Running it again re-checks the whole proposal in the background. A saved version is stored first.\n\n" +
              completeCleanGuide,
            confirmLabel: "Run again",
            tone: "default",
          }
        : {
            title: "Review & fix (optional)",
            description:
              "Build my proposal already matched RFP order, fact-checked, and ran Ralph trim. " +
              "Use Review & fix only if you edited the draft and want a full re-audit. " +
              "It re-reads the whole proposal and spends extra tokens.\n\n" +
              `${completeCleanGuide}\n\n` +
              "If you continue:\n" +
              "• A saved version is stored first\n" +
              "• You can keep working while it runs\n" +
              `• ${capabilityById("completeClean").doesnt}`,
            confirmLabel: "Run anyway",
            tone: "default",
          }
    );
    if (!scanOk) {
      return;
    }
    setGenerateNotice(null);
    setGenerateError(null);
    setIsFulfillingRfpGaps(true);
    setGapResolveError(null);
    setGapResolveNotice(null);
    setScanSummary(null);
    setScanSummaryExpanded(false);
    setFulfillJustCompleted(false);
    setCompletionBannerDismissed(false);
    fulfillCompletionShownRef.current = false;
    fulfillSawRunningRef.current = false;
    fulfillAbortRef.current?.abort();
    const abort = new AbortController();
    fulfillAbortRef.current = abort;
    const stopScanPoll = startLiveDraftPolling(
      rfp.id,
      handleLiveDraftUpdate,
      handleResearchPoll
    );
    try {
      // The scan is a multi-minute background job. Awaiting the single request
      // for its whole duration means a dropped/timed-out connection (proxy or
      // browser closing a long-held request WITHOUT an error) never resolves —
      // the button then shows "running" forever until a manual refresh, even
      // though the backend finished and saved. Guard: race the request against a
      // job-status watcher so completion is detected either way.
      const requestOutcome = runFulfillRfpGaps(rfp.id, {
        signal: abort.signal,
        mode: "full",
      })
        .then((r) => ({ via: "request" as const, r }))
        .catch((e) => ({ via: "error" as const, e }));

      const watchCompletion = async (): Promise<
        "done" | "failed" | "stalled"
      > => {
        const deadline = Date.now() + 45 * 60_000;
        let sawRunning = false;
        while (Date.now() < deadline) {
          if (abort.signal.aborted) return "stalled";
          await new Promise((resolve) => setTimeout(resolve, 2500));
          let job: Awaited<ReturnType<typeof getProposalJobStatus>> = null;
          try {
            job = await getProposalJobStatus(rfp.id);
          } catch {
            continue; // status unreachable — keep waiting on the request
          }
          if (job && (job.status === "running" || job.status === "queued")) {
            sawRunning = true;
            continue;
          }
          if (job && job.status === "failed") return "failed";
          // Not in-flight. Only conclude "done" once we've actually seen the job
          // running — avoids a premature verdict in the gap before it registers.
          if (sawRunning) return "done";
        }
        return "stalled";
      };

      const outcome = await Promise.race([
        requestOutcome,
        watchCompletion().then((w) => ({ via: "watch" as const, w })),
      ]);

      if (outcome.via === "error") {
        throw outcome.e;
      }

      if (outcome.via === "request") {
        const { review, research: updatedResearch, draft, fulfillReport } =
          outcome.r;
        await applyFulfillScanSuccessUi({
          draft,
          research: updatedResearch,
          fulfillReport:
            (fulfillReport as Record<string, unknown>) ??
            (draft.lastFulfillReport as Record<string, unknown>) ??
            null,
          skipFetch: true,
        });
        if (review) setPresubmitReview(review);
      } else if (outcome.w === "failed") {
        throw new Error("Review & fix failed on the server.");
      } else {
        // Backend finished (or poll stalled while worker kept going) — pull final
        // saved state. Do NOT abort() here: that made success look like Stop.
        await applyFulfillScanSuccessUi();
      }
    } catch (error) {
      // Only a real user Stop (fulfillAbortRef) is "stopped". Timeout / race
      // AbortErrors must not clear a finished scan.
      if (abort.signal.aborted) {
        // A newer run already replaced this controller (e.g. an automatic
        // resume) — it will report its own progress, so don't flash a stale
        // "stopped" notice for a request that isn't live anymore.
        if (fulfillAbortRef.current !== abort) {
          return;
        }
        // If the safety-net effect already showed success, don't overwrite it
        // with a "stopped" banner.
        if (fulfillCompletionShownRef.current || fulfillJustCompleted) {
          return;
        }
        setScanSummary(null);
        setScanSummaryExpanded(false);
        setGenerateNotice(
          "Review & fix stopped — progress saved. Use Review & fix to resume from the last step."
        );
        setGenerateError(null);
        return;
      }
      const message =
        error instanceof Error ? error.message : "Review & fix failed";
      setScanSummary(null);
      setScanSummaryExpanded(false);
      setGapResolveError(message);
      setGenerateError(message);
    } finally {
      if (fulfillAbortRef.current === abort) {
        fulfillAbortRef.current = null;
      }
      stopScanPoll();
      setIsFulfillingRfpGaps(false);
    }
  }, [confirm, rfp.id, applyOutlineFromServer, handleLiveDraftUpdate, handleResearchPoll, applyFulfillScanSuccessUi, canResumeFulfillScan, fulfillResumeStep, fulfillResumeLabel, scanAlreadyDone, hasCompletedScanBefore, fulfillJustCompleted]);

  const handleAlignRfpOutline = useCallback(async () => {
    if (
      isAligningRfpOutline ||
      anyPipelineRunning ||
      alignPreviewLoading ||
      placePreviewLoading
    ) {
      return;
    }
    setAlignPreview(null);
    setAlignPreviewError(null);
    setAlignPreviewOpen(true);
    setAlignPreviewLoading(true);
    setGenerateNotice("Checking left list vs RFP order (preview only)…");
    setGenerateError(null);
    try {
      const result = await previewAlignRfpOutline(rfp.id);
      setAlignPreview(result.preview);
      setGenerateNotice(
        result.nothingToChange
          ? "Preview ready — left list already matches. Nothing to apply."
          : "Preview ready — compare the lists, then Apply changes if you agree."
      );
    } catch (error) {
      setAlignPreviewError(
        error instanceof Error ? error.message : "Align preview failed"
      );
      setGenerateNotice(null);
    } finally {
      setAlignPreviewLoading(false);
    }
  }, [
    rfp.id,
    isAligningRfpOutline,
    anyPipelineRunning,
    alignPreviewLoading,
    placePreviewLoading,
  ]);

  const handleApplyAlignPreview = useCallback(async () => {
    if (isAligningRfpOutline || anyPipelineRunning) return;
    setAlignPreviewOpen(false);
    setGenerateNotice(
      "Applying left-list changes — saving undo checkpoint…"
    );
    setGenerateError(null);
    setIsAligningRfpOutline(true);
    const abort = new AbortController();
    const stopPoll = startLiveDraftPolling(
      rfp.id,
      handleLiveDraftUpdate,
      (updated) => {
        handleResearchPoll(updated);
        const cp = updated?.pipelineCheckpoint;
        if (cp?.inProgressPhase === ALIGN_RFP_OUTLINE_PHASE) {
          const detail = cp.activityDetail?.trim();
          const step =
            cp.stepIndex != null && cp.stepTotal
              ? `Step ${cp.stepIndex}/${cp.stepTotal}`
              : null;
          setGenerateNotice(
            [step, detail || cp.activityLabel || "Align to RFP outline…"]
              .filter(Boolean)
              .join(" — ")
          );
        }
      }
    );
    try {
      const result = await runAlignRfpOutline(rfp.id, { signal: abort.signal });
      applyOutlineFromServer(result.draft);
      if (result.research) {
        handleResearchPoll(result.research);
      }
      const beforeAlign = [...(result.draft.snapshots ?? [])]
        .reverse()
        .find((s) => /before align to rfp/i.test(s.label ?? ""));
      if (beforeAlign?.savedAt) {
        setRestoreSnapshotAt(beforeAlign.savedAt);
      }
      const beforeN = result.report.beforeTitles?.length ?? 0;
      const afterN = result.report.afterTitles?.length ?? 0;
      setGenerateNotice(
        result.report.changed
          ? `Left list updated — ${beforeN} → ${afterN} headings (order/empty slots only; writing unchanged). Undo: Restore “Before Align to RFP outline”.`
          : "Align finished — list already matched (nothing changed)."
      );
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        setGenerateNotice("Align stopped.");
        setGenerateError(null);
        return;
      }
      setGenerateError(
        error instanceof Error ? error.message : "Align to RFP outline failed"
      );
      setGenerateNotice(null);
    } finally {
      stopPoll();
      setIsAligningRfpOutline(false);
    }
  }, [
    rfp.id,
    isAligningRfpOutline,
    anyPipelineRunning,
    applyOutlineFromServer,
    handleLiveDraftUpdate,
    handleResearchPoll,
  ]);

  const handleCloseAlignPreview = useCallback(() => {
    if (isAligningRfpOutline) return;
    setAlignPreviewOpen(false);
    setAlignPreviewError(null);
  }, [isAligningRfpOutline]);

  const handlePlacePacketContent = useCallback(async () => {
    if (isPlacingPacketContent || anyPipelineRunning || placePreviewLoading) {
      return;
    }
    setPlaceReport(null);
    setPlacePreview(null);
    setPlacePreviewError(null);
    setPlacePreviewOpen(true);
    setPlacePreviewLoading(true);
    setGenerateNotice("Scanning draft for misplaced blocks (preview only)…");
    setGenerateError(null);
    try {
      const result = await previewPacketRedistribute(rfp.id);
      setPlacePreview(result.preview);
      const n = result.plannedMoves ?? result.preview.plannedMoves ?? 0;
      setGenerateNotice(
        n > 0
          ? `Preview ready — ${n} move(s) proposed. Review the modal, then Apply.`
          : "Preview ready — no automatic moves. Check the modal for notes."
      );
    } catch (error) {
      setPlacePreviewError(
        error instanceof Error ? error.message : "Place preview failed"
      );
      setGenerateNotice(null);
    } finally {
      setPlacePreviewLoading(false);
    }
  }, [
    rfp.id,
    isPlacingPacketContent,
    anyPipelineRunning,
    placePreviewLoading,
  ]);

  const handleApplyPlacePreview = useCallback(async () => {
    if (isPlacingPacketContent || anyPipelineRunning) return;
    setPlacePreviewOpen(false);
    setPlaceReport(null);
    setGenerateNotice(
      "Applying approved moves — Step 1/4: saving undo checkpoint…"
    );
    setGenerateError(null);
    setIsPlacingPacketContent(true);
    const abort = new AbortController();
    const stopPoll = startLiveDraftPolling(
      rfp.id,
      handleLiveDraftUpdate,
      (updated) => {
        handleResearchPoll(updated);
        const cp = updated?.pipelineCheckpoint;
        if (cp?.inProgressPhase === PACKET_REDISTRIBUTE_PHASE) {
          const detail = cp.activityDetail?.trim();
          const step =
            cp.stepIndex != null && cp.stepTotal
              ? `Step ${cp.stepIndex}/${cp.stepTotal}`
              : null;
          setGenerateNotice(
            [step, detail || cp.activityLabel || "Place content…"]
              .filter(Boolean)
              .join(" — ")
          );
        }
      }
    );
    try {
      const result = await runPacketRedistribute(rfp.id, {
        signal: abort.signal,
      });
      applyOutlineFromServer(result.draft);
      if (result.research) {
        handleResearchPoll(result.research);
      }
      const beforePlace = [...(result.draft.snapshots ?? [])]
        .reverse()
        .find((s) => /before packet redistribute/i.test(s.label ?? ""));
      if (beforePlace?.savedAt) {
        setRestoreSnapshotAt(beforePlace.savedAt);
      }
      const report = result.report as PacketPlaceReport;
      setPlaceReport(report);
      const moved = report.movedCount ?? 0;
      const planned = report.plannedMoves ?? moved;
      const flags =
        (report.humanGaps?.length ?? 0) +
        (report.stubTitles?.length ?? 0) +
        (report.skipped?.length ?? 0);
      setGenerateNotice(
        moved > 0 || planned > 0
          ? `Place finished — ${moved}${planned ? ` of ${planned}` : ""} move(s).${
              flags > 0 ? ` ${flags} flag(s) to review below.` : ""
            } Undo: Restore “Before packet redistribute”.`
          : "Place finished — no block moves. Check the summary below for any notes."
      );
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        setGenerateNotice("Place content stopped.");
        setGenerateError(null);
        return;
      }
      setGenerateError(
        error instanceof Error ? error.message : "Place content failed"
      );
      setGenerateNotice(null);
      setPlaceReport(null);
    } finally {
      stopPoll();
      setIsPlacingPacketContent(false);
    }
  }, [
    rfp.id,
    isPlacingPacketContent,
    anyPipelineRunning,
    applyOutlineFromServer,
    handleLiveDraftUpdate,
    handleResearchPoll,
  ]);

  const handleClosePlacePreview = useCallback(() => {
    if (isPlacingPacketContent) return;
    setPlacePreviewOpen(false);
    setPlacePreviewError(null);
  }, [isPlacingPacketContent]);

  const handleGenerateFullProposal = useCallback(async (options?: {
    startAfterSections1to3?: boolean;
    startFromCaseStudies?: boolean;
  }) => {
    // A duplicate invocation while one is already in flight (double-click, a
    // second call sharing this same handler) used to silently abort the
    // running request via fullProposalAbortRef below — the run would look
    // "stopped" client-side even though the backend kept executing
    // independently to completion (confirmed live: a Celery task logged
    // succeeded well after the UI showed "Stopped"). No-op instead of
    // restarting; the running progress UI already reflects this state.
    if (isFullProposalRunning || fullProposalInFlightRef.current) {
      return;
    }
    fullProposalInFlightRef.current = true;
    // Continue = resume from checkpoint (e.g. budget failure).
    // Fresh / regenerate-from-done = forceRestart from Sections 1–3.
    // startAfterSections1to3 = Start from Intelligence (keep 1–3, re-run Phase 2+).
    // startFromCaseStudies = keep Company + Bios, re-extract Our Work, then Phase 2+.
    const startAfterSections1to3 = Boolean(options?.startAfterSections1to3);
    const startFromCaseStudies = Boolean(options?.startFromCaseStudies);
    const hasManuscriptContent = countSectionsWithContent(outline) > 0;
    // Never "resume" an empty outline — that is always a forceRestart generate.
    const shouldResume =
      !startAfterSections1to3 &&
      !startFromCaseStudies &&
      canResumePipeline &&
      hasManuscriptContent &&
      Boolean(pipelineStatus);

    if (startFromCaseStudies) {
      const caseOk = await confirm({
        title: "Start from Case Studies?",
        description:
          "This DELETES existing Our Work case studies, Intelligence, RFP tabs, Budget, and Review — then re-runs case-study extraction and rebuilds from there.\n\n" +
          "Company overview + Team Bios are kept.",
        confirmLabel: "Start from Case Studies",
        tone: "danger",
      });
      if (!caseOk) {
        fullProposalInFlightRef.current = false;
        return;
      }
    } else if (startAfterSections1to3) {
      const intelligenceOk = await confirm({
        title: "Start from Intelligence?",
        description:
          "This DELETES existing Intelligence, RFP tabs, Budget, and Review — then rebuilds them.\n\n" +
          "Sections 1–3 are kept.",
        confirmLabel: "Start from Intelligence",
        tone: "danger",
      });
      if (!intelligenceOk) {
        fullProposalInFlightRef.current = false;
        return;
      }
    } else if (shouldResume) {
      const resumeOk = await confirm({
        title: "Continue proposal?",
        description: `${pipelineResumeMessage(pipelineStatus!)}\n\nContinue from where it left off? (Skips finished phases.)`,
        confirmLabel: "Continue",
        tone: "default",
      });
      if (!resumeOk) {
        fullProposalInFlightRef.current = false;
        return;
      }
    } else if (buildPipelineComplete) {
      const restartOk = await confirm({
        title: "Build my proposal already ran",
        description:
          "This RFP already completed the full build (Sections 1–3, drafting, review, and final checks). " +
          "Running again re-generates content and uses LLM tokens (often several dollars on large RFPs).\n\n" +
          "Run the full build again?",
        confirmLabel: "Run build again",
        tone: "danger",
      });
      if (!restartOk) {
        fullProposalInFlightRef.current = false;
        return;
      }
    } else if (fullProposalDone) {
      const restartOk = await confirm({
        title: "Start full proposal from the beginning?",
        description:
          "This regenerates Sections 1–3, intelligence, drafting, budget, and review (uses LLM tokens).",
        confirmLabel: "Start from beginning",
        tone: "danger",
      });
      if (!restartOk) {
        fullProposalInFlightRef.current = false;
        return;
      }
    }

    fullProposalAbortRef.current?.abort();
    const abort = new AbortController();
    fullProposalAbortRef.current = abort;

    try {
      sessionStorage.removeItem(buildBannerDismissKey(rfp.id));
      setBuildBannerDismissed(false);
    } catch {
      // ignore quota / private mode
    }

    const forceRestart = !(shouldResume || startAfterSections1to3 || startFromCaseStudies);

    setIsFullProposalRunning(true);
    setFullProposalProgress(null);
    setGenerateError(null);
    setGenerateNotice(null);

    // Fresh start: clear the editor immediately so old manuscript cannot flash
    // while the server soft-regenerates Sections 1–3 in place (no DB wipe).
    // Keep Key Persona picks — buildDefaultOutline() starts at [] and must not
    // erase the selection the generate-gate just confirmed.
    if (forceRestart) {
      const defaults = {
        ...buildDefaultOutline(rfp),
        selectedKeyPersonas: outline.selectedKeyPersonas ?? [],
      };
      saveGenerationRef.current += 1;
      skipNextSaveRef.current = true;
      liveContentFingerprintRef.current = new Map();
      setOutline(defaults);
      setResearch(null);
      setBudget(null);
      setPresubmitReview(null);
      setPipelineStatus(null);
      setSectionRevisions({});
      persistStoredRevisions(rfp.id, {});
      setLiveGeneratedCount(0);
      setLiveLatestSectionTitle(null);
      setSelectedSectionId(defaults.sections[0]?.id ?? null);
      setActiveTab("content");
    } else if (startFromCaseStudies) {
      try {
        try {
          await stopProposalGeneration(rfp.id);
        } catch {
          // Best-effort: nothing may be running.
        }
        const stripped = await restartProposalFromCaseStudies(rfp.id);
        saveGenerationRef.current += 1;
        skipNextSaveRef.current = true;
        liveContentFingerprintRef.current = new Map();
        applyOutlineFromServer(stripped);
        setResearch(null);
        setBudget(null);
        setPresubmitReview(null);
        setPipelineStatus(null);
        setSectionRevisions({});
        persistStoredRevisions(rfp.id, {});
        setLiveGeneratedCount(countSectionsWithContent(stripped));
        setLiveLatestSectionTitle(null);
        setSelectedSectionId(stripped.sections[0]?.id ?? null);
        setActiveTab("content");
        setGenerateNotice(
          "Cleared Our Work + Intelligence. Re-running case-study extraction…"
        );
      } catch (error) {
        setIsFullProposalRunning(false);
        setGenerateError(
          error instanceof Error
            ? error.message
            : "Failed to clear case studies before restart."
        );
        fullProposalInFlightRef.current = false;
        return;
      }
    } else if (startAfterSections1to3) {
      // Wipe stale Intelligence / RFP tabs before Phase 2 rebuilds a lean outline.
      try {
        try {
          await stopProposalGeneration(rfp.id);
        } catch {
          // Best-effort: nothing may be running.
        }
        const stripped = await restartProposalFromIntelligence(rfp.id);
        saveGenerationRef.current += 1;
        skipNextSaveRef.current = true;
        liveContentFingerprintRef.current = new Map();
        applyOutlineFromServer(stripped);
        setResearch(null);
        setBudget(null);
        setPresubmitReview(null);
        setPipelineStatus(null);
        setSectionRevisions({});
        persistStoredRevisions(rfp.id, {});
        setLiveGeneratedCount(countSectionsWithContent(stripped));
        setLiveLatestSectionTitle(null);
        setSelectedSectionId(stripped.sections[0]?.id ?? null);
        setActiveTab("content");
        setGenerateNotice(
          "Cleared previous Intelligence / RFP tabs. Rebuilding from Phase 2…"
        );
      } catch (error) {
        setIsFullProposalRunning(false);
        setGenerateError(
          error instanceof Error
            ? error.message
            : "Failed to clear previous Intelligence before restart."
        );
        fullProposalInFlightRef.current = false;
        return;
      }
    } else {
      setLiveGeneratedCount(countSectionsWithContent(outline));
      setLiveLatestSectionTitle(null);
    }

    try {
      const { draft, research: updatedResearch } =
        await generateFullProposalStaged(rfp.id, setFullProposalProgress, {
          forceRestart,
          // Continue trusts backend resumeFromPhase — do not pass a client override.
          startFrom: startAfterSections1to3
            ? "phase-2"
            : startFromCaseStudies
              ? "sections-1-3"
              : undefined,
          forceRerunFromStart: startAfterSections1to3 || startFromCaseStudies,
          signal: abort.signal,
          onDraftUpdate: handleLiveDraftUpdate,
          onResearchUpdate: handleResearchPoll,
        });
      if (abort.signal.aborted) return;
      applyOutlineFromServer(draft);
      if (updatedResearch) {
        setResearch(updatedResearch);
        const snap = await fetchProposalDraft(rfp.id);
        setPipelineStatus(
          snap.pipelineStatus ??
            buildPipelineStatus(draft, updatedResearch, snap.pipelineStatus)
        );
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
    } catch (error) {
      // Only treat *user/explicit* abort as Stop. Timeouts and AbortSignal.any
      // from PHASE_START_TIMEOUT also raise AbortError — those must resume, not
      // look like the user hit Stop.
      if (abort.signal.aborted) {
        if (fullProposalAbortRef.current !== abort) {
          // A newer run already replaced this controller (e.g. an automatic
          // resume) — it will report its own progress, so don't flash a
          // stale "Stopped" notice for a request that isn't live anymore.
          return;
        }
        setGenerateNotice(
          "Stopped — progress saved in the database. Use Continue proposal to resume."
        );
        setGenerateError(null);
        return;
      }
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
          const snap = await fetchProposalDraft(rfp.id);
          const status =
            snap.pipelineStatus ??
            buildPipelineStatus(
              recovered.draft,
              recovered.research,
              snap.pipelineStatus
            );
          setPipelineStatus(status);
          if (recovered.research.budget) {
            setBudget(recovered.research.budget);
          }
          if (recovered.research.presubmitReview) {
            setPresubmitReview(recovered.research.presubmitReview);
          }
          setActiveTab("content");
          setSelectedSectionId(
            recovered.draft.sections.find((s) => s.content)?.id ??
              recovered.draft.sections[0]?.id ??
              null
          );
          const inFlight = recovered.research?.pipelineCheckpoint?.inProgressPhase;
          const serverNote = inFlight
            ? ` ${pipelineServerStillWorkingMessage(inFlight)}`
            : "";
          setGenerateNotice(
            `Step failed, but progress is saved. ${pipelineResumeMessage(status, { blocker: errMsg })}${serverNote}`
          );
          setGenerateError(null);
        } else {
          setActiveTab("content");
          setSelectedSectionId(
            recovered.draft.sections.find((s) => s.content)?.id ??
              recovered.draft.sections[0]?.id ??
              null
          );
          setGenerateError(errMsg);
        }
      } else {
        setGenerateError(errMsg);
      }
    } finally {
      fullProposalInFlightRef.current = false;
      if (fullProposalAbortRef.current === abort) {
        fullProposalAbortRef.current = null;
      }
      setIsFullProposalRunning(false);
      setLiveLatestSectionTitle(null);
      let keepClientProgress = false;
      if (!abort.signal.aborted) {
        try {
          const snap = await fetchProposalDraft(rfp.id);
          if (snap.research) {
            setResearch(snap.research);
            setPipelineStatus(
              snap.pipelineStatus ??
                buildPipelineStatus(
                  snap.draft ?? outline,
                  snap.research,
                  snap.pipelineStatus
                )
            );
            if (isBuildPipelineComplete(snap.pipelineStatus, snap.research)) {
              try {
                sessionStorage.removeItem(buildBannerDismissKey(rfp.id));
              } catch {
                // ignore
              }
              setBuildBannerDismissed(false);
            }
            keepClientProgress = Boolean(
              snap.research.pipelineCheckpoint?.inProgressPhase
            );
          }
        } catch {
          keepClientProgress = false;
        }
      }
      if (!keepClientProgress) {
        setFullProposalProgress(null);
      }
    }
  }, [confirm, rfp, buildPipelineComplete, fullProposalDone, canResumePipeline, pipelineStatus, outline, handleLiveDraftUpdate, handleResearchPoll, applyOutlineFromServer]);

  const handleResetOutline = async () => {
    setIsResettingDraft(true);
    setResetConfirmOpen(false);

    // Cancel in-flight Full Proposal / budget HTTP calls first
    fullProposalAbortRef.current?.abort();
    fullProposalAbortRef.current = null;
    setIsFullProposalRunning(false);
    setIsPricingRunning(false);
    setIsRefiningBudget(false);
    setFullProposalProgress(null);
    setLiveLatestSectionTitle(null);
    liveContentFingerprintRef.current = new Map();

    // 1. Hard-delete from DB (archives filled draft first, then wipe)
    let resetFailed: string | null = null;
    try {
      await resetProposal(rfp.id);
    } catch (error) {
      resetFailed =
        error instanceof Error
          ? error.message
          : "Server reset failed — local outline still cleared.";
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
    setBudget(null);
    setPipelineStatus(null);
    setGenerateError(resetFailed);
    setGenerateNotice(
      resetFailed
        ? "Local outline cleared, but server wipe failed — try Reset again before generating."
        : "Reset complete. Live draft and research cache cleared."
    );
    setFullProposalProgress(null);
    setLiveLatestSectionTitle(null);
    setLiveGeneratedCount(0);

    // 3. Persist empty shell so a late autosave / race cannot resurrect old monolith sections
    try {
      await saveProposalDraft(rfp.id, defaults);
    } catch {
      // Non-fatal — DB reset already cleared content
    } finally {
      setIsResettingDraft(false);
    }
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

      setFullProposalProgress("phase-3-5-budget");
      const { draft: budgetedDraft, research: afterBudget, budget } =
        await runPhase3_5BudgetWithRecovery(rfp.id);

      setFullProposalProgress("phase-3-6-self-edit");
      const { draft: polished, research: afterEdit } =
        await runPhase3_6SelfEditWithRecovery(rfp.id);

      setFullProposalProgress("phase-4-review");
      const { research: reviewedResearch } = await runPhase4PreSubmitReview(rfp.id);

      const finalDraft = polished ?? budgetedDraft ?? drafted;
      applyOutlineFromServer(finalDraft);
      setResearch(reviewedResearch ?? afterEdit ?? afterBudget ?? afterPhase3);
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
    const seeded = buildDefaultOutline(rfp);
    applyOutlineFromServer(seeded);
    await saveProposalDraft(rfp.id, seeded);
    // Show Section 1 stubs immediately so the user can follow subsection-by-subsection.
    setActiveTab("content");
    setSelectedSectionId(
      seeded.sections.find((s) => s.id.startsWith("section-1-"))?.id ??
        seeded.sections[0]?.id ??
        null
    );
    setLiveGeneratedCount(0);
    setLiveLatestSectionTitle(null);
    const stopPolling = startLiveDraftPolling(
      rfp.id,
      handleLiveDraftUpdate,
      handleResearchPoll
    );
    try {
      const draft = await generateProposalSections1to3(rfp.id);
      if (!staticSections1to3Complete(draft)) {
        throw new Error(
          "Sections 1–3 finished but content is missing. Click Reset, then try Draft Sections 1–3 again."
        );
      }
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
  }, [rfp, applyOutlineFromServer, handleLiveDraftUpdate]);

  const handlePrimaryPipeline = useCallback(async () => {
    if (manuscriptRecoveryNeeded) {
      await handleRecoverManuscript();
      return;
    }
    await handleGenerateFullProposal();
  }, [manuscriptRecoveryNeeded, handleRecoverManuscript, handleGenerateFullProposal]);

  const primaryPipelineLabel = useMemo(() => {
    if (isFullProposalRunning || serverPipelineActive) {
      const activity = research?.pipelineCheckpoint?.activityLabel?.trim();
      if (activity) {
        return activity.length > 44 ? `${activity.slice(0, 43)}…` : activity;
      }
      if (effectiveFullProposalProgress === "sections-1-3") return "Sections 1–3…";
      if (effectiveFullProposalProgress === "phase-2") return "Intelligence…";
      if (effectiveFullProposalProgress === "phase-3") {
        if (rfpTabProgress) {
          return `RFP tabs ${rfpTabProgress.filled}/${rfpTabProgress.total}…`;
        }
        return "RFP tabs…";
      }
      if (effectiveFullProposalProgress === "phase-3-6-self-edit") {
        return manualFillCount > 0
          ? `Senior editor · ${manualFillCount} flags…`
          : "Senior editor…";
      }
      if (effectiveFullProposalProgress === "phase-3-5-budget") return "Budget…";
      if (effectiveFullProposalProgress === "phase-4-review") return "Pre-submit…";
      if (effectiveFullProposalProgress === "build-finalize") return "Final checks…";
      if (effectiveFullProposalProgress === "recovering") return "Syncing…";
      return "Working…";
    }
    if (isFulfillingRfpGaps) {
      const activity = research?.pipelineCheckpoint?.activityLabel?.trim();
      if (activity) {
        return activity.length > 40 ? `${activity.slice(0, 39)}…` : activity;
      }
      return "Review & fix…";
    }
    if (canResumePipeline) return "Continue build";
    return "Build my proposal";
  }, [
    isFullProposalRunning,
    serverPipelineActive,
    effectiveFullProposalProgress,
    canResumePipeline,
    isFulfillingRfpGaps,
    research?.pipelineCheckpoint?.activityLabel,
    rfpTabProgress,
    manualFillCount,
  ]);

  const updateSection = (id: string, patch: Partial<OutlineSection>) => {
    // A genuine user edit means the draft no longer matches the last completed
    // Complete & clean run — re-enable the button.
    setFulfillJustCompleted(false);
    setOutline((prev) => ({
      ...prev,
      sections: prev.sections.map((s) =>
        s.id === id ? { ...s, ...patch } : s
      ),
      updatedAt: new Date().toISOString(),
    }));
  };

  const moveSection = (id: string, direction: -1 | 1) => {
    setFulfillJustCompleted(false);
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

  const reorderSectionByDrag = useCallback(
    (fromId: string, toId: string) => {
      if (anyPipelineRunning) return;
      setFulfillJustCompleted(false);
      setOutline((prev) => {
        const sections = reorderSectionsById(prev.sections, fromId, toId);
        if (sections === prev.sections) return prev;
        return {
          ...prev,
          sections,
          updatedAt: new Date().toISOString(),
        };
      });
    },
    [anyPipelineRunning]
  );

  const removeSection = async (id: string) => {
    const target = outline.sections.find((s) => s.id === id);
    if (!target) return;
    if (outline.sections.length <= 1) return;
    const ok = await confirm({
      title: `Delete “${target.title}”?`,
      description: "This can’t be undone from here.",
      confirmLabel: "Delete section",
      tone: "danger",
    });
    if (!ok) return;

    setFulfillJustCompleted(false);
    setOutline((prev) => {
      let bio = 0;
      let work = 0;
      const sections = prev.sections
        .filter((s) => s.id !== id)
        .map((s) => {
          if (
            s.id.startsWith("section-2-bio-") &&
            s.id !== "section-2-bio-placeholder"
          ) {
            bio += 1;
            const name = s.title.includes("—")
              ? s.title.split("—").slice(1).join("—").trim()
              : s.title;
            return { ...s, title: `2.${bio} — ${name}` };
          }
          if (
            s.id.startsWith("section-3-work-") &&
            s.id !== "section-3-work-placeholder"
          ) {
            work += 1;
            const name = s.title.includes("—")
              ? s.title.split("—").slice(1).join("—").trim()
              : s.title;
            return { ...s, title: `3.${work} — ${name}` };
          }
          return s;
        });
      if (selectedSectionId === id) {
        setSelectedSectionId(sections[0]?.id ?? null);
      }
      return { ...prev, sections, updatedAt: new Date().toISOString() };
    });
    setSectionRevisions((prev) => {
      if (!(id in prev)) return prev;
      const next = { ...prev };
      delete next[id];
      persistStoredRevisions(rfp.id, next);
      return next;
    });
  };

  const addCustomSection = () => {
    const title = newSectionTitle.trim();
    if (!title) return;
    setFulfillJustCompleted(false);
    const section = createCustomSection(title);
    setOutline((prev) => ({
      ...prev,
      sections: [...prev.sections, section],
      updatedAt: new Date().toISOString(),
    }));
    setSelectedSectionId(section.id);
    setNewSectionTitle("");
  };

  const fullManuscript = useMemo(() => {
    return getManuscriptSections(outline.sections)
      .filter((s) => s.content?.trim())
      .map((s) => {
        const clean = stripManuscriptDisplayArtifacts(s.content || "").replace(
          /\s*\[E\d+\]/g,
          ""
        );
        return `## ${s.title}\n\n${clean}`;
      })
      .join("\n\n---\n\n");
  }, [outline.sections]);

  const handleDownloadDocx = async () => {
    if (!fullManuscript.trim()) return;
    setDocxDownloadError(null);
    setIsDownloadingDocx(true);
    try {
      await downloadProposalDocx(rfp.id);
      setDocxDownloaded(true);
      setTimeout(() => setDocxDownloaded(false), 3000);
    } catch (error) {
      setDocxDownloadError(
        error instanceof Error ? error.message : "Word download failed."
      );
    } finally {
      setIsDownloadingDocx(false);
    }
  };

  if (!hydrated || draftLoadState === "loading" || draftLoadState === "idle") {
    return (
      <section className="proposal-workspace-card">
        <div className="proposal-workspace-chrome shrink-0 border-b border-zo-border/80 bg-white">
          <div className="flex items-center gap-3 px-3 py-2 md:px-4">
            <h2 className="min-w-0 flex-1 truncate text-sm font-semibold leading-tight text-foreground md:text-[0.95rem]">
              {rfp.title}
            </h2>
            {onOpenGoRfpPicker && goRfpCount ? (
              <button
                type="button"
                onClick={onOpenGoRfpPicker}
                className="proposal-go-picker-btn shrink-0"
                title="Switch to another Go RFP"
              >
                Switch RFP
                <span className="proposal-go-picker-count">{goRfpCount}</span>
              </button>
            ) : null}
          </div>
        </div>
        <div
          className="flex min-h-[min(28rem,70vh)] flex-col items-center justify-center gap-4 px-6 py-12 text-center"
          role="status"
          aria-live="polite"
          aria-busy="true"
        >
          <span
            className="h-9 w-9 animate-spin rounded-full border-[3px] border-zo-border border-t-zo-orange"
            aria-hidden
          />
          <div className="space-y-1.5">
            <p className="text-sm font-semibold text-foreground">
              Loading proposal…
            </p>
            <p className="max-w-sm text-xs leading-relaxed text-zo-text-muted">
              Fetching your saved draft. Generated content appears here when
              load finishes — this is not an empty proposal.
            </p>
          </div>
        </div>
      </section>
    );
  }

  if (draftLoadState === "error") {
    return (
      <section className="proposal-workspace-card">
        <div className="proposal-workspace-chrome shrink-0 border-b border-zo-border/80 bg-white">
          <div className="flex items-center gap-3 px-3 py-2 md:px-4">
            <h2 className="min-w-0 flex-1 truncate text-sm font-semibold leading-tight text-foreground md:text-[0.95rem]">
              {rfp.title}
            </h2>
            {onOpenGoRfpPicker && goRfpCount ? (
              <button
                type="button"
                onClick={onOpenGoRfpPicker}
                className="proposal-go-picker-btn shrink-0"
                title="Switch to another Go RFP"
              >
                Switch RFP
                <span className="proposal-go-picker-count">{goRfpCount}</span>
              </button>
            ) : null}
          </div>
        </div>
        <div className="flex min-h-[min(28rem,70vh)] flex-col items-center justify-center gap-4 px-6 py-12 text-center">
          <p className="text-sm font-semibold text-foreground">
            Couldn’t load this proposal
          </p>
          <p className="max-w-md text-xs leading-relaxed text-zo-text-muted">
            {generateError ??
              "The server didn’t return the draft in time. Your content may still be saved — try again."}
          </p>
          <button
            type="button"
            className="zo-btn !px-4 !py-2 !text-xs"
            onClick={() => {
              setGenerateError(null);
              setDraftLoadState("loading");
            }}
          >
            Retry load
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="proposal-workspace-card">
      <div className="proposal-workspace-chrome shrink-0 border-b border-zo-border/80 bg-white">
        <div className="flex items-center gap-3 px-3 py-2 md:px-4">
          <h2 className="min-w-0 flex-1 truncate text-sm font-semibold leading-tight text-foreground md:text-[0.95rem]">
            {rfp.title}
          </h2>
          {anyPipelineRunning || isStopping ? (
            <button
              type="button"
              onClick={() => void handleStopPipeline()}
              disabled={isStopping}
              className="proposal-stop-generation-btn shrink-0"
              title="Stop the running build or Review & fix job"
            >
              {isStopping ? (
                <span
                  className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white"
                  aria-hidden
                />
              ) : (
                <span className="proposal-stop-generation-dot" aria-hidden />
              )}
              {isStopping ? "Stopping…" : "Stop Generation"}
            </button>
          ) : null}
          {onOpenGoRfpPicker && goRfpCount ? (
            <button
              type="button"
              onClick={onOpenGoRfpPicker}
              className="proposal-go-picker-btn shrink-0"
              title="Switch to another Go RFP"
            >
              Switch RFP
              <span className="proposal-go-picker-count">{goRfpCount}</span>
            </button>
          ) : null}
        </div>

        {manualFillCount === 0 && plainStatus.tone !== "good" ? (
          <p
            className={`proposal-header-status px-3 pb-1.5 text-xs leading-snug md:px-4 proposal-header-status--${plainStatus.tone}`}
          >
            {plainStatus.headline}
          </p>
        ) : null}

        {sectionProgress < 100 ? (
          <div className="flex items-center gap-2 px-3 pb-2 md:px-4">
            <div className="proposal-progress-track proposal-progress-track--slim min-w-0 flex-1">
              <div
                className="proposal-progress-fill"
                style={{ width: `${sectionProgress}%` }}
              />
            </div>
            <span className="shrink-0 text-[10px] font-medium tabular-nums text-zo-text-muted">
              {sectionProgress}%
            </span>
          </div>
        ) : null}

        {/* Server-derived completion banner — shows even after a page refresh
            (or for another user), because it comes from the checkpoint the
            Celery task wrote, not this browser session. Only when a run finished
            recently, is not currently running, wasn't just shown in-session, and
            hasn't been dismissed. */}
        {scanRecentlyCompleted &&
        !isFulfillingRfpGaps &&
        !fulfillJustCompleted &&
        !completionBannerDismissed ? (
          <div className="flex items-center gap-2 border-t border-emerald-200/80 bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-900 md:px-4">
            <span aria-hidden>✓</span>
            <span className="flex-1">
              Complete &amp; clean finished successfully — this draft is up to date.
            </span>
            <button
              type="button"
              onClick={() => setCompletionBannerDismissed(true)}
              className="shrink-0 rounded px-1.5 py-0.5 text-emerald-700 transition-smooth hover:bg-emerald-100"
              aria-label="Dismiss"
            >
              ✕
            </button>
          </div>
        ) : null}

        {buildPipelineComplete &&
        !isFullProposalRunning &&
        !isFulfillingRfpGaps &&
        !buildBannerDismissed ? (
          <div className="flex items-center gap-2 border-t border-emerald-200/80 bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-900 md:px-4">
            <span aria-hidden>✓</span>
            <span className="flex-1">
              Build my proposal already ran for this RFP (through final checks).
              {manualFillCount > 0
                ? ` ${manualFillCount} form or attachment item${manualFillCount === 1 ? "" : "s"} still need manual input — use the checklist on Review.`
                : " Open Review, then download Word."}
            </span>
            <button
              type="button"
              onClick={() => {
                setBuildBannerDismissed(true);
                try {
                  sessionStorage.setItem(buildBannerDismissKey(rfp.id), "1");
                } catch {
                  // ignore
                }
              }}
              className="shrink-0 rounded px-1.5 py-0.5 text-emerald-700 transition-smooth hover:bg-emerald-100"
              aria-label="Dismiss"
            >
              ✕
            </button>
          </div>
        ) : null}

        {(scanSummary || generateNotice || generateError || placeReport) && (
          <div>
            {/* Unmistakable success banner when a Complete & clean run just
                finished — shown ABOVE the results summary so the user always
                knows the run completed (vs got stuck). */}
            {fulfillJustCompleted && generateNotice ? (
              <div className="flex items-center gap-2 border-t border-emerald-200/80 bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-900 md:px-4">
                <span aria-hidden>✓</span>
                <span>{generateNotice}</span>
              </div>
            ) : null}
            {placeReport ? (
              <PacketPlaceReportBanner
                report={placeReport}
                onDismiss={() => setPlaceReport(null)}
              />
            ) : null}
            {scanSummary ? (
              <ScanRfpSummaryBanner
                summary={scanSummary}
                defaultExpanded={scanSummaryExpanded}
                onDismiss={() => {
                  setScanSummary(null);
                  setScanSummaryExpanded(false);
                }}
              />
            ) : null}
            {(generateNotice || generateError) &&
            !fulfillJustCompleted &&
            (!scanSummary ||
              isPlacingPacketContent ||
              isAligningRfpOutline) &&
            !placeReport ? (
              <div
                className={`border-t px-3 py-1.5 text-xs md:px-4 ${
                  generateNotice
                    ? "border-amber-200/80 bg-amber-50 text-amber-950"
                    : "border-red-200/80 bg-red-50 text-zo-error"
                }`}
              >
                {generateNotice ?? generateError}
              </div>
            ) : null}
            {generateError && (scanSummary || placeReport) ? (
              <div className="border-t border-red-200/80 bg-red-50 px-3 py-1.5 text-xs text-zo-error md:px-4">
                {generateError}
              </div>
            ) : null}
          </div>
        )}

        <div className="px-3 md:px-4">
          <OutlineTabs
            variant="underline"
            fullWidth
            tabs={workspaceTabs}
            activeTab={activeTab}
            onChange={(id) => {
              const next = id as WorkspaceTab;
              setActiveTab(next);
              if (next !== "outline") {
                setSectionChatReference(null);
              }
            }}
          />
        </div>
      </div>

      <ProposalManualFlagsPanel
        open={showManualFlags}
        flags={actionableFlags}
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

      {(isFullProposalRunning || isFulfillingRfpGaps) && (
        <QueuedJobBanner rfpId={rfp.id} />
      )}

      {/* The live scan/generation workflow now lives in the right-hand RFP
          Workflow rail (status, activity, and every step). The old full-width
          top strip was a duplicate of it, so it is intentionally not rendered
          here anymore — reclaiming the vertical space above the editor. */}

      {rfpCost && activeTab !== "content" ? (
        <div className="border-b border-zo-border/70 bg-[#fafbfc] px-3 py-2 md:px-4">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-zo-text-muted">
            <span className="font-semibold text-foreground">
              LLM cost: {fmtUsd(rfpCost.totalCostUsd)}
            </span>
            <span>Generate: {fmtUsd(costByRunType.generate)}</span>
            <span>Complete Scan: {fmtUsd(costByRunType.completeScan)}</span>
            <span>Chat edits: {fmtUsd(costByRunType.chat)}</span>
            <span>{rfpCost.callCount.toLocaleString()} calls</span>
            <span>{rfpCost.runCount} runs</span>
          </div>
          {rfpCost.byNode.length > 0 ? (
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-zo-text-muted">
              {rfpCost.byNode.slice(0, 6).map((row) => (
                <span key={row.nodeName}>
                  {row.nodeName}: {fmtUsd(row.costUsd)}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="proposal-workspace-body">
      {/* Outline tab */}
      <TabPanel id="outline" activeTab={activeTab} className="proposal-workspace-tab">
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="proposal-tab-actions flex shrink-0 flex-wrap items-center justify-end gap-2 border-b border-zo-border/60 px-3 py-2">
            <span className="mr-auto text-[13px] font-semibold tabular-nums text-zo-text-muted">
              {manuscriptProgress.complete}/{manuscriptProgress.total} drafted
            </span>
            <div className="proposal-tab-actions-toolbar">
            <label className="proposal-snapshot-field">
              <CapabilityHoverTip id="savedVersion" side="bottom">
                <span className="proposal-snapshot-field-label">
                  Saved version
                </span>
              </CapabilityHoverTip>
              <span className="proposal-snapshot-field-control">
                <CapabilityHoverTip id="savedVersion" side="bottom">
                  <select
                    value={restoreSnapshotAt}
                    onChange={(e) =>
                      handleSnapshotDropdownChange(e.target.value)
                    }
                    disabled={
                      isRestoringSnapshot ||
                      anyPipelineRunning ||
                      (outline.snapshots?.length ?? 0) === 0
                    }
                    className="proposal-snapshot-select"
                    aria-label="Choose a saved proposal version"
                    aria-busy={isRestoringSnapshot}
                  >
                    {(outline.snapshots?.length ?? 0) === 0 ? (
                      <option value="">No versions yet</option>
                    ) : (
                      [...(outline.snapshots ?? [])].reverse().map((snap) => (
                        <option key={snap.savedAt} value={snap.savedAt}>
                          {snap.label}
                          {" · "}
                          {new Date(snap.savedAt).toLocaleString(undefined, {
                            month: "short",
                            day: "numeric",
                            hour: "numeric",
                            minute: "2-digit",
                          })}
                        </option>
                      ))
                    )}
                  </select>
                </CapabilityHoverTip>
              </span>
              <CapabilityHoverTip id="restore" side="bottom">
                <button
                  type="button"
                  className="proposal-tab-text-btn"
                  disabled={
                    !restoreSnapshotAt ||
                    isRestoringSnapshot ||
                    anyPipelineRunning ||
                    (outline.snapshots?.length ?? 0) === 0
                  }
                  onClick={() => void handleRestoreSnapshot()}
                >
                  {isRestoringSnapshot ? "Restoring…" : "Restore"}
                </button>
              </CapabilityHoverTip>
            </label>
            <div className="flex shrink-0 items-center gap-1.5">
              <CapabilityHoverTip id="matchStudies" side="bottom">
                <button
                  type="button"
                  onClick={() => void handleMatchCaseStudies()}
                  disabled={anyPipelineRunning || isMatchingCaseStudies}
                  className="inline-flex min-h-[2.125rem] items-center gap-1.5 rounded-lg border border-[#ef5018]/30 bg-[#ef5018]/10 px-2.5 py-1.5 text-xs font-semibold text-[#ef5018] hover:bg-[#ef5018]/15 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <svg
                    className="h-3.5 w-3.5 shrink-0"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}
                    aria-hidden
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z"
                    />
                  </svg>
                  {isMatchingCaseStudies ? "Matching…" : "Match studies"}
                </button>
              </CapabilityHoverTip>
              <CapabilityHoverTip id="moreMenu" side="bottom">
                <span className="inline-flex">
                  <ProposalTabMoreMenu
                    disabled={anyPipelineRunning}
                    items={[
                      {
                        id: "designer-compact",
                        label: "Designer-compact all",
                        title: "Rewrite overlong tabs as designer-ready tables/bullets",
                        disabled:
                          anyPipelineRunning ||
                          isDesignerCompacting ||
                          manuscriptProgress.complete === 0,
                        onClick: () => void handleDesignerCompactAll(),
                      },
                      {
                        id: "reset",
                        label: "Reset draft",
                        disabled: isResettingDraft,
                        tone: "danger",
                        onClick: () => setResetConfirmOpen(true),
                      },
                      {
                        id: "from-case-studies",
                        label: "Start from Case Studies",
                        title: "Keeps Company + Team Bios; re-extracts case studies",
                        disabled: anyPipelineRunning,
                        onClick: () =>
                          requireKeyPersonas(() =>
                            void handleGenerateFullProposal({ startFromCaseStudies: true })
                          ),
                      },
                      {
                        id: "from-intelligence",
                        label: "Start from Intelligence",
                        title: "Keeps Sections 1–3; rebuilds Intelligence onward",
                        disabled: anyPipelineRunning,
                        onClick: () =>
                          requireKeyPersonas(() =>
                            void handleGenerateFullProposal({ startAfterSections1to3: true })
                          ),
                      },
                    ]}
                  />
                </span>
              </CapabilityHoverTip>
            </div>
            <CapabilityHoverTip id="keyPersonas" side="bottom">
              <span className="inline-flex">
                <KeyPersonasBox
                  rfpId={rfp.id}
                  initialSelectedIds={outline.selectedKeyPersonas || []}
                  onSelectionChange={handleKeyPersonasChange}
                  onDraftSynced={handlePersonasDraftSynced}
                />
              </span>
            </CapabilityHoverTip>
            {/* Gate shown when generation is attempted with no personas chosen.
                Separate instance from KeyPersonasBox so its open state is
                driven by the generate action rather than by the toolbar. */}
            <KeyPersonasModal
              isOpen={personaGateOpen}
              onClose={closePersonaGate}
              rfpId={rfp.id}
              initialSelectedIds={outline.selectedKeyPersonas || []}
              onSelectionChange={handleKeyPersonasChange}
              onDraftSynced={handlePersonasDraftSynced}
            />
            <CapabilityHoverTip id="generateProposal" side="bottom">
              <button
                type="button"
                onClick={() => requireKeyPersonas(() => void handlePrimaryPipeline())}
                disabled={anyPipelineRunning}
                className="zo-btn proposal-toolbar-btn disabled:opacity-60"
              >
                {isFullProposalRunning || isFulfillingRfpGaps ? (
                  <>
                    <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-zo-white/30 border-t-zo-white" />
                    {primaryPipelineLabel}
                  </>
                ) : (
                  primaryPipelineLabel
                )}
              </button>
            </CapabilityHoverTip>
            </div>
          </div>

          <div
            className="proposal-outline-layout grid min-h-0 min-w-0 flex-1 overflow-hidden"
          >
          <div className="proposal-section-list flex min-h-0 min-w-0 flex-col overflow-hidden rounded-none border-b border-zo-border lg:rounded-2xl lg:border lg:border-zo-border/80">
            <div className="flex shrink-0 items-center justify-between border-b border-zo-border/60 px-3 py-2.5">
              <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-zo-text-muted">
                Sections
              </p>
            </div>
            <ul className="custom-scrollbar min-h-0 flex-1 overflow-x-hidden overflow-y-auto">
              <ProposalSectionTree
                sections={outline.sections}
                manuscriptIndexById={manuscriptIndexById}
                selectedSectionId={selectedSectionId}
                highlightedSectionId={highlightedSectionId}
                manualFillFlags={actionableFlags}
                sectionRevisions={sectionRevisions}
                sectionButtonRefs={sectionButtonRefs}
                onSelectSection={selectSection}
                onOpenRevision={(sectionId) => {
                  selectSection(sectionId);
                  setRevisionDrawerSectionId(sectionId);
                }}
                onDeleteSection={(id) => void removeSection(id)}
                onReorderSection={
                  anyPipelineRunning ? undefined : reorderSectionByDrag
                }
              />
            </ul>

            <div className="shrink-0 border-t border-zo-border bg-[var(--zo-input-bg)] p-2">
              <div className="flex min-w-0 items-stretch gap-1.5">
                <input
                  type="text"
                  value={newSectionTitle}
                  onChange={(e) => setNewSectionTitle(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && addCustomSection()}
                  placeholder="New section title…"
                  className="min-w-0 flex-1 zo-input px-2.5 py-2 text-sm outline-none transition-smooth focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10"
                />
                <button
                  type="button"
                  onClick={addCustomSection}
                  className="zo-btn shrink-0 !px-2.5 !py-2"
                >
                  Add
                </button>
              </div>
            </div>
          </div>

          <div className="proposal-editor-pane flex min-h-0 min-w-0 flex-col overflow-hidden rounded-none lg:rounded-2xl lg:border lg:border-zo-border/80">
            {selectedSection ? (
              <>
                <div className="proposal-editor-chrome">
                  <div className="proposal-editor-chrome-row">
                    <span className="shrink-0 text-[10px] font-bold tabular-nums text-zo-text-muted">
                      {manuscriptIndexById.get(selectedSection.id) != null
                        ? `Section ${manuscriptIndexById.get(selectedSection.id)} of ${manuscriptProgress.total}`
                        : "Section"}
                    </span>
                    <input
                      type="text"
                      value={selectedSection.title}
                      onChange={(e) =>
                        updateSection(selectedSection.id, {
                          title: e.target.value,
                        })
                      }
                      className="proposal-editor-chrome-title"
                      aria-label="Section title"
                    />
                    <div className="ml-auto flex shrink-0 flex-wrap items-center gap-1">
                      <IconButton
                        onClick={() => void removeSection(selectedSection.id)}
                        label="Remove section"
                        variant="danger"
                      >
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      </IconButton>
                    </div>
                  </div>
                </div>

                <div className="proposal-editor-split">
                  <div ref={editorScrollRef} className="proposal-editor-body">
                    <DraftSectionEditor
                      key={selectedSection.id}
                      section={selectedSection}
                      wordCount={countWords(selectedSection.content)}
                      disabled={anyPipelineRunning}
                      chatBusy={sectionChatBusy}
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
                      onOpenRevisionChat={(request) => {
                        openSectionChat(request);
                      }}
                      storedRevision={
                        selectedSection
                          ? sectionRevisions[selectedSection.id] ?? null
                          : null
                      }
                      revisionDrawerOpen={
                        revisionDrawerSectionId === selectedSection?.id
                      }
                      onRevisionDrawerOpenChange={(open) =>
                        setRevisionDrawerSectionId(
                          open ? selectedSection.id : null
                        )
                      }
                    />
                  </div>
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

          <div
            ref={assistantPaneRef}
            className="proposal-assistant-pane flex min-h-0 min-w-0 flex-col overflow-hidden rounded-none border-t border-zo-border lg:rounded-2xl lg:border lg:border-zo-border/80"
          >
            <ProposalSectionChatPanel
              rfpId={rfp.id}
              sections={outline.sections}
              viewingSectionId={assistantViewSectionId}
              disabled={anyPipelineRunning}
              reference={sectionChatReference}
              onSetReference={setSectionChatReference}
              messages={sectionChatMessages}
              onMessagesChange={setSectionChatMessages}
              onSectionUpdated={applySectionImproveFromServer}
              onRevisionRecorded={(sectionId, revision) =>
                recordSectionRevision(sectionId, revision)
              }
              onRevisionDrawerOpenChange={(sectionId, open) => {
                if (open) {
                  selectSection(sectionId);
                }
                setRevisionDrawerSectionId(open ? sectionId : null);
              }}
              onFocusSection={(sectionId) => {
                selectSection(sectionId);
              }}
              onBusyChange={setSectionChatBusy}
            />
          </div>
        </div>
        </div>
      </TabPanel>

      {/* Content tab */}
      <TabPanel id="content" activeTab={activeTab} className="proposal-workspace-tab proposal-workspace-tab--natural">
        {outline.sections.some((s) => s.content.trim()) ||
        isFullProposalRunning ||
        serverPipelineActive ? (
            <div className="proposal-content-tab-shell flex min-h-0 flex-1 flex-col">
            <div className="proposal-tab-actions flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-zo-border/60 px-3 py-2.5">
              <p className="text-xs text-zo-text-muted">
                After Build my proposal, use the Checklist. Review & fix is optional after edits.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <CapabilityHoverTip id="completeClean" side="bottom">
                  <button
                    type="button"
                    onClick={() => void handleFulfillRfpGaps()}
                    disabled={
                      anyPipelineRunning ||
                      !outline.sections.some((s) => s.content.trim())
                    }
                    className="zo-btn secondary !py-2 !px-3 !text-sm disabled:opacity-40"
                  >
                    {isFulfillingRfpGaps
                      ? "Review & fix…"
                      : scanAlreadyDone && !canResumeFulfillScan
                        ? "✓ Review & fix done"
                        : canResumeFulfillScan
                          ? "Continue Review & fix"
                          : "Review & fix"}
                  </button>
                </CapabilityHoverTip>
                <details
                  className="relative"
                  open={
                    isAligningRfpOutline ||
                    isPlacingPacketContent ||
                    undefined
                  }
                >
                  <summary className="zo-btn secondary !py-2 !px-3 !text-sm cursor-pointer list-none select-none [&::-webkit-details-marker]:hidden">
                    Staff tools
                  </summary>
                  <div className="absolute right-0 top-[calc(100%+0.35rem)] z-30 flex min-w-[16rem] flex-wrap items-center gap-2 rounded-lg border border-zo-border bg-white p-2.5 shadow-lg">
                    <MatchRfpPacketControl
                      disabled={
                        !outline.sections.some((s) => s.content.trim()) ||
                        anyPipelineRunning
                      }
                      isOrdering={isAligningRfpOutline || alignPreviewLoading}
                      isPlacing={isPlacingPacketContent || placePreviewLoading}
                      onOrderTabs={() => void handleAlignRfpOutline()}
                      onPlaceContent={() => void handlePlacePacketContent()}
                    />
                  </div>
                </details>
                {outline.lastFulfillReport ? (
                  <button
                    type="button"
                    className="zo-btn secondary !py-2 !px-3 !text-sm"
                    title="Re-open the last Review & fix results panel"
                    onClick={handleOpenLastResults}
                  >
                    {scanSummary ? "Results" : "Last results"}
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() => setShowManualFlags((open) => !open)}
                  className={`proposal-checklist-btn ${
                    manualFillCount > 0
                      ? "proposal-checklist-btn--alert"
                      : "proposal-checklist-btn--idle"
                  } ${showManualFlags ? "is-open" : ""}`}
                >
                  Checklist
                  {manualFillCount > 0 ? (
                    <span className="proposal-checklist-count">{manualFillCount}</span>
                  ) : null}
                </button>
              </div>
            </div>
            <div className="proposal-content-jump-strip custom-scrollbar" aria-label="Jump to section">
              {manuscriptSections.map((section, index) => (
                <button
                  key={section.id}
                  type="button"
                  className={`proposal-content-jump-chip ${
                    highlightedSectionId === section.id ||
                    selectedSectionId === section.id
                      ? "is-active"
                      : ""
                  }`}
                  title={section.title}
                  onClick={() => scrollToManuscriptSection(section.id)}
                >
                  {index + 1}. {section.title}
                </button>
              ))}
            </div>
            {(outline.snapshots?.length ?? 0) > 0 && selectedSnapshotForCompare ? (
              <details
                ref={compareDetailsRef}
                className="mx-3 mb-2 shrink-0 rounded-lg border border-zo-border/70 bg-[#fafbfc] px-3 py-2"
              >
                <summary className="cursor-pointer text-xs font-semibold text-foreground">
                  Compare to saved version ({selectedSnapshotForCompare.label})
                </summary>
                <div className="mt-2">
                  <ProposalVersionCompare
                    rfpId={rfp.id}
                    selectedSnapshot={selectedSnapshotForCompare}
                    currentSections={outline.sections}
                    onJumpToSection={handleCompareJumpToSection}
                  />
                </div>
              </details>
            ) : null}
            <div className={`proposal-workflow-layout ${reviewFocusMode ? "is-review-focus" : ""}`}>
            <aside className="proposal-review-sections proposal-section-list flex min-h-0 min-w-0 flex-col overflow-hidden">
              <div className="flex shrink-0 items-center justify-between border-b border-zo-border/60 px-3 py-2.5">
                <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-zo-text-muted">
                  Sections
                </p>
              </div>
              <div className="shrink-0 border-b border-zo-border/60 p-2">
                <div className="relative w-full">
                  <svg
                    className="pointer-events-none absolute left-2.5 top-1/2 z-[1] h-3.5 w-3.5 -translate-y-1/2 text-zo-text-muted"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}
                    aria-hidden
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z"
                    />
                  </svg>
                  <input
                    type="search"
                    value={reviewSectionQuery}
                    onChange={(e) => setReviewSectionQuery(e.target.value)}
                    placeholder="Search sections…"
                    className="zo-input w-full rounded-lg py-1.5 pl-8 pr-2.5 text-xs outline-none transition-smooth focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10 [&::-webkit-search-cancel-button]:hidden"
                  />
                </div>
              </div>
              <ul className="custom-scrollbar min-h-0 flex-1 overflow-x-hidden overflow-y-auto">
                <ProposalSectionTree
                  sections={reviewSections}
                  manuscriptIndexById={manuscriptIndexById}
                  selectedSectionId={selectedSectionId}
                  highlightedSectionId={highlightedSectionId}
                  manualFillFlags={actionableFlags}
                  sectionRevisions={sectionRevisions}
                  sectionButtonRefs={sectionButtonRefs}
                  onSelectSection={scrollToManuscriptSection}
                  onOpenRevision={(sectionId) => {
                    scrollToManuscriptSection(sectionId);
                    setRevisionDrawerSectionId(sectionId);
                  }}
                  onReorderSection={
                    anyPipelineRunning || reviewSectionQuery.trim()
                      ? undefined
                      : reorderSectionByDrag
                  }
                />
              </ul>
            </aside>
            <div className="proposal-review-main flex min-h-0 min-w-0 flex-col overflow-hidden">
            <ProposalReviewToolbar
              textareaRef={activeSectionTextareaRef}
              content={activeReviewMarkdown}
              disabled={anyPipelineRunning || !activeReviewSection}
              lastSavedAt={lastSavedAt}
              onComment={handleReviewComment}
              expanded={reviewFocusMode}
              onToggleExpanded={() => setReviewFocusMode((v) => !v)}
              ensureEditable={ensureReviewEditable}
              showFormattedView={() => setEditingSectionId(null)}
              previewSelection={reviewPreviewSelection}
              onPreviewSelectionConsumed={() => setReviewPreviewSelection(null)}
              onChange={(next) =>
                activeReviewSection &&
                updateSection(activeReviewSection.id, {
                  content: next,
                  status: next ? "generated" : "outline",
                })
              }
            />
            <ManuscriptSelectionBubble
              active={Boolean(
                reviewPreviewSelection &&
                  reviewPreviewSelection.end > reviewPreviewSelection.start &&
                  activeReviewSection
              )}
              disabled={anyPipelineRunning || !activeReviewSection}
              onBold={() => {
                if (!activeReviewSection || !reviewPreviewSelection) return;
                const { next } = toggleWrapMarkers(
                  activeReviewMarkdown,
                  reviewPreviewSelection.start,
                  reviewPreviewSelection.end,
                  "**"
                );
                updateSection(activeReviewSection.id, {
                  content: next,
                  status: next ? "generated" : "outline",
                });
                setReviewPreviewSelection(null);
                setEditingSectionId(null);
              }}
              onItalic={() => {
                if (!activeReviewSection || !reviewPreviewSelection) return;
                const { next } = toggleWrapMarkers(
                  activeReviewMarkdown,
                  reviewPreviewSelection.start,
                  reviewPreviewSelection.end,
                  "_"
                );
                updateSection(activeReviewSection.id, {
                  content: next,
                  status: next ? "generated" : "outline",
                });
                setReviewPreviewSelection(null);
                setEditingSectionId(null);
              }}
              onAskToChange={() => {
                if (!activeReviewSection) return;
                const selected =
                  reviewPreviewSelection != null
                    ? activeReviewMarkdown.slice(
                        reviewPreviewSelection.start,
                        reviewPreviewSelection.end
                      )
                    : null;
                handleReviewComment(selected);
              }}
            />
            <div className="proposal-content-layout flex-1 min-h-0">
            <div
              ref={contentScrollRef}
              className="proposal-content-scroll proposal-content-manuscript-pane proposal-review-manuscript custom-scrollbar min-h-0"
            >
              {(isFullProposalRunning || fullProposalProgress === "sections-1-3") &&
              fullProposalProgress === "sections-1-3" ? (
                <div className="rounded-xl border border-zo-orange/30 bg-[#ef5018]/08 px-4 py-3 text-sm text-foreground">
                  <span className="font-semibold text-zo-orange">Drafting live</span>
                  {" — "}
                  {liveLatestSectionTitle
                    ? `Latest: ${liveLatestSectionTitle}`
                    : "Section stubs ready. Subsections appear here as each agent finishes."}
                  {liveGeneratedCount > 0 ? (
                    <span className="ml-1 text-zo-text-muted">
                      ({liveGeneratedCount} with content)
                    </span>
                  ) : null}
                </div>
              ) : null}
              {manuscriptSections.map((section, index) =>
                  isSectionDrafted(section.content) ? (
                  section.id === activeReviewSectionId ? (
                  <article
                    key={section.id}
                    id={section.id}
                    className="proposal-content-article proposal-content-article--read scroll-mt-24 bg-[rgba(17,24,39,0.015)]"
                  >
                    <h3 className="proposal-content-section-title">
                      <span className="text-zo-text-muted">{index + 1}.</span>{" "}
                      {section.title}
                      {sectionManualFillCount(section.id, actionableFlags) > 0 ? (
                        <span className="ml-2 text-[11px] font-medium text-amber-800">
                          · needs input
                        </span>
                      ) : null}
                      <button
                        type="button"
                        onClick={() =>
                          setEditingSectionId((cur) =>
                            cur === section.id ? null : section.id
                          )
                        }
                        disabled={anyPipelineRunning}
                        className="ml-2 align-middle text-[11px] font-semibold text-zo-orange transition-smooth hover:underline disabled:opacity-50"
                      >
                        {editingSectionId === section.id ? "Done editing" : "Edit source"}
                      </button>
                    </h3>
                    <div
                      className="proposal-prose proposal-prose--manuscript mt-4 selection:bg-blue-500/20 selection:text-inherit"
                      onMouseUp={() => {
                        if (editingSectionId !== section.id) {
                          captureReviewPreviewSelection();
                        }
                      }}
                    >
                      {editingSectionId === section.id ? (
                        <textarea
                          ref={(el) => registerManuscriptTextarea(section.id, el)}
                          value={stripLeadingTitleEcho(section.content, section.title)}
                          disabled={anyPipelineRunning}
                          onFocus={(e) => handleManuscriptTextareaFocus(section.id, e)}
                          onInput={(e) => resizeManuscriptTextarea(e.currentTarget)}
                          onSelect={(e) => {
                            const el = e.currentTarget;
                            if (el.selectionStart !== el.selectionEnd) {
                              setReviewPreviewSelection({
                                start: el.selectionStart,
                                end: el.selectionEnd,
                              });
                            }
                          }}
                          onChange={(e) =>
                            updateSection(section.id, {
                              content: e.target.value,
                              status: e.target.value ? "generated" : "outline",
                            })
                          }
                          className="proposal-review-inline-textarea"
                        />
                      ) : (
                        // Default when a section is selected: FORMATTED render,
                        // never raw markdown. Raw source shows only after the
                        // user clicks "Edit source" above.
                        <MarkdownReportBody
                          body={stripLeadingTitleEcho(section.content, section.title)}
                          variant="document"
                          highlightTexts={
                            activeSubmissionFlag?.sectionId === section.id &&
                            activeFlagHighlight
                              ? [activeFlagHighlight.text]
                              : []
                          }
                        />
                      )}
                    </div>
                  </article>
                  ) : (
                  <article
                    key={section.id}
                    id={section.id}
                    className={`proposal-content-article proposal-content-article--read scroll-mt-24 ${
                      highlightedSectionId === section.id ? "is-flag-target" : ""
                    }`}
                  >
                    <h3 className="proposal-content-section-title">
                      <span className="text-zo-text-muted">{index + 1}.</span>{" "}
                      {section.title}
                      {sectionManualFillCount(section.id, actionableFlags) > 0 ? (
                        <span className="ml-2 text-[11px] font-medium text-amber-800">
                          · needs input
                        </span>
                      ) : null}
                    </h3>
                    <div
                      className="proposal-prose proposal-prose--manuscript proposal-prose--editable mt-4"
                      title="Click to edit this section"
                      onClick={() => {
                        setFocusedSectionId(section.id);
                        setEditingSectionId(null);
                      }}
                    >
                      <MarkdownReportBody
                        body={stripLeadingTitleEcho(section.content, section.title)}
                        variant="document"
                        highlightTexts={
                          activeSubmissionFlag?.sectionId === section.id && activeFlagHighlight
                            ? [activeFlagHighlight.text]
                            : []
                        }
                      />
                    </div>
                  </article>
                  )
                  ) : (
                  <article
                    key={section.id}
                    id={section.id}
                    className={`proposal-content-article scroll-mt-32 border border-dashed border-zo-border/80 bg-[var(--zo-input-bg)]/40 opacity-90 ${
                      highlightedSectionId === section.id ? "is-flag-target" : ""
                    }`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-3">
                        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-zo-border bg-white text-sm font-bold text-zo-text-muted">
                          {index + 1}
                        </span>
                        <div className="min-w-0">
                          <h3 className="text-[1.05rem] font-bold leading-tight tracking-tight text-foreground">
                            {section.title}
                          </h3>
                          <p className="mt-0.5 text-[11px] font-medium text-zo-orange">
                            {isFullProposalRunning ? "Generating…" : "Not drafted yet"}
                          </p>
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <SectionStatusPill status={section.status || "outline"} />
                        <button
                          type="button"
                          className="zo-btn !px-3 !py-1.5 !text-xs"
                          onClick={() => selectSection(section.id)}
                        >
                          Open editor
                        </button>
                      </div>
                    </div>
                  </article>
                  )
                )}
            </div>
          </div>
            </div>

            <ProposalWorkflowRail
              checkpoint={research?.pipelineCheckpoint}
              isRunning={anyPipelineRunning}
              fullProposalPhase={effectiveFullProposalProgress}
              isFulfillScanRunning={isFulfillingRfpGaps}
              isAlignRunning={isAligningRfpOutline}
              isPlaceRunning={isPlacingPacketContent}
              hasCompletedFulfillReport={Boolean(outline.lastFulfillReport)}
              buildPipelineComplete={buildPipelineComplete}
              manualFillCount={manualFillCount}
              rfpCost={rfpCost}
              costByRunType={costByRunType}
              fmtUsd={fmtUsd}
              canCompareToSaved={Boolean((outline.snapshots?.length ?? 0) > 0 && selectedSnapshotForCompare)}
              onCompareToSaved={handleOpenCompareToSaved}
              canViewLastResults={Boolean(outline.lastFulfillReport)}
              onViewLastResults={handleOpenLastResults}
              goRfpCount={goRfpCount}
              onOpenGoRfpPicker={onOpenGoRfpPicker}
            />
            </div>
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
              {serverPipelineActive && !isFullProposalRunning
                ? `Generation already in progress — ${
                    inProgressPhaseLabel(
                      research!.pipelineCheckpoint!.inProgressPhase!
                    )
                  }. Wait for it to finish instead of starting another run.`
                : "Generate the full proposal (Sections 1–3 + RFP-specific sections)."}
            </p>
            <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
              <CapabilityHoverTip id="generateProposal" side="bottom">
                <button
                  type="button"
                  onClick={() => requireKeyPersonas(() => void handleGenerateFullProposal())}
                  disabled={anyPipelineRunning}
                  className="zo-btn disabled:opacity-60"
                >
                  Generate Proposal
                </button>
              </CapabilityHoverTip>
              {anyPipelineRunning || isStopping ? (
                <button
                  type="button"
                  onClick={() => void handleStopPipeline()}
                  disabled={isStopping}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-red-300 bg-white px-5 py-2.5 text-sm font-semibold text-red-700 transition-smooth hover:bg-red-50 disabled:opacity-70"
                >
                  {isStopping ? (
                    <>
                      <span
                        className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-red-300 border-t-red-600"
                        aria-hidden
                      />
                      Stopping…
                    </>
                  ) : (
                    "Stop"
                  )}
                </button>
              ) : null}
            </div>
          </div>
        )}
      </TabPanel>


      {/* Export tab */}
      <TabPanel id="export" activeTab={activeTab} className="proposal-workspace-tab proposal-workspace-tab--natural">
        <div className="proposal-submit-tab flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="grid min-h-0 flex-1 gap-0 overflow-hidden lg:grid-cols-[minmax(0,1fr)_minmax(16rem,20rem)]">
            <div className="flex min-h-0 min-w-0 flex-col overflow-hidden border-b border-zo-border lg:border-b-0 lg:border-r">
              <div className="flex shrink-0 items-center justify-between gap-2 border-b border-zo-border/60 px-3 py-2 md:px-4">
                <p className="text-xs font-semibold uppercase tracking-wide text-zo-text-muted">
                  Full proposal preview
                </p>
                <span className="text-[11px] tabular-nums text-zo-text-muted">
                  {manuscriptProgress.complete}/{manuscriptProgress.total} sections
                </span>
              </div>
              {fullManuscript ? (
                <div className="proposal-content-layout flex-1 min-h-0">
                  <nav className="proposal-on-page-nav" aria-label="Jump to section">
                    <p className="proposal-on-page-nav-label text-[10px] font-semibold text-zo-text-muted">
                      Jump to
                    </p>
                    <ul className="proposal-on-page-nav-list mt-2 space-y-0.5">
                      {manuscriptSections.map((section, index) => (
                        <li key={section.id}>
                          <button
                            type="button"
                            className={`proposal-on-page-link w-full text-left ${
                              highlightedSectionId === section.id ||
                              selectedSectionId === section.id
                                ? "is-active"
                                : ""
                            }`}
                            title={section.title}
                            onClick={() => scrollToManuscriptSection(section.id)}
                          >
                            <span className="proposal-on-page-num">{index + 1}</span>
                            <span className="proposal-on-page-title">
                              {section.title}
                              {!isSectionDrafted(section.content) ? " · …" : ""}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </nav>
                  <div
                    ref={submitScrollRef}
                    className="proposal-content-scroll proposal-content-manuscript-pane proposal-review-manuscript custom-scrollbar min-h-0"
                  >
                    {manuscriptSections.map((section, index) =>
                      isSectionDrafted(section.content) ? (
                        <article
                          key={section.id}
                          id={section.id}
                          className={`proposal-content-article proposal-content-article--read scroll-mt-24 ${
                            highlightedSectionId === section.id ? "is-flag-target" : ""
                          }`}
                        >
                          <h3 className="proposal-content-section-title">
                            <span className="text-zo-text-muted">{index + 1}.</span>{" "}
                            {section.title}
                          </h3>
                          <div className="proposal-prose proposal-prose--manuscript mt-4">
                            <MarkdownReportBody
                              body={stripLeadingTitleEcho(section.content, section.title)}
                              variant="document"
                            />
                          </div>
                        </article>
                      ) : (
                        <article
                          key={section.id}
                          id={section.id}
                          className={`proposal-content-article scroll-mt-32 border border-dashed border-zo-border/80 bg-[var(--zo-input-bg)]/40 opacity-90 ${
                            highlightedSectionId === section.id ? "is-flag-target" : ""
                          }`}
                        >
                          <div className="flex flex-wrap items-center justify-between gap-3">
                            <div className="flex min-w-0 items-center gap-3">
                              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-zo-border bg-white text-sm font-bold text-zo-text-muted">
                                {index + 1}
                              </span>
                              <div className="min-w-0">
                                <h3 className="text-[1.05rem] font-bold leading-tight tracking-tight text-foreground">
                                  {section.title}
                                </h3>
                                <p className="mt-0.5 text-[11px] font-medium text-zo-orange">
                                  Not drafted yet
                                </p>
                              </div>
                            </div>
                            <button
                              type="button"
                              className="zo-btn !px-3 !py-1.5 !text-xs"
                              onClick={() => selectSection(section.id)}
                            >
                              Open editor
                            </button>
                          </div>
                        </article>
                      )
                    )}
                  </div>
                </div>
              ) : (
                <div className="flex min-h-[16rem] flex-1 flex-col items-center justify-center px-4 text-center">
                  <p className="text-sm text-zo-text-muted">
                    Build my proposal first, then preview and export
                    it here.
                  </p>
                </div>
              )}
            </div>

            <aside className="flex shrink-0 flex-col gap-4 overflow-y-auto bg-[#fafbfc] px-4 py-4 md:px-5">
              <div>
                <h3 className="font-heading text-base font-bold text-foreground">
                  Finish &amp; export
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-zo-text-muted">
                  Read the manuscript, then download Word for layout and PDF.
                </p>
              </div>

              <div className="space-y-2">
                <button
                  type="button"
                  onClick={() => void handleDownloadDocx()}
                  disabled={
                    !fullManuscript || isDownloadingDocx || anyPipelineRunning
                  }
                  className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-[#0b2f6b] bg-[#0b2f6b] px-4 py-3 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-[#0a2758] disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {isDownloadingDocx
                    ? "Preparing Word file…"
                    : docxDownloaded
                      ? "Download started"
                      : "Download Word (.docx)"}
                </button>
                <p className="text-[11px] leading-relaxed text-zo-text-muted">
                  Same headings, lists, tables, and designer notes as the preview
                  — opens in Microsoft Word or Google Docs.
                </p>

                {docxDownloadError ? (
                  <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs leading-relaxed text-rose-800">
                    {docxDownloadError}
                  </p>
                ) : null}
              </div>

              {gapResolveNotice ? (
                <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs leading-relaxed text-emerald-900">
                  {gapResolveNotice}
                </p>
              ) : null}
              {gapResolveError ? (
                <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs leading-relaxed text-rose-800">
                  {gapResolveError}
                </p>
              ) : null}
            </aside>
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
                className="proposal-revision-drawer overflow-hidden"
                style={{ maxHeight: "100dvh" }}
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
                  showReapply={
                    !!revisionDrawerSection &&
                    (revisionDrawerSection.content || "") !==
                      (activeRevision.after || "") &&
                    (activeRevision.after || "").trim().length > 0
                  }
                  onReapply={() => {
                    if (!revisionDrawerSectionId || !activeRevision.after) return;
                    setOutline((prev) => ({
                      ...prev,
                      sections: prev.sections.map((s) =>
                        s.id === revisionDrawerSectionId
                          ? {
                              ...s,
                              content: activeRevision.after,
                              status: "generated" as const,
                            }
                          : s
                      ),
                      updatedAt: new Date().toISOString(),
                    }));
                    setRevisionDrawerSectionId(null);
                  }}
                  onDismiss={() => dismissSectionRevision(revisionDrawerSectionId)}
                />
              </div>
            </>,
            document.body
          )
        : null}
      {resetConfirmOpen && typeof document !== "undefined"
        ? createPortal(
            <div
              className="fixed inset-0 z-[200] flex items-center justify-center p-4 sm:p-6"
              role="dialog"
              aria-modal="true"
              aria-labelledby="reset-draft-title"
            >
              <button
                type="button"
                className="absolute inset-0 bg-slate-900/20 backdrop-blur-[2px]"
                aria-label="Close reset confirmation"
                disabled={isResettingDraft}
                onClick={() => !isResettingDraft && setResetConfirmOpen(false)}
              />
              <div className="relative z-10 w-full max-w-md rounded-2xl border border-zo-border bg-white p-6 shadow-[0_24px_64px_rgba(15,23,42,0.12)]">
                <h2
                  id="reset-draft-title"
                  className="font-heading text-lg font-bold text-foreground"
                >
                  Reset outline and clear all generated content?
                </h2>
                <p className="mt-2 text-sm leading-relaxed text-zo-text-secondary">
                  This will:
                </p>
                <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-zo-text-secondary">
                  <li>Clear the live draft sections</li>
                  <li>
                    Delete pipeline checkpoints and research cache from Supabase
                  </li>
                  <li>Cancel any generation currently running</li>
                </ul>
                <p className="mt-3 text-sm leading-relaxed text-zo-text-muted">
                  Use Saved version to load an earlier checkpoint if one exists.
                </p>
                <div className="mt-6 flex flex-wrap justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => setResetConfirmOpen(false)}
                    disabled={isResettingDraft}
                    className="zo-btn secondary !py-2.5 disabled:opacity-50"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleResetOutline()}
                    disabled={isResettingDraft}
                    className="zo-btn !border-zo-danger !bg-zo-danger !py-2.5 hover:!bg-red-700 disabled:opacity-50"
                  >
                    {isResettingDraft ? "Resetting…" : "Reset draft"}
                  </button>
                </div>
              </div>
            </div>,
            document.body
          )
        : null}
      <CaseStudyMatchModal
        open={caseStudyMatchOpen}
        onClose={() => setCaseStudyMatchOpen(false)}
        result={caseStudyMatchResult}
        loading={isMatchingCaseStudies}
        error={caseStudyMatchError}
      />
      <AlignOutlinePreviewModal
        open={alignPreviewOpen}
        loading={alignPreviewLoading}
        error={alignPreviewError}
        preview={alignPreview}
        applying={isAligningRfpOutline}
        onClose={handleCloseAlignPreview}
        onApply={() => void handleApplyAlignPreview()}
      />
      <PacketPlacePreviewModal
        open={placePreviewOpen}
        loading={placePreviewLoading}
        error={placePreviewError}
        preview={placePreview}
        applying={isPlacingPacketContent}
        onClose={handleClosePlacePreview}
        onApply={() => void handleApplyPlacePreview()}
        onFixSectionOrder={() => void handleAlignRfpOutline()}
      />
    </section>
  );
}
